"""NVIDIA NIM Chat model implementation.

This module provides a ChatNVIDIA class that integrates with NVIDIA's
NIM (NVIDIA Inference Microservices) API using the OpenAI-compatible endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat_model import ChatModel
from pydantic import BaseModel

from browser_agent.llm.base import BaseChatModel
from browser_agent.llm.messages import BaseMessage
from browser_agent.llm.openai.serializer import OpenAIMessageSerializer
from browser_agent.llm.views import ChatInvokeCompletion
from browser_agent.llm.exceptions import ModelProviderError

T = TypeVar('T', bound=BaseModel)


@dataclass
class ChatNVIDIA(BaseChatModel):
    """NVIDIA NIM chat model implementation.

    This class provides an interface to NVIDIA's NIM API using the OpenAI-compatible
    endpoint at https://integrate.api.nvidia.com/v1.

    Args:
        model: The model name to use (e.g., 'stepfun-ai/step-3.7-flash').
        base_url: The API endpoint URL. Defaults to NVIDIA's endpoint.
        api_key: API key for authentication. If not provided, will use
            the NVIDIA_API_KEY environment variable.
        temperature: Sampling temperature (0-2). Default: 0.7.
        max_tokens: Maximum tokens to generate. Default: 4096.
        top_p: Nucleus sampling parameter. Default: 0.9.
        **kwargs: Additional parameters passed to the OpenAI client.
    """

    # Model configuration
    model: str

    # Model params
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 4096

    # Client initialization parameters
    api_key: str | None = None
    base_url: str = 'https://integrate.api.nvidia.com/v1'
    timeout: float | None = 60.0
    max_retries: int = 2
    _client: AsyncOpenAI | None = field(default=None, init=False, repr=False)

    @property
    def provider(self) -> str:
        """Return the provider name."""
        return 'nvidia'

    def _get_client(self) -> AsyncOpenAI:
        """Get or create the OpenAI-compatible client."""
        if self._client is not None:
            return self._client

        api_key = self.api_key or os.getenv('NVIDIA_API_KEY')
        if not api_key:
            raise ValueError(
                'NVIDIA API key is required. Set NVIDIA_API_KEY environment variable '
                'or pass api_key to ChatNVIDIA.'
            )

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=self.timeout or 60.0,
            max_retries=self.max_retries,
        )
        return self._client

    def _convert_messages(self, messages: list[BaseMessage]) -> list[ChatCompletionMessageParam]:
        """Convert browser-use messages to OpenAI format."""
        return OpenAIMessageSerializer.serialize_messages(messages)

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        """Invoke the NVIDIA model with the given messages.

        Structured output is handled by injecting the JSON schema into the
        system prompt (schema-in-prompt) rather than using response_format
        json_schema, which NVIDIA NIM does not support on most models and
        returns 404 for. The model is asked to return valid JSON and the
        response is parsed with model_validate_json.
        """
        import json as _json

        client = self._get_client()
        openai_messages = self._convert_messages(messages)

        # Build request parameters — only params NIM supports.
        # Use 4096 tokens for structured output (schema + response need room).
        effective_max_tokens = kwargs.get('max_tokens', self.max_tokens)
        if output_format is not None:
            effective_max_tokens = max(effective_max_tokens, 4096)

        request_params: dict[str, Any] = {
            'model': self.model,
            'messages': openai_messages,
            'temperature': kwargs.get('temperature', self.temperature),
            'max_tokens': effective_max_tokens,
            'top_p': kwargs.get('top_p', self.top_p),
        }

        # Structured output: inject a compact schema hint into the system prompt.
        # Full json_schema dumps are too large; a minimal instruction works better.
        if output_format is not None:
            schema = output_format.model_json_schema()
            # Use compact (non-indented) JSON to save tokens
            schema_text = (
                '\n\nRespond with ONLY a valid JSON object matching this schema '
                '(no markdown, no explanation):\n'
                + _json.dumps(schema, separators=(',', ':'))
            )
            if openai_messages and openai_messages[0].get('role') == 'system':
                content = openai_messages[0].get('content', '')
                if isinstance(content, str):
                    openai_messages[0] = dict(openai_messages[0])
                    openai_messages[0]['content'] = content + schema_text
            else:
                openai_messages.insert(0, {'role': 'system', 'content': schema_text.lstrip()})
            request_params['messages'] = openai_messages

        # Retry up to 2 times on empty response (model occasionally returns blank
        # when the prompt is very large, e.g. vision screenshot + schema).
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                response = await client.chat.completions.create(**request_params)

                choice = response.choices[0] if response.choices else None
                if choice is None:
                    raise ModelProviderError(
                        message='NVIDIA API returned no choices',
                        status_code=500,
                        model=self.model,
                    )

                usage = self._get_usage(response)
                raw_content = (choice.message.content or '').strip()

                # Empty response — retry (model ran out of capacity or timed out)
                if output_format is not None and not raw_content:
                    if _attempt < 2:
                        continue
                    raise ModelProviderError(
                        message='Model returned empty response for structured output after 3 attempts',
                        status_code=500,
                        model=self.model,
                    )

                if output_format is not None:
                    # Strip optional markdown fences
                    text = raw_content
                    if text.startswith('```'):
                        lines = text.splitlines()
                        text = '\n'.join(
                            ln for ln in lines
                            if not ln.strip().startswith('```')
                        ).strip()
                    # Find the outermost JSON object/array
                    start = min(
                        (text.find(c) for c in ('{', '[') if text.find(c) != -1),
                        default=-1,
                    )
                    if start != -1:
                        text = text[start:]
                    try:
                        parsed = output_format.model_validate_json(text)
                        return ChatInvokeCompletion(
                            completion=parsed,
                            usage=usage,
                            stop_reason=choice.finish_reason,
                        )
                    except Exception as e:
                        if _attempt < 2:
                            last_error = e
                            continue  # retry on parse failure too
                        raise ModelProviderError(
                            message=f'Failed to parse structured output: {e}\nRaw: {raw_content[:300]}',
                            status_code=500,
                            model=self.model,
                        ) from e
                else:
                    return ChatInvokeCompletion(
                        completion=raw_content,
                        usage=usage,
                        stop_reason=choice.finish_reason,
                    )

            except ModelProviderError:
                raise
            except Exception as e:
                last_error = e
                if _attempt < 2:
                    continue
                raise ModelProviderError(
                    message=str(e),
                    status_code=getattr(e, 'status_code', 500),
                    model=self.model,
                ) from e

        raise ModelProviderError(
            message=f'All 3 attempts failed. Last error: {last_error}',
            status_code=500,
            model=self.model,
        )

    def _get_usage(self, response: Any) -> dict[str, int | None]:
        """Extract usage statistics from the response."""
        if hasattr(response, 'usage') and response.usage:
            return {
                'total_tokens': response.usage.total_tokens,
                'prompt_tokens': response.usage.prompt_tokens,
                'completion_tokens': response.usage.completion_tokens,
                'prompt_cached_tokens': getattr(response.usage, 'prompt_cached_tokens', None),
                'prompt_cache_creation_tokens': getattr(response.usage, 'prompt_cache_creation_tokens', None),
                'prompt_image_tokens': getattr(response.usage, 'prompt_image_tokens', None),
            }
        return {
            'total_tokens': 0,
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'prompt_cached_tokens': None,
            'prompt_cache_creation_tokens': None,
            'prompt_image_tokens': None,
        }