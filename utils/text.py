import tiktoken
import json
import math
from dataclasses import dataclass
from typing import Any

def get_tokenizer(model: str):
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str, model: str = "") -> int:
    tokenizer = get_tokenizer(model)

    if tokenizer:
        return len(tokenizer.encode(text))

    return estimate_tokens(text)

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) # on avg 1 token is ~ 4 characters


@dataclass(frozen=True)
class RequestSizeEstimate:
    estimated_tokens: int
    serialized_bytes: int
    estimator: str


def estimate_serialized_request(
    payload: dict[str, Any],
    *,
    model: str = "",
    tokenizer_encoding: str | None = None,
) -> RequestSizeEstimate:
    """Estimate the complete API body, using a known tokenizer or a byte bound."""
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    serialized_bytes = len(serialized.encode("utf-8"))
    try:
        if tokenizer_encoding:
            tokenizer = tiktoken.get_encoding(tokenizer_encoding)
            estimator = f"tiktoken:{tokenizer_encoding}"
        elif model:
            tokenizer = tiktoken.encoding_for_model(model)
            estimator = f"tiktoken:model:{model}"
        else:
            tokenizer = None
            estimator = "utf8-byte-upper-bound"
    except (KeyError, ValueError):
        tokenizer = None
        estimator = "utf8-byte-upper-bound"

    messages = payload.get("messages", [])
    tools = payload.get("tools", [])
    if tokenizer is None:
        estimated = serialized_bytes + len(messages) * 8 + len(tools) * 16 + 64
    else:
        token_count = len(tokenizer.encode(serialized))
        framing = len(messages) * 8 + len(tools) * 16 + 64
        estimated = math.ceil(token_count * 1.10) + framing
    return RequestSizeEstimate(estimated, serialized_bytes, estimator)


def estimate_request_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    model: str = "",
    tokenizer_encoding: str | None = None,
) -> int:
    payload: dict[str, Any] = {"messages": messages}
    if tools:
        payload["tools"] = tools
    return estimate_serialized_request(
        payload, model=model, tokenizer_encoding=tokenizer_encoding
    ).estimated_tokens

def truncate_text(
        text: str, 
        model: str, 
        max_tokens: int, 
        suffix: str = "\n...[truncated]",
        preserve_lines: bool = True,
    ):

    current_tokens = count_tokens(text, model)

    if current_tokens <= max_tokens:
        return text
    
    suffix_tokens = count_tokens(suffix, model)
    target_tokens = max_tokens - suffix_tokens

    if target_tokens <= 0:
        return suffix.strip()

    if preserve_lines:
        return _truncate_by_lines(text, target_tokens, suffix, model)
    else:
        return _truncate_by_chars(text, target_tokens, suffix, model)

def _truncate_by_lines(text: str, target_tokens: int, suffix: str, model: str) -> str:
    lines = text.split("\n")
    result_lines: list[str] = []
    current_tokens = 0

    for line in lines:
        line_tokens = count_tokens(line + "\n", model)
        if current_tokens + line_tokens > target_tokens:
            break
        result_lines.append(line)
        current_tokens += line_tokens

    if not result_lines:
        # Fall back to character truncation if no complete lines fit
        return _truncate_by_chars(text, target_tokens, suffix, model)

    return "\n".join(result_lines) + suffix


def _truncate_by_chars(text: str, target_tokens: int, suffix: str, model: str) -> str:
    # Binary search for the right length
    low, high = 0, len(text)

    while low < high:
        mid = (low + high + 1) // 2
        if count_tokens(text[:mid], model) <= target_tokens:
            low = mid
        else:
            high = mid - 1

    return text[:low] + suffix
