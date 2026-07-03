"""NVIDIA NIM Chat model implementation.

This module provides a ChatNVIDIA class that integrates with NVIDIA's
NIM (NVIDIA Inference Microservices) API using the OpenAI-compatible endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, cast
from uuid import uuid4

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

        Args:
            messages: List of messages to send to the model.
            output_format: Optional Pydantic model for structured output.
            **kwargs: Additional parameters (temperature, max_tokens, etc.).

        Returns:
            ChatInvokeCompletion containing the response and usage statistics.
        """
        client = self._get_client()
        openai_messages = self._convert_messages(messages)

        # Build request parameters
        request_params: dict[str, Any] = {
            'model': self.model,
            'messages': openai_messages,
        }

        # Add optional parameters
        request_params['temperature'] = kwargs.get('temperature', self.temperature)
        request_params['max_tokens'] = kwargs.get('max_tokens', self.max_tokens)
        request_params['top_p'] = kwargs.get('top_p', self.top_p)

        # Handle structured output if output_format is provided
        if output_format is not None:
            # NVIDIA NIM supports JSON schema via response_format
            # Generate JSON schema from the Pydantic model
            schema = output_format.model_json_schema()

            request_params['response_format'] = {
                'type': 'json_schema',
                'json_schema': {
                    'name': f'response_{uuid4().hex[:8]}',
                    'schema': schema,
                    'strict': True,
                }
            }

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

            if output_format is not None:
                # Parse structured output
                if choice.message.content is None:
                    raise ModelProviderError(
                        message='Failed to parse structured output from model response',
                        status_code=500,
                        model=self.model,
                    )

                try:
                    parsed = output_format.model_validate_json(choice.message.content)
                    return ChatInvokeCompletion(
                        completion=parsed,
                        usage=usage,
                        stop_reason=choice.finish_reason,
                    )
                except Exception as e:
                    raise ModelProviderError(
                        message=f'Failed to parse structured output: {e}',
                        status_code=500,
                        model=self.model,
                    ) from e
            else:
                return ChatInvokeCompletion(
                    completion=choice.message.content or '',
                    usage=usage,
                    stop_reason=choice.finish_reason,
                )

        except Exception as e:
            # Re-raise as ModelProviderError for consistent error handling
            if isinstance(e, ModelProviderError):
                raise
            raise ModelProviderError(
                message=str(e),
                status_code=getattr(e, 'status_code', 500),
                model=self.model,
            ) from e

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