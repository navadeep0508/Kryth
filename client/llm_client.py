import math
import asyncio
import random
from openai import AsyncOpenAI, RateLimitError, APIConnectionError, APIError, APIStatusError
from typing import Any, AsyncGenerator
from client.response import TextDelta, TokenUsage, StreamEvent, StreamEventType, ToolCallDelta, ToolCall, parse_tool_call_arguments
from config.config import Config


def _token_usage(usage: Any) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        cached_tokens=(
            getattr(usage.prompt_tokens_details, "cached_tokens", 0)
            if usage.prompt_tokens_details else 0
        ),
        reasoning_tokens=(
            getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0
            if getattr(usage, "completion_tokens_details", None) else 0
        ),
    )


def _reasoning_text(payload: Any) -> str | None:
    """Reasoning arrives as `reasoning` on OpenRouter and `reasoning_content`
    on providers that follow the DeepSeek shape. Neither field is in the
    OpenAI schema, so both land in the model's extras."""

    for field in ("reasoning", "reasoning_content"):
        value = getattr(payload, field, None)
        if isinstance(value, str) and value:
            return value
    return None


def _classify_rate_limit_error(e: Any) -> str:
    """Classify a rate limit error into a user-facing category.

    Returns one of: 'temporary', 'quota_exhausted', 'billing', 'model_unavailable',
    'auth_failure', 'unknown'.
    """
    # Try to read HTTP status code and body from the error.
    status_code = getattr(e, "status_code", None) or getattr(e, "status", None)
    body = ""
    try:
        body = str(e)
    except Exception:
        pass

    body_lower = body.lower()

    # Auth failures.
    if status_code == 401 or "authentication" in body_lower or "invalid api key" in body_lower:
        return "auth_failure"

    # Model unavailable.
    if status_code == 404 or "model" in body_lower and ("not found" in body_lower or "unavailable" in body_lower):
        return "model_unavailable"

    # Billing / credit issues.
    if status_code == 402 or "credit" in body_lower or "billing" in body_lower or "insufficient" in body_lower:
        return "billing"

    # Quota exhausted (daily/monthly limit).
    if status_code == 429:
        if "quota" in body_lower or "limit" in body_lower or "exhausted" in body_lower or "daily" in body_lower:
            return "quota_exhausted"
        return "temporary"

    # Generic 429.
    if status_code == 429:
        return "temporary"

    return "unknown"


def _retry_after_seconds(e: Any, attempt: int) -> float:
    """Determine how long to wait before retrying.

    Parses Retry-After header when available, otherwise uses exponential
    backoff with jitter.
    """
    # Try Retry-After header.
    headers = getattr(e, "headers", None) or getattr(e, "response", None)
    if headers:
        try:
            retry_after = headers.get("Retry-After") or headers.get("retry-after")
            if retry_after:
                return float(retry_after)
        except (AttributeError, TypeError, ValueError):
            pass

    # Exponential backoff with jitter: base * 2^attempt + random jitter.
    base = 2.0
    backoff = base * (2 ** attempt)
    jitter = random.uniform(0, 1.0)
    return backoff + jitter


def _rate_limit_message(category: str, retry_after: float | None = None) -> str:
    """Build a user-facing message for a rate limit error."""
    if category == "auth_failure":
        return "Authentication failure: invalid API key or credentials."
    if category == "billing":
        return "Billing or credit issue: your provider account may have insufficient credits."
    if category == "model_unavailable":
        return "Model unavailable: the requested model is not found or not accessible."
    if category == "quota_exhausted":
        if retry_after:
            return f"Daily quota exhausted. Retry after {retry_after:.0f}s."
        return "Daily quota exhausted: your provider quota has been used up."
    if category == "temporary":
        if retry_after:
            return f"Temporary rate limit. Retrying in {retry_after:.0f}s..."
        return "Temporary rate limit exceeded. Retrying with backoff..."
    return f"Rate limit error: unknown category."


class LLMClient:
    def __init__(self, config: Config) -> None:
        self._client: AsyncOpenAI | None = None
        self._max_retries: int = 5
        self.config = config

    def get_client(self) -> AsyncOpenAI:
        if self._client is None:
            provider = None
            if self.config.providers.active_name:
                provider = self.config.providers.get(self.config.providers.active_name)

            default_headers: dict[str, str] = {}
            if provider and provider.extra_headers:
                default_headers.update(provider.extra_headers)

            self._client = AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                default_headers=default_headers or None,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    def _build_tools(
            self,
            tools: list[dict[str, Any]]
    ):
        return [
            {
                'type': 'function',
                'function': {
                    'name': tool['name'],
                    'description': tool.get('description', ""),
                    'parameters': tool.get('parameters', {
                        'type': 'object',
                        'properties': {}
                    })
                },
            }
            for tool in tools
        ]

    async def chat_completion(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            stream: bool = True
    ) -> AsyncGenerator[StreamEvent, None]:

        client = self.get_client()

        kwargs = {
            "model": self.config.model_name,
            "messages": messages,
            "stream": stream,
        }

        # Without this the stream carries no usage chunk at all, and the
        # context gauge on the prompt rule has nothing to show.
        if stream:
            kwargs["stream_options"] = {"include_usage": True}

        if tools:
            kwargs['tools'] = self._build_tools(tools)
            kwargs['tool_choice'] = 'auto'

        # OpenRouter's own extension, so it rides along in extra_body. Models
        # that cannot reason ignore it.
        reasoning = self.config.reasoning.to_request_payload()
        if reasoning:
            kwargs['extra_body'] = {'reasoning': reasoning}

        for attempt in range(self._max_retries + 1):
            emitted = False
            try:
                if stream:
                    async for event in self._stream_response(client, kwargs):
                        emitted = True
                        yield event
                else:
                    event = await self._non_stream_response(client, kwargs)
                    yield event
                return
            except RateLimitError as e:
                category = _classify_rate_limit_error(e)
                retry_after = _retry_after_seconds(e, attempt)

                # Non-retryable categories: fail immediately.
                if category in {"auth_failure", "billing", "model_unavailable"}:
                    yield StreamEvent(
                        type=StreamEventType.ERROR,
                        error=_rate_limit_message(category, retry_after),
                    )
                    return

                # Quota exhausted: fail immediately (not temporary).
                if category == "quota_exhausted":
                    yield StreamEvent(
                        type=StreamEventType.ERROR,
                        error=_rate_limit_message(category, retry_after),
                    )
                    return

                # Temporary rate limit: retry silently with countdown.
                # Don't yield to the caller — the agent loop terminates on
                # ERROR events, which would abandon the retry. Sleep and
                # continue the retry loop instead.
                if attempt < self._max_retries:
                    await asyncio.sleep(retry_after)
                    continue
                else:
                    yield StreamEvent(
                        type=StreamEventType.ERROR,
                        error=f"Rate limit exceeded after {self._max_retries} retries: {e}",
                    )
                    return
            except APIConnectionError as e:
                if not emitted and attempt < self._max_retries:
                    wait_time = math.pow(2, attempt)
                    await asyncio.sleep(wait_time)
                else:
                    yield StreamEvent(
                        type=StreamEventType.ERROR,
                        error=f"Connection Error exceeded: {e}",
                    )
                    return
            # No retries here because there is no point in retrying a persistent API error.
            except APIError as e:
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    error=f"API Error: {e}",
                )
                return

    async def _stream_response(self, client: AsyncOpenAI, kwargs: dict[str, Any]) -> AsyncGenerator[StreamEvent, None]:
        response = await client.chat.completions.create(**kwargs)

        finish_reason: str | None = None
        usage: TokenUsage | None = None
        tool_calls: dict[int, dict[str, Any]] = {}

        async for chunk in response:
            if hasattr(chunk, "usage") and chunk.usage:
                usage = _token_usage(chunk.usage)

            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if choice.finish_reason:
                finish_reason = choice.finish_reason

            reasoning = _reasoning_text(delta)
            if reasoning:
                yield StreamEvent(
                    type=StreamEventType.REASONING_DELTA,
                    reasoning_delta=reasoning,
                )

            if delta.content:
                yield StreamEvent(
                    type=StreamEventType.TEXT_DELTA,
                    text_delta=TextDelta(delta.content),
                )

            if delta.tool_calls:
                for tool_call_delta in delta.tool_calls:
                    index = tool_call_delta.index

                    if index not in tool_calls:
                        tool_calls[index] = {
                            'id': tool_call_delta.id or "",
                            'name': '',
                            'arguments': '',
                        }

                    # NOTE: this used to be nested inside "if index not in tool_calls",
                    # which meant argument fragments after the first chunk for a given
                    # index were silently dropped. Now runs on every chunk.
                    if tool_call_delta.function:
                        if tool_call_delta.function.name:
                            tool_calls[index]['name'] = tool_call_delta.function.name
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL_START,
                                tool_call_delta=ToolCallDelta(
                                    call_id=tool_calls[index]['id'],
                                    name=tool_call_delta.function.name,
                                )
                            )

                        if tool_call_delta.function.arguments:
                            tool_calls[index]['arguments'] += tool_call_delta.function.arguments
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL_DELTA,
                                tool_call_delta=ToolCallDelta(
                                    call_id=tool_calls[index]['id'],
                                    name=tool_calls[index]['name'],
                                    arguments_delta=tool_call_delta.function.arguments,
                                )
                            )

        for index, toolcall in tool_calls.items():
            yield StreamEvent(
                type=StreamEventType.TOOL_CALL_COMPLETE,
                tool_call=ToolCall(
                    call_id=toolcall['id'],
                    name=toolcall['name'],
                    arguments=parse_tool_call_arguments(toolcall['arguments']),
                )
            )

        yield StreamEvent(
            type=StreamEventType.MESSAGE_COMPLETE,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def _non_stream_response(self, client: AsyncOpenAI, kwargs: dict[str, Any]) -> StreamEvent:
        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]  # only interested in first index, first message
        message = choice.message

        text_delta = None
        if message.content:
            text_delta = TextDelta(content=message.content)

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for toolcall in message.tool_calls:
                tool_calls.append(ToolCall(
                    call_id=toolcall.id,
                    name=toolcall.function.name,
                    arguments=parse_tool_call_arguments(
                        toolcall.function.arguments
                    )
                ))

        usage = None
        if response.usage:
            usage = _token_usage(response.usage)

        return StreamEvent(
            type=StreamEventType.MESSAGE_COMPLETE,
            text_delta=text_delta,
            reasoning_delta=_reasoning_text(message),
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
        )