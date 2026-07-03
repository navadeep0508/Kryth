from openai import (
    OpenAI,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,
    BadRequestError,
)
from dotenv import load_dotenv
import httpx
import json
import logging
import os
import re
import time
import types

from agent import ui
from agent.env import getenv

load_dotenv()

_logger = logging.getLogger(__name__)

# Provider endpoint + models are env-configurable so users can swap
# providers (OpenAI, NVIDIA, freemodel.dev, OpenRouter, local llama.cpp)
# without editing this file.
BASE_URL = getenv("KRYTH_BASE_URL", "https://api.openai.com/v1")

# Each tier is env-overridable. Set ``KRYTH_MAIN_MODEL`` (etc.) in
# .env if your provider exposes different names.
MAIN_MODEL = getenv("KRYTH_MAIN_MODEL", "gpt-4o-mini")
PLANNER_MODEL = getenv("KRYTH_PLANNER_MODEL", "gpt-4o-mini")
SUMMARIZER_MODEL = getenv("KRYTH_SUMMARIZER_MODEL", "gpt-4o-mini")

def reload_config() -> None:
    """Refresh module-level config from environment variables.

    Call this after `/config` saves new values to ``~/.kryth/config.json``
    and calls ``apply_to_env()``.  The module-level constants
    ``MAIN_MODEL``, ``PLANNER_MODEL``, ``SUMMARIZER_MODEL``, and
    ``BASE_URL`` are re-read from ``os.environ`` so that REPL commands
    like ``/diag``, ``/models``, and ``/status`` show the live values
    without a restart. If the API key or base URL changed, the cached
    ``_client`` is also discarded so the next call picks up the new
    credentials.
    """
    global _client, BASE_URL, MAIN_MODEL, PLANNER_MODEL, SUMMARIZER_MODEL

    # Snapshot old values to detect changes
    old_key = os.getenv("OPENAI_API_KEY", "").strip() or "not-configured"
    old_url = getenv("KRYTH_BASE_URL", BASE_URL)

    # Refresh from env
    MAIN_MODEL       = getenv("KRYTH_MAIN_MODEL",       "gpt-4o-mini")
    PLANNER_MODEL    = getenv("KRYTH_PLANNER_MODEL",    "gpt-4o-mini")
    SUMMARIZER_MODEL = getenv("KRYTH_SUMMARIZER_MODEL", "gpt-4o-mini")
    BASE_URL         = getenv("KRYTH_BASE_URL",         "https://api.openai.com/v1")

    # If key or endpoint changed, drop the cached client so
    # ``_get_client()`` creates a fresh one on the next call.
    new_key = os.getenv("OPENAI_API_KEY", "").strip() or "not-configured"
    new_url = getenv("KRYTH_BASE_URL", BASE_URL)
    if new_key != old_key or new_url.rstrip("/") != old_url.rstrip("/"):
        _client = None


# Transient errors worth retrying. Auth / bad-request / not-found are
# terminal — retrying won't help.
RETRYABLE = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)
RETRY_DELAYS = (0.5, 1.5, 4.0, 8.0, 16.0, 30.0)


def _make_client() -> OpenAI:
    """Create the OpenAI client, reading env vars at call time."""
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key or key == "not-set":
        key = "not-configured"
    return OpenAI(
        base_url=getenv("KRYTH_BASE_URL", BASE_URL),
        api_key=key,
        timeout=httpx.Timeout(connect=30.0, read=float(os.getenv("KRYTH_READ_TIMEOUT", "180")), write=60.0, pool=30.0),
    )


# Module-level client — created lazily on first use via _get_client().
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Return the shared client, (re)creating it if the key or base_url
    changed since the last call (e.g. after /config set api_key ...).

    When model_config is available, delegates to the router for the
    "main" role. Falls back to the original env-var path if not.
    """
    global _client, BASE_URL, MAIN_MODEL, PLANNER_MODEL, SUMMARIZER_MODEL

    # Try model_config router first (non-blocking — fails silently)
    try:
        from agent.model_config.router import get_client as _mc_get_client
        return _mc_get_client("main")
    except Exception as _e:
        _logger.debug("model_config.router unavailable, using env-var fallback: %s", _e)

    current_key = os.getenv("OPENAI_API_KEY", "").strip() or "not-configured"
    current_url = getenv("KRYTH_BASE_URL", BASE_URL)
    # Refresh module-level model names in case /config changed them
    MAIN_MODEL       = getenv("KRYTH_MAIN_MODEL",       MAIN_MODEL)
    PLANNER_MODEL    = getenv("KRYTH_PLANNER_MODEL",    PLANNER_MODEL)
    SUMMARIZER_MODEL = getenv("KRYTH_SUMMARIZER_MODEL", SUMMARIZER_MODEL)
    BASE_URL         = current_url
    if _client is None:
        _client = _make_client()
    else:
        try:
            existing_key = _client.api_key  # type: ignore[attr-defined]
            existing_url = str(_client.base_url).rstrip("/")
        except Exception:
            existing_key, existing_url = "", ""
        if existing_key != current_key or existing_url != current_url.rstrip("/"):
            _client = _make_client()
    return _client


# Keep a `client` name for any code that references it directly
# (health_check, ask_planner, etc.) — it now goes through _get_client().
class _ClientProxy:
    """Thin proxy so ``client.chat.completions.create(...)`` calls
    always use the up-to-date client without changing every call site."""
    def __getattr__(self, name: str):
        return getattr(_get_client(), name)


client = _ClientProxy()  # type: ignore[assignment]


_API_ERROR_HINTS = {
    AuthenticationError: (
        "401 — OPENAI_API_KEY in .env was rejected by the gateway. "
        "Verify the key is present and valid for the provider configured "
        "in KRYTH_BASE_URL. Make sure .env is in the directory you run "
        "main.py from."
    ),
    PermissionDeniedError: (
        "403 — your key is valid but isn't authorized for this model "
        "(gated model, exhausted quota, or wrong plan). Check the "
        "provider's dashboard for quota and model access."
    ),
    NotFoundError: (
        "404 - endpoint or model not found. Confirm "
        "KRYTH_MAIN_MODEL / KRYTH_PLANNER_MODEL / "
        "KRYTH_SUMMARIZER_MODEL in .env match what your provider "
        "exposes, and that KRYTH_BASE_URL points at the right path "
        "(some providers expose /v1, some don't)."
    ),
    BadRequestError: (
        "400 — request rejected. Possible causes: too many input tokens, "
        "tool schema unsupported by this model, or max_tokens above the "
        "model's per-request cap (drop max_tokens in agent/llm.py)."
    ),
}


def _format_api_error(e: APIStatusError) -> str:
    code = getattr(e, "status_code", "?")
    name = type(e).__name__
    detail = ""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("message") or body.get("error") or ""
    elif isinstance(body, str):
        detail = body
    hint = _API_ERROR_HINTS.get(type(e), "")
    parts = [f"[{name} {code}]"]
    if detail:
        parts.append(str(detail)[:200])
    if hint:
        parts.append("-> " + hint)
    return " ".join(parts)


def _retry(call_label, fn, *args, **kwargs):
    """Run ``fn`` with bounded exponential backoff on transient errors.

    The first attempt runs immediately. Subsequent attempts emit a UI
    event before sleeping so the user sees the wait instead of a hang,
    and the sleep is broken into ticks so the spinner stays alive.

    KeyboardInterrupt and non-retryable API errors are re-raised
    immediately.
    """
    last_exc: BaseException | None = None
    total = len(RETRY_DELAYS) + 1

    for attempt in range(1, total + 1):
        try:
            return fn(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except RETRYABLE as e:
            last_exc = e
            if attempt >= total:
                break
            delay = RETRY_DELAYS[attempt - 1]
            ui.llm_retry(
                label=call_label,
                attempt=attempt,
                total=total,
                reason=type(e).__name__,
                delay=delay,
            )
            _interruptible_sleep(delay)

    raise last_exc  # type: ignore[misc]


def _interruptible_sleep(seconds: float) -> None:
    """Sleep in 100ms ticks so Ctrl+C cancels promptly. Plain
    ``time.sleep`` blocks the signal until the call returns on some
    platforms."""
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def health_check() -> dict:
    """Ping every configured model with a one-token request.

    Returns ``{model_name: "ok" | error_message}``. Used by the /diag
    REPL command to isolate environment problems (missing key, missing
    model access, exhausted credits) from code bugs.
    """
    results: dict[str, str] = {}
    for label, model in (
        ("main", MAIN_MODEL),
        ("planner", PLANNER_MODEL),
        ("summarizer", SUMMARIZER_MODEL),
    ):
        try:
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
            )
            results[f"{label} ({model})"] = "ok"
        except KeyboardInterrupt:
            raise
        except APIStatusError as e:
            results[f"{label} ({model})"] = _format_api_error(e)
        except Exception as e:
            results[f"{label} ({model})"] = f"{type(e).__name__}: {e}"
    return results


def _adapter_tool_deltas_to_openai(norm_chunks: list) -> list:
    """Convert adapter NormalizedChunk tool calls into OpenAI delta shape.

    The ModelAdapter emits a COMPLETE tool call per chunk (sanitized name +
    parsed args dict). We re-shape it into the streaming-delta form that
    ``_merge_tool_call_delta`` consumes, so the accumulation path is identical
    to the native code. Indices are assigned sequentially per emission.
    """
    out = []
    for i, nc in enumerate(norm_chunks):
        name = getattr(nc, "tool_name", "") or ""
        args = getattr(nc, "tool_args", {}) or {}
        call_id = getattr(nc, "tool_call_id", "") or None
        try:
            args_str = json.dumps(args) if isinstance(args, (dict, list)) else str(args)
        except Exception:
            args_str = "{}"
        out.append(types.SimpleNamespace(
            index=i,
            id=call_id,
            function=types.SimpleNamespace(name=name, arguments=args_str),
        ))
    return out


def _merge_tool_call_delta(accum: dict, delta_call) -> None:
    idx = delta_call.index
    slot = accum.setdefault(
        idx,
        {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
    )
    if delta_call.id:
        slot["id"] = delta_call.id
    fn = getattr(delta_call, "function", None)
    if fn is not None:
        if getattr(fn, "name", None):
            slot["function"]["name"] += fn.name
        if getattr(fn, "arguments", None):
            slot["function"]["arguments"] += fn.arguments


def _classify_400(e: APIStatusError) -> str:
    """Return 'max_tokens' | 'context_overflow' | 'tools_unsupported' |
    'content_tool_calls' | 'payload_too_large' | 'other'."""
    body = (str(getattr(e, "body", "") or "") + " " + str(e)).lower()
    # Provider per-request / per-minute token CAPACITY exceeded (e.g. Groq
    # free-tier TPM, or a 413 "request too large"). NOT time-recoverable when
    # driven by a fixed payload (the tool schemas), so it must fail FAST with
    # actionable guidance instead of slow-retrying for a minute.
    if (
        "request too large" in body
        or "reduce your message size" in body
        or ("tokens per minute" in body and "limit" in body)
        or ("tpm" in body and "limit" in body)
    ):
        return "payload_too_large"
    if ("max_tokens" in body or "max_completion_tokens" in body) and (
        "too large" in body or "is larger than" in body or "exceeds" in body
    ):
        return "max_tokens"
    if any(p in body for p in (
        "context_length_exceeded", "context length", "too long",
        "resulted in", "reduce the length", "input is too long",
        "prompt is too long", "tokens in the messages",
    )):
        return "context_overflow"
    if any(p in body for p in (
        "invalid tools", "tools structure", "none is not", "tool_choice",
        "tools not supported", "does not support tool", "does not support function",
        "function calling", "function call is not supported",
    )):
        return "tools_unsupported"
    if "content" in body and "tool_calls" in body and (
        "both" in body or "either" in body or "not both" in body
    ):
        return "content_tool_calls"
    return "other"


def _extract_token_limit(e: APIStatusError) -> int | None:
    """Parse the model's ACTUAL token ceiling from a 400 error body.

    The error text often reads:
      'max_tokens is too large: 32768. This model's maximum context length is 8192.'
    We must return 8192 (the model limit), NOT 32768 (our request).
    Use only specific anchor phrases so we never misread the requested value.
    """
    body = str(getattr(e, "body", "") or "") + " " + str(e)
    # Ordered from most to least specific — stop at first confident match.
    for pat in (
        r"maximum context length is (\d+)",
        r"model(?:'s)? maximum.*?(\d+)",
        r"maximum.*?allowed.*?(\d+)",
        r"supports up to (\d+)",
        r"model supports (\d+)",
    ):
        m = re.search(pat, body, re.IGNORECASE)
        if m:
            v = int(m.group(1))
            if 256 <= v <= 1_000_000:
                return v
    return None  # fall back to halving


# Per-model discovered output-token ceiling: avoids retrying every turn.
_model_max_tokens_cache: dict[str, int] = {}

# Models that rejected structured tool schemas — always use text-mode for these.
_model_text_tool_cache: set[str] = set()

# ── Runtime v2 ModelAdapter normalization seam (feature-flagged, default OFF) ──
# When KRYTH_ADAPTER_STREAM is set, each raw streaming chunk is parsed by the
# provider-agnostic ModelAdapter instead of the inline manual delta parsing.
# The surrounding loop (TTFT, degenerate detection, max-token retry, schema
# fallback, accumulators) is UNCHANGED — only the per-chunk parse is swapped.
_adapter_cache: dict[str, object] = {}


def _adapter_stream_enabled() -> bool:
    try:
        from agent.env import getenv_bool
        return getenv_bool("KRYTH_ADAPTER_STREAM")
    except Exception:
        return False


def _get_adapter(model_name: str):
    """Lazily build + cache a ModelAdapter for the active model."""
    adp = _adapter_cache.get(model_name)
    if adp is None:
        from agent.model.model_adapter import ModelAdapter
        adp = ModelAdapter.for_model(model_name, BASE_URL)
        _adapter_cache[model_name] = adp
    return adp

# Known output-token ceilings for models that don't report their limit
# clearly in 400 error bodies (e.g. NVIDIA NIM nemotron). Substring-matched
# against the model name (lowercased) — most specific match wins.
_KNOWN_OUTPUT_LIMITS: list[tuple[str, int]] = [
    ("mistral-nemotron", 4096),
    ("nemotron", 4096),
    ("llama-3.1-nemotron", 4096),
    ("llama-3.3-nemotron", 4096),
    ("mistral-7b", 4096),
    ("mistral-small", 4096),
    ("mixtral-8x7b", 4096),
]


def _known_output_limit(model: str) -> int | None:
    """Return a hardcoded output-token ceiling for known constrained models."""
    low = model.lower()
    for substr, limit in _KNOWN_OUTPUT_LIMITS:
        if substr in low:
            return limit
    return None


def _api_error_response(label: str, err: APIStatusError) -> dict:
    """Build a 'graceful exit' response when the gateway rejects us.

    The agent loop reads ``interrupted=True`` and shuts down the turn
    cleanly instead of dumping a traceback. The error is surfaced
    through the event bus so the renderer paints it in context.
    For auth / permission / not-found errors we also offer to open
    the interactive config editor so the user can fix the issue inline.
    """
    msg = _format_api_error(err)
    ui.llm_error(label=label, message=msg, hint="Run /diag to ping each model individually.")

    # Offer inline config fix for actionable error types
    error_type = type(err).__name__
    if error_type in ("AuthenticationError", "PermissionDeniedError", "NotFoundError"):
        try:
            from kryth.config import prompt_config_fix
            prompt_config_fix(error_type)
        except ImportError:
            pass  # not running inside kryth — skip silently

    return {
        "content": None,
        "tool_calls": None,
        "finish_reason": "api_error",
        "usage": None,
        "interrupted": True,
    }


def _is_nvidia_endpoint() -> bool:
    return "integrate.api.nvidia.com" in BASE_URL.lower()


def _tool_mode() -> str:
    """Return schema/text tool delivery mode for the active provider.

    Native function calling ("schema") is the default for ALL providers — it
    is the robust path and avoids text/XML/Harmony parsing entirely. Models
    that genuinely cannot accept a tool schema are detected automatically: the
    provider returns a `tools_unsupported` 400 on the first call, the model is
    added to ``_model_text_tool_cache``, and the request is replayed in text
    mode (see the retry loop in ``ask_llm_stream``). Set KRYTH_TOOL_MODE=text
    to force the legacy text protocol.
    """
    mode = getenv("KRYTH_TOOL_MODE", "auto").strip().lower()
    if mode in {"schema", "text"}:
        return mode
    return "schema"


def _status_detail(err: APIStatusError) -> str:
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        value = body.get("detail") or body.get("message") or body.get("error") or ""
        return str(value)
    if isinstance(body, str):
        return body
    response = getattr(err, "response", None)
    if response is not None:
        try:
            return str(response.text or "")
        except Exception:
            return ""
    return ""


def _should_retry_chat_compat(err: APIStatusError) -> bool:
    """Some OpenAI-compatible gateways reject optional OpenAI fields with
    misleading 404/400 responses. Retry once with the minimal chat shape.
    """
    code = getattr(err, "status_code", None)
    if code == 400:
        # Don't compat-retry errors we handle explicitly in the outer loop
        body = (str(getattr(err, "body", "") or "") + " " + str(err)).lower()
        if "content" in body and "tool_calls" in body and (
            "both" in body or "either" in body or "not both" in body
        ):
            return False
        return True
    if code == 404 and _is_nvidia_endpoint():
        detail = _status_detail(err).lower()
        return "page not found" in detail or "not found" in detail
    return False


def _tool_text_fallback(tools) -> str:
    if not tools:
        return ""
    lines = [
        "TOOL CALL FORMAT — MANDATORY:",
        "This model does not support structured function-calling.",
        "You MUST use the following XML format for EVERY tool call.",
        "DO NOT use markdown code fences (``` or ```bash). DO NOT use [TOOL_CALLS] format.",
        "DO NOT describe what you are doing. Just output the tool call block directly:",
        "",
        '<tool_call>{"name":"tool_name","arguments":{"arg":"value"}}</tool_call>',
        "",
        "One tool call per response. After the tool result arrives, call the next tool.",
        "NEVER output ```bash, ```python, or any code block — use run_command tool instead.",
        "",
        "Available tools:",
    ]
    for spec in tools:
        fn = spec.get("function", {}) if isinstance(spec, dict) else {}
        name = fn.get("name", "")
        if not name:
            continue
        params = fn.get("parameters", {}) or {}
        props = params.get("properties", {}) or {}
        required = set(params.get("required", []) or [])
        args = []
        for key, prop in props.items():
            marker = "*" if key in required else ""
            args.append(f"{key}{marker}:{prop.get('type', 'any')}")
        desc = (fn.get("description", "") or "").strip().split("\n", 1)[0]
        signature = f"{name}({', '.join(args)})"
        lines.append(f"- {signature}: {desc}" if desc else f"- {signature}")
    return "\n".join(lines)


def _messages_with_tool_text_fallback(messages, tools) -> list:
    hint = _tool_text_fallback(tools)
    if not hint:
        return messages
    out = [dict(m) for m in messages]
    if out and out[0].get("role") == "system":
        out[0]["content"] = str(out[0].get("content") or "") + "\n\n" + hint
    else:
        out.insert(0, {"role": "system", "content": hint})
    return out


def _sanitize_assistant_messages(messages: list) -> list:
    """Enforce: assistant messages must have content XOR tool_calls, never both.

    Some providers (mistral-nemotron) reject any assistant message that carries
    both fields, even when content is an empty string.
    """
    out = []
    for m in messages:
        if m.get("role") == "assistant":
            has_tools = bool(m.get("tool_calls"))
            has_content = "content" in m
            if has_tools and has_content:
                m = {k: v for k, v in m.items() if k != "content"}
            elif not has_tools and not has_content:
                m = dict(m)
                m["content"] = ""
        out.append(m)
    return out


def _sanitize_tool_call_ids(messages: list) -> list:
    """Ensure every tool_call id is 9 alphanumeric chars."""
    out = []
    for m in messages:
        m2 = dict(m)
        if m2.get("tool_calls"):
            fixed_calls = []
            for i, tc in enumerate(m2["tool_calls"]):
                tc2 = dict(tc)
                raw_id = tc2.get("id") or ""
                if len(raw_id) != 9 or not raw_id.isalnum():
                    prefix = raw_id[:3].replace("_", "x").replace("-", "x") or "fix"
                    tc2["id"] = _make_tool_call_id(prefix, i)
                fixed_calls.append(tc2)
            m2["tool_calls"] = fixed_calls
        if m2.get("role") == "tool":
            raw_id = m2.get("tool_call_id") or ""
            if len(raw_id) != 9 or not raw_id.isalnum():
                prefix = raw_id[:3].replace("_", "x").replace("-", "x") or "fix"
                m2["tool_call_id"] = _make_tool_call_id(prefix, 0)
        out.append(m2)
    return out


def _sanitize_messages_for_provider(messages: list) -> list:
    """Apply all provider-compatibility message fixes in one pass."""
    msgs = _sanitize_tool_call_ids(messages)
    msgs = _sanitize_assistant_messages(msgs)
    # Stable prefix ordering — ensures system messages are grouped at the top
    # so OpenAI automatic caching and Anthropic cache_control both get maximum hits.
    msgs = _ensure_stable_prefix_order(msgs)
    # Anthropic: add explicit cache_control breakpoints
    if _is_anthropic_provider():
        msgs = _apply_anthropic_cache_control(msgs)
    return msgs


def _is_anthropic_provider() -> bool:
    """True when BASE_URL points to the Anthropic API (supports prompt caching)."""
    url = os.getenv("KRYTH_BASE_URL", BASE_URL).lower()
    return "anthropic" in url or "claude" in url


def _ensure_stable_prefix_order(messages: list) -> list:
    """Guarantee all system messages precede conversation turns.

    OpenAI models (gpt-4o-mini-2024-07-18+, gpt-4o-2024-05-13+) cache the
    longest matching prefix from a previous request automatically — no markers
    needed.  For this to work, the stable parts (system prompt, injected repo
    context) must appear at the START of the messages array, unchanged between
    turns.  If system messages have drifted into the middle of history (e.g.
    from inject-once context blocks), this reorders them to the top so the
    stable prefix is as long as possible across consecutive calls.

    Disabled via KRYTH_NO_PREFIX_CACHE=1.
    """
    if os.environ.get("KRYTH_NO_PREFIX_CACHE", "0") in ("1", "true", "yes"):
        return messages

    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system  = [m for m in messages if m.get("role") != "system"]

    # Already ordered correctly — avoid allocating a new list
    if messages == system_msgs + non_system:
        return messages

    return system_msgs + non_system


def _apply_anthropic_cache_control(messages: list) -> list:
    """Add cache_control breakpoints to stable prefix components.

    Anthropic prompt caching reduces cost and latency for repeated prefixes.
    We mark the system prompt and early context messages as ephemeral cache
    breakpoints. The system prompt is stable; only the conversation tail changes.

    Only the last 4 cache_control markers matter per Anthropic's docs — we
    place them at the top 2-3 system messages for maximum cache reuse.
    """
    if os.environ.get("KRYTH_NO_PREFIX_CACHE", "0") in ("1", "true", "yes"):
        return messages

    result = []
    system_count = 0
    MAX_CACHE_MARKERS = 3  # Anthropic supports up to 4; leave 1 for tools

    for msg in messages:
        if msg.get("role") == "system" and system_count < MAX_CACHE_MARKERS:
            content = msg.get("content", "")
            # Only cache substantial blocks (>200 chars = ~50 tok minimum)
            if isinstance(content, str) and len(content) > 200:
                msg = dict(msg)
                msg["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
                system_count += 1
        result.append(msg)
    return result


def _chat_completion_with_compat(call_label: str, *, compat_fallback: bool = True, **kwargs):
    """Create a chat completion, retrying once with provider-safe args.

    NVIDIA's OpenAI-compatible endpoint supports the main chat shape, but
    individual models/gateways can reject optional extras such as stop
    sequences, stream_options, or tool schemas with a misleading 404.
    """
    # Always sanitize tool call IDs before sending.
    if "messages" in kwargs:
        kwargs = dict(kwargs)
        kwargs["messages"] = _sanitize_messages_for_provider(kwargs["messages"])
    try:
        return _retry(call_label, client.chat.completions.create, **kwargs)
    except APIStatusError as e:
        if not compat_fallback or not _should_retry_chat_compat(e):
            raise

        safe = dict(kwargs)
        safe.pop("stream_options", None)
        safe.pop("stop", None)

        # If a provider rejects structured tool schemas, fall back to the
        # prompt-level tool-call format that this module already recovers
        # from Hermes/Qwen-style content. Also strip tool_calls from
        # assistant messages in history — they reference ids the model
        # will reject in text mode.
        if safe.get("tools") is not None:
            safe["messages"] = _messages_with_tool_text_fallback(
                safe.get("messages") or [], safe.get("tools")
            )
            safe.pop("tools", None)
        # Strip tool_calls from assistant history and orphaned tool results
        # when in text-mode fallback — they reference ids the model will reject.
        cleaned = []
        for m in (safe.get("messages") or []):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                m = dict(m)
                m.pop("tool_calls", None)
            elif m.get("role") == "tool":
                # Replace tool result with a user message so the model sees
                # the output without needing a valid tool_call_id.
                m = {
                    "role": "user",
                    "content": f"[tool result: {m.get('name', '')}]\n{m.get('content', '')}",
                }
            cleaned.append(m)
        safe["messages"] = _sanitize_messages_for_provider(cleaned)

        ui.muted("(provider compatibility retry: minimal chat/completions payload)")
        return _retry(f"{call_label} compat", client.chat.completions.create, **safe)


# ---------------------------------------------------------------------------
# Hermes / Qwen tool-call recovery
# ---------------------------------------------------------------------------
#
# Some NVIDIA-hosted models (notably qwen3-coder-480b) emit tool calls in
# their native Hermes-style format inside the ``content`` stream instead
# of as structured ``tool_calls``. Two shapes observed in the wild:
#
#   <tool_call>
#   <function=write_file>
#   <parameter=path>index.html</parameter>
#   <parameter=content>...</parameter>
#   </function>
#   </tool_call>
#
#   <tool_call>
#   {"name": "write_file", "arguments": {"path": "index.html", "content": "..."}}
#   </tool_call>
#
# When this happens, the structured ``delta.tool_calls`` stream is empty
# and we'd otherwise treat the response as a final assistant message —
# losing the intended action. ``_recover_hermes_tool_calls`` translates
# such content back into OpenAI ``tool_calls`` so the dispatcher can act.

import string as _string
_TC_ID_CHARS = _string.ascii_letters + _string.digits
def _make_tool_call_id(prefix: str, index: int) -> str:
    """Return a 9-char alphanumeric tool call ID as required by strict providers."""
    raw = f"{prefix}{index:04d}"
    # Pad or truncate to exactly 9 chars using only [a-zA-Z0-9]
    safe = "".join(c for c in raw if c in _TC_ID_CHARS)
    safe = (safe + "aaaaaaaaa")[:9]
    return safe

_HERMES_BLOCK_RE = re.compile(
    r"<(?:tool_call|seed:tool_call)>\s*(.*?)\s*</(?:tool_call|seed:tool_call)>",
    re.DOTALL,
)
_HERMES_FN_RE = re.compile(r"<function=([\w\-]+)>")
_HERMES_PARAM_RE = re.compile(
    r"<parameter=([\w\-]+)>(.*?)</parameter>",
    re.DOTALL,
)
# Malformed variant emitted by qwen3-coder: the function name is
# inlined as a parameter prefix (<parameter=command=run_command>) and
# no <function=...> tag is present. We recognize this when the FIRST
# parameter "name" matches a known tool, and treat the rest of the
# parameter line ("=...") as a stray header (ignored).
_HERMES_PARAM_HEADER_RE = re.compile(
    r"<parameter=([\w\-]+)=([\w\-]+)>",
)


# Detect "degenerate" generations where the model gets stuck repeating
# the same short token (e.g. </function>) until max_tokens. Cheap: only
# checks the trailing window. The detector runs every N chunks during
# streaming so the loop can abort early and salvage what we have rather
# than burning the whole token budget on garbage.
_DEGEN_TAIL_WINDOW = 300
_DEGEN_TAIL_BLOCK = 60
_DEGEN_REPETITIONS = 5
_DEGEN_CHECK_EVERY = 25  # chunks


def _is_degenerate_tail(content: str) -> bool:
    """Return True if the last ``_DEGEN_TAIL_WINDOW`` chars are dominated
    by a single short token repeating ``_DEGEN_REPETITIONS+`` times.

    Catches the common qwen3-coder pathology where the model emits
    ``</function>\\n</function>\\n...`` forever after a confused
    Hermes-block close.
    """
    if len(content) < _DEGEN_TAIL_WINDOW:
        return False
    region = content[-_DEGEN_TAIL_WINDOW:]
    block = region[-_DEGEN_TAIL_BLOCK:]
    if not block.strip():
        return False
    # If the trailing block appears at least N times in the window, the
    # model is looping. Use rstrip to be robust to dangling newlines.
    needle = block.rstrip()
    if len(needle) < 4:
        return False
    return region.count(needle) >= _DEGEN_REPETITIONS


# Boundary tag detector: <CONTENT> is the canonical marker that separates
# reasoning from visible output — the model is instructed to emit it as the
# very first tag before any user-facing text.  All legacy display tags are
# kept as fallbacks so old-style responses still work.
# Everything BEFORE the first match is treated as reasoning (shown in cyan).
# Plain-text models that never emit any tag get a 200-char grace window before
# the buffer is flushed as content so the response is never lost.
_PROTOCOL_BOUNDARY_RE = re.compile(
    r"<(?:CONTENT"
    r"|display|block|status|mission|timeline|spinner|todo|plan"
    r"|task_start|task_update|task_complete|task_skip|task_error"
    r"|summary|warning|exec_stream|build_stream|test_stream"
    r"|tool_read|tool_write|tool_edit|tool_run|tool_result"
    r"|checkpoint|agent|decision|activity|health|budget|risk"
    r"|experience|memory)\b",
    re.IGNORECASE,
)
_PRE_TAG_FLUSH = 600   # chars: give up waiting for <CONTENT>; larger window avoids mid-sentence flush

# Dangling reasoning-close: some reasoning models (e.g. stepfun step-3.7-flash
# on NVIDIA NIM) stream their chain-of-thought INSIDE the `content` field,
# terminated by a closing </think> with NO matching opening <think> (the open
# only appears in the separate reasoning_content channel). The balanced
# think-depth counter never engages, so the reasoning would leak into the
# visible reply. We treat such a dangling close as a reasoning→answer
# separator: everything up to and including it is reasoning.
_REASONING_CLOSE_RE = re.compile(
    r"</(?:think|thinking|seed:think|reasoning|analysis|reflect)\s*>",
    re.IGNORECASE,
)

# Stop sequences sent to the gateway. OpenAI-compatible endpoints terminate
# the response before the model can spam its way to max_tokens. They are
# sequences no legitimate output would emit consecutively.
#
# IMPORTANT: the OpenAI `stop` parameter is capped at 4 items by the spec, and
# several gateways (e.g. Groq) hard-reject >4 with a 400 — which forced KRYTH
# into a slow compatibility-retry loop on every call. Keep this list to <= 4.
# These four cover the degenerate-loop tails we actually observe across
# providers (Hermes/Qwen tool tags, parameter spam, Llama code-interpreter).
_STOP_SEQUENCES = [
    "</function></function></function>",
    "</tool_call></tool_call></tool_call>",
    "</parameter></parameter></parameter></parameter>",
    "<|python_tag|><|python_tag|>",  # Llama code-interpreter token loop
]


def _recover_hermes_tool_calls(content: str) -> list[dict]:
    """Extract tool calls from Hermes/Qwen-formatted content.

    Returns a list of OpenAI-style tool_call dicts. Empty when no
    ``<tool_call>`` blocks are present or none could be parsed against
    the live tools registry.

    Lazy-imports ``agent.tools`` to avoid a circular import at module
    load (tools doesn't import llm directly, but the agent_loop -> tools
    -> llm chain would close otherwise).
    """
    if "<tool_call>" not in content and "<seed:tool_call>" not in content:
        return []

    try:
        from agent.tools import TOOLS
    except Exception:
        return []
    known_names = set(TOOLS.keys())

    calls: list[dict] = []
    for i, block in enumerate(_HERMES_BLOCK_RE.findall(content)):
        # Shape 1: JSON object inside <tool_call>...</tool_call>
        stripped = block.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                name = _sanitize_tool_name(payload.get("name") or "")
                args = payload.get("arguments", {})
                if name in known_names:
                    arguments = (
                        json.dumps(args)
                        if isinstance(args, (dict, list))
                        else str(args)
                    )
                    calls.append({
                        "id": _make_tool_call_id("hrm", i),
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    })
                    continue

        # Shape 2: <function=NAME>...<parameter=KEY>VALUE</parameter>...
        fn_match = _HERMES_FN_RE.search(block)
        name = _sanitize_tool_name(fn_match.group(1)) if fn_match else None

        # Shape 2b (malformed, observed with qwen3-coder): the function
        # name is hidden in the FIRST <parameter=A=B> header. Either
        # group can be the function — the model swaps them. We accept
        # whichever side matches a known tool.
        if not name:
            header_match = _HERMES_PARAM_HEADER_RE.search(block)
            if header_match:
                cand_a, cand_b = header_match.group(1), header_match.group(2)
                if cand_b in known_names:
                    name = cand_b
                elif cand_a in known_names:
                    name = cand_a
                if name:
                    # Strip the malformed header so the real
                    # <parameter=K>v extraction below doesn't pick it
                    # up as a key.
                    block = _HERMES_PARAM_HEADER_RE.sub("", block, count=1)

        params = {k.strip(): v.strip() for k, v in _HERMES_PARAM_RE.findall(block)}
        if name and name in known_names:
            calls.append({
                "id": _make_tool_call_id("hrm", i),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(params),
                },
            })

    return calls


# Self-closing XML tool call format emitted by Llama/Meta models:
#   <tool name="edit_file" arguments={"path": "...", "old_text": "...", "new_text": ""} />
_XML_TOOL_RE = re.compile(
    r'<tool\s+name="([^"]+)"\s+arguments=(\{.*?\})\s*/?>',
    re.DOTALL,
)


def _recover_xml_tool_calls(content: str) -> list[dict]:
    """Parse <tool name="..." arguments={...} /> self-closing XML tool calls.

    Used for Llama/Meta models that emit this format instead of the OpenAI
    structured tool_calls stream.  Returns OpenAI-style tool_call dicts.
    """
    if '<tool ' not in content:
        return []
    try:
        from agent.tools import TOOLS
    except Exception:
        return []
    known_names = set(TOOLS.keys())

    calls: list[dict] = []
    for i, m in enumerate(_XML_TOOL_RE.finditer(content)):
        name = m.group(1).strip()
        args_raw = m.group(2).strip()
        if name not in known_names:
            continue
        try:
            args_obj = json.loads(args_raw)
            arguments = json.dumps(args_obj)
        except json.JSONDecodeError:
            arguments = args_raw
        calls.append({
            "id": _make_tool_call_id("xml", i),
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        })
    return calls


# [TOOL_CALLS] bracket format: some models emit tool calls as
#   [TOOL_CALLS]tool_name{"arg": "value"}
_BRACKET_TOOL_RE = re.compile(
    r"\[TOOL_CALLS\]\s*([\w_-]+)\s*(\{.*?\})",
    re.DOTALL,
)


def _recover_bracket_tool_calls(content: str) -> list[dict]:
    """Parse [TOOL_CALLS]name{args} format emitted by some mistral-family models."""
    if "[TOOL_CALLS]" not in content:
        return []
    try:
        from agent.tools import TOOLS
    except Exception:
        return []
    known_names = set(TOOLS.keys())

    calls = []
    for i, m in enumerate(_BRACKET_TOOL_RE.finditer(content)):
        name = m.group(1).strip()
        args_raw = m.group(2).strip()
        if name not in known_names:
            continue
        try:
            arguments = json.dumps(json.loads(args_raw))
        except json.JSONDecodeError:
            arguments = args_raw
        calls.append({
            "id": _make_tool_call_id("bkt", i),
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        })
    return calls


# Markdown fence recovery: models in text-mode sometimes output ```bash / ```python
# blocks instead of <tool_call>. Extract the commands and map them to run_command.
_FENCE_RE = re.compile(
    r"```(?:bash|sh|shell|cmd|python|py|js|javascript|typescript|ts)?\s*\n(.*?)```",
    re.DOTALL,
)


def _recover_fence_tool_calls(content: str) -> list[dict]:
    """Convert ```bash ... ``` blocks into run_command tool calls.

    Only fires when there are NO proper tool_call blocks already — it's a
    last-resort recovery for models that ignore the text-mode format instruction.
    """
    if "<tool_call>" in content or "<seed:tool_call>" in content:
        return []
    try:
        from agent.tools import TOOLS
        if "run_command" not in TOOLS:
            return []
    except Exception:
        return []

    calls = []
    for i, m in enumerate(_FENCE_RE.finditer(content)):
        body = m.group(1).strip()
        if not body:
            continue
        lines = [
            ln for ln in body.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        if not lines:
            continue
        command = " && ".join(ln.strip() for ln in lines)
        calls.append({
            "id": _make_tool_call_id("fnc", i),
            "type": "function",
            "function": {
                "name": "run_command",
                "arguments": json.dumps({"command": command}),
            },
        })
    return calls


# ---------------------------------------------------------------------------
# Reasoning leak filter
# ---------------------------------------------------------------------------

_LEAK_PATTERNS = [
    # <CONTENT> boundary marker — must be stripped from stored text
    re.compile(r"</?CONTENT\s*>", re.IGNORECASE),
    re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<seed:think>.*?</seed:think>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<seed:tool_call>.*?</seed:tool_call>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<function=[^>]+>.*?</function>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<parameter=[^>]+>.*?</parameter>", re.DOTALL | re.IGNORECASE),
    re.compile(r"</?tool_call\s*/?>", re.IGNORECASE),
    re.compile(r"</?function[^>]*>", re.IGNORECASE),
    re.compile(r"</?parameter[^>]*>", re.IGNORECASE),
    re.compile(r"</?think\s*>", re.IGNORECASE),
    re.compile(r"</?thinking\s*>", re.IGNORECASE),
    # [TOOL_CALLS] bracket format used by mistral-family models
    re.compile(r"\[TOOL_CALLS\]\s*[\w_-]+\s*\{.*?\}", re.DOTALL),
    # Llama/Meta code-interpreter special tokens — never user-visible
    re.compile(r"<\|python_tag\|>", re.IGNORECASE),
    re.compile(r"<\|[a-z_]+\|>", re.IGNORECASE),  # any <|token|> sentinel
    # Self-closing XML tool call format: <tool name="..." arguments={...} />
    re.compile(r'<tool\s+name="[^"]*"\s+arguments=\{[^}]*\}\s*/?>',
               re.DOTALL | re.IGNORECASE),
]

_THINK_CLOSE_RE = re.compile(
    r"</think\s*>|</thinking\s*>|</seed:think\s*>", re.IGNORECASE
)
_THINK_OPEN_RE = re.compile(
    r"<think\s*>|<thinking\s*>|<seed:think\s*>", re.IGNORECASE
)

# Open/close tag pairs for hold-back logic
_LEAK_OPEN_TAGS = ["<think>", "<thinking>", "<seed:think>", "<tool_call>", "<seed:tool_call>", "<function="]
_LEAK_CLOSE_TAGS = ["</think>", "</thinking>", "</seed:think>", "</tool_call>", "</seed:tool_call>", "</function>"]


_API_KEY_PATTERNS = [
    re.compile(r"nvapi-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Za-z0-9]{16}"),
    re.compile(r"AIza[A-Za-z0-9_-]{35}"),
]

def _redact_api_keys(text: str) -> str:
    for pat in _API_KEY_PATTERNS:
        text = pat.sub("[REDACTED_API_KEY]", text)
    return text


def _filter_leaks(text: str) -> str:
    """Remove complete reasoning/protocol blocks and bare tags."""
    # Safety net for reasoning models that stream chain-of-thought in `content`
    # ending in a dangling close tag (no matching open) — e.g. step-3.7-flash.
    # If a reasoning-close appears with no opening tag before it, drop
    # everything up to and including the LAST such close (it was all reasoning).
    if text:
        last_close = None
        for mm in _REASONING_CLOSE_RE.finditer(text):
            last_close = mm
        if last_close is not None:
            head = text[:last_close.start()].lower()
            if not any(op in head for op in ("<think", "<thinking", "<seed:think",
                                             "<reasoning", "<analysis", "<reflect")):
                text = text[last_close.end():].lstrip("\n")
    for pat in _LEAK_PATTERNS:
        text = pat.sub("", text)
    text = _redact_api_keys(text)
    return text


# A valid tool/function name is a bare identifier. Models that speak the
# OpenAI Harmony channel protocol (gpt-oss family) sometimes bleed channel
# markers into the function-name field, e.g. ``read_file<|channel|>json``.
# Title-casing such a name later produces the user-visible garbage
# ``Read File<|Channel|>Json`` and the tool-registry lookup fails. We strip
# any ``<|...|>`` special token and keep the first identifier-shaped token.
_TOOL_NAME_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _sanitize_tool_name(name: str) -> str:
    """Return a clean tool name, stripping Harmony/Llama special tokens.

    ``read_file<|channel|>json`` -> ``read_file``
    ``  write_file `` -> ``write_file``
    Returns "" for input that contains no identifier.
    """
    if not name:
        return ""
    # Remove well-formed <|...|> tokens, then any dangling token delimiters.
    cleaned = re.sub(r"<\|[^|>]*\|>", " ", name)
    cleaned = cleaned.replace("<|", " ").replace("|>", " ")
    m = _TOOL_NAME_ID_RE.search(cleaned)
    return m.group(0) if m else cleaned.strip()


def _strip_surrogates(text: str) -> str:
    """Remove lone surrogate characters that cannot be encoded as UTF-8.

    On Windows, reading binary/non-UTF-8 files via surrogateescape produces
    lone surrogates in the string. The OpenAI SDK serialises the payload with
    json.dumps which calls str.encode('utf-8'), crashing on surrogates. Strip
    them so the request always goes through cleanly.
    """
    return text.encode("utf-8", errors="replace").decode("utf-8")


def _sanitize_messages(messages: list) -> list:
    """Return a copy of the message list with surrogates removed and
    provider-incompatible structures fixed (content+tool_calls on same
    assistant message, etc.)."""
    out = []
    for m in messages:
        m2 = dict(m)
        if isinstance(m2.get("content"), str):
            m2["content"] = _strip_surrogates(m2["content"])
        if m2.get("tool_calls"):
            cleaned_calls = []
            for tc in m2["tool_calls"]:
                tc2 = dict(tc)
                fn = dict(tc2.get("function") or {})
                if isinstance(fn.get("arguments"), str):
                    fn["arguments"] = _strip_surrogates(fn["arguments"])
                tc2["function"] = fn
                cleaned_calls.append(tc2)
            m2["tool_calls"] = cleaned_calls
            # Remove content when tool_calls are present — some providers
            # (mistral-nemotron etc.) reject messages that carry both fields,
            # even when content is an empty string.
            m2.pop("content", None)
        out.append(m2)
    return out


def ask_llm_stream(messages, tools=None, *, routing_hints=None, task_complexity: str = ""):
    # Signal "waiting for model" — the renderer paints a spinner that
    # auto-cancels as soon as the first reasoning/content/tool_call
    # chunk arrives.
    ui.llm_waiting("waiting for model…")

    # Auto-routing is opt-in via env. ask_llm_stream computes a
    # cheap payload-size hint from the rendered messages so the router
    # has something concrete to work with; callers can supply richer
    # hints (recent_failures, explicit_override) by passing routing_hints.
    from agent.model_router import RouteHints, pick_main_model
    if routing_hints is None:
        chars = sum(
            len(m.get("content") or "")
            + sum(len((c.get("function") or {}).get("arguments") or "")
                  for c in (m.get("tool_calls") or []))
            for m in messages
        )
        routing_hints = RouteHints(
            payload_chars=chars,
            has_tool_specs=bool(tools),
            task_complexity=task_complexity,
        )
    elif task_complexity and not routing_hints.task_complexity:
        routing_hints.task_complexity = task_complexity
    selected_model = pick_main_model(routing_hints)

    # Override client/model from model_config if available
    _stream_client = client
    try:
        from agent.model_config.router import get_llm as _mc_get_llm
        _stream_mc_client, _stream_mc_model = _mc_get_llm("main")
        _stream_client = _stream_mc_client
        # Only override model if model_config has a non-default value
        if _stream_mc_model and _stream_mc_model != selected_model:
            selected_model = _stream_mc_model
    except Exception as _e:
        _logger.debug("model_config.router.get_llm unavailable, using main: %s", _e)

    request_messages = _sanitize_messages(messages)
    request_messages = _sanitize_messages_for_provider(request_messages)
    request_tools = tools
    if tools and (_tool_mode() == "text" or selected_model in _model_text_tool_cache):
        request_messages = _messages_with_tool_text_fallback(request_messages, tools)
        request_tools = None

    _max_tokens = int(getenv("KRYTH_MAX_TOKENS", "16384"))
    # Apply any previously discovered limit for this model immediately.
    if selected_model in _model_max_tokens_cache:
        _max_tokens = min(_max_tokens, _model_max_tokens_cache[selected_model])
    # Pre-cap for known constrained models so we never need to halve at all.
    _known_limit = _known_output_limit(selected_model)
    if _known_limit and _max_tokens > _known_limit:
        _max_tokens = _known_limit
        _model_max_tokens_cache[selected_model] = _known_limit

    # Open the stream, automatically shrinking max_tokens if the model
    # rejects our value, and signalling context_overflow when input is too long.
    stream = None
    for _tok_attempt in range(8):
        try:
            stream = _chat_completion_with_compat(
                "ask_llm_stream",
                model=selected_model,
                messages=request_messages,
                tools=request_tools,
                temperature=0,
                max_tokens=_max_tokens,
                stream=True,
                stream_options={"include_usage": True},
                stop=_STOP_SEQUENCES[:4],  # OpenAI/Groq cap `stop` at 4 items
            )
            break  # success
        except KeyboardInterrupt:
            raise
        except APIStatusError as e:
            kind = _classify_400(e)
            try:
                from agent.model import telemetry as _telr
                _telr.incr("retry")
            except Exception:
                pass
            if kind == "max_tokens":
                # Model's output window is smaller than we requested.
                # Extract the real limit; fall back to halving if not parseable.
                real = _extract_token_limit(e)
                if real and real < _max_tokens:
                    new_max = real
                else:
                    new_max = _max_tokens // 2
                new_max = max(new_max, 256)
                _model_max_tokens_cache[selected_model] = new_max
                ui.muted(f"(max_tokens {_max_tokens} → {new_max} for {selected_model})")
                _max_tokens = new_max
                continue
            if kind == "tools_unsupported":
                # Model doesn't support structured tool schemas.
                # Switch to text-mode for this model permanently and retry.
                try:
                    from agent.model import telemetry as _telf
                    _telf.incr("fallback_activated")
                except Exception:
                    pass
                _model_text_tool_cache.add(selected_model)
                ui.muted(f"(tools schema rejected by {selected_model} — switching to text-mode)")
                if request_tools is not None:
                    request_messages = _messages_with_tool_text_fallback(
                        request_messages, request_tools
                    )
                    request_tools = None
                continue
            if kind == "content_tool_calls":
                # Provider rejects assistant messages that have both content
                # and tool_calls. Strip content (or tool_calls) from every
                # assistant message aggressively and retry once.
                fixed = []
                for _m in request_messages:
                    if _m.get("role") == "assistant":
                        _m2 = {k: v for k, v in _m.items() if k != "content"}
                        if not _m2.get("tool_calls"):
                            _m2["content"] = ""
                        fixed.append(_m2)
                    else:
                        fixed.append(_m)
                request_messages = fixed
                # Only retry once — if still failing after this, surface error.
                if _tok_attempt == 0:
                    continue
                return _api_error_response("ask_llm_stream", e)
            if kind == "context_overflow":
                # Input is too large — caller must compact and retry.
                ui.muted("(context full — compacting…)")
                return {
                    "content": None,
                    "tool_calls": None,
                    "finish_reason": "context_overflow",
                    "usage": None,
                    "interrupted": True,
                }
            if kind == "payload_too_large":
                # Provider token-per-minute / request-size cap exceeded. The
                # tool-schema payload is fixed, so retrying won't help — fail
                # fast with guidance instead of a 60s retry storm.
                ui.llm_error(
                    label="ask_llm_stream",
                    message=f"{selected_model}: provider token/size limit exceeded",
                    hint=(
                        "The request (tool schemas + context) is larger than this "
                        "provider tier allows. Use a higher-tier key or a provider "
                        "with larger per-minute limits."
                    ),
                )
                return {
                    "content": None,
                    "tool_calls": None,
                    "finish_reason": "payload_too_large",
                    "usage": None,
                    "interrupted": True,
                }
            return _api_error_response("ask_llm_stream", e)
        except APITimeoutError:
            ui.llm_error(
                label="ask_llm_stream timed out",
                message="gateway didn't respond after retries",
                hint=(
                    "model may be overloaded, prompt too large, or network flaky. "
                    "Try a smaller prompt, run /diag, or retry."
                ),
            )
            return {
                "content": None,
                "tool_calls": None,
                "finish_reason": "timeout",
                "usage": None,
                "interrupted": True,
            }
        except (APIConnectionError, RateLimitError, InternalServerError) as e:
            ui.llm_error(
                label=f"ask_llm_stream {type(e).__name__}",
                message=str(e)[:200] or "transient error after retries",
                hint="Network or provider hiccup; try again, or run /diag.",
            )
            return {
                "content": None,
                "tool_calls": None,
                "finish_reason": "api_error",
                "usage": None,
                "interrupted": True,
            }
    if stream is None:
        ui.llm_error(
            label="ask_llm_stream",
            message=f"could not open stream for {selected_model} after reducing max_tokens to {_max_tokens}",
            hint="The model may have a very small context window. Set KRYTH_MAX_TOKENS to a lower value.",
        )
        return {
            "content": None,
            "tool_calls": None,
            "finish_reason": "api_error",
            "usage": None,
            "interrupted": True,
        }

    content_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    tool_calls_accum: dict = {}
    finish_reason = None
    usage = None
    chunks_since_check = 0
    degenerate = False
    saw_content = False
    saw_reasoning = False
    _reasoning_start: float | None = None
    _think_depth = 0
    _content_started = False
    _pre_tag_buf = ""
    _stream_open = time.monotonic()
    # TTFT (time-to-first-token) guard. Default OFF (0) so slow reasoning
    # models are never prematurely killed — the provider's own request timeout
    # is the backstop against an infinitely-hung connection. Set
    # KRYTH_TTFT_TIMEOUT to a positive number of seconds to re-enable.
    _ttft_timeout = float(getenv("KRYTH_TTFT_TIMEOUT", "0"))
    # Counts tool-call chunks — drives the live "Generating…" spinner so
    # large file writes don't look frozen.  Fires every 10 chunks (not 30)
    # and surfaces the tool name once we can parse it.
    _tool_gen_chunks = 0
    _gen_label = "Generating…"

    # Runtime v2 adapter seam — resolved once per stream (default OFF).
    _adapter_use = _adapter_stream_enabled()
    _adapter = _get_adapter(selected_model) if _adapter_use else None
    if _adapter is not None:
        try:
            _adapter.reset()  # clear per-stream state on the cached adapter
        except Exception:
            pass

    # Burn-in telemetry — pure in-memory counters, never alters output.
    from agent.model import telemetry as _tel
    _tel.incr("adapter_used" if _adapter_use else "legacy_used")
    _tel_provider = getattr(getattr(_adapter, "provider", None), "value", "") or ""
    _tel_ttft_done = False
    _tel_event_order: list[str] = []

    try:
        for chunk in stream:
            # TTFT guard: if no output at all after timeout, bail out.
            # Disabled when _ttft_timeout <= 0 (the default) so slow models
            # are allowed to take their time to first token.
            if (
                _ttft_timeout > 0
                and not saw_content
                and not saw_reasoning
                and not tool_calls_accum
                and time.monotonic() - _stream_open > _ttft_timeout
            ):
                try:
                    stream.close()
                except Exception:
                    pass
                ui.llm_error(
                    label="ask_llm_stream",
                    message=f"{selected_model} TTFT exceeded {_ttft_timeout:.0f}s — model too slow",
                    hint="Try /config to switch to a faster model, or set KRYTH_TTFT_TIMEOUT.",
                )
                return {
                    "content": None,
                    "tool_calls": None,
                    "finish_reason": "timeout",
                    "usage": None,
                    "interrupted": True,
                }

            if getattr(chunk, "usage", None):
                usage = chunk.usage

            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            # ── Runtime v2 adapter seam (flagged) ────────────────────────────
            # Normalize this chunk through the provider-agnostic ModelAdapter,
            # then project the normalized result back onto the same locals the
            # rest of the loop already consumes (reasoning_piece, delta.content,
            # delta.tool_calls, finish_reason, usage). The loop's orchestration
            # is untouched. Default OFF → the manual parsing below runs as-is.
            if _adapter_use:
                try:
                    _norm = _adapter.normalize_chunk(chunk)  # list[NormalizedChunk]
                except Exception:
                    _norm = []
                    _tel.incr("parser_error")
                _txt, _rsn, _tool_deltas = "", "", []
                for _nc in _norm:
                    ct = getattr(_nc, "chunk_type", None)
                    ctv = getattr(ct, "value", ct)
                    _tel_event_order.append(str(ctv))
                    if ctv == "text":
                        _txt += _nc.text or ""
                    elif ctv == "reasoning":
                        _rsn += (_nc.reasoning or _nc.text or "")
                    elif ctv == "tool_call":
                        _tool_deltas.append(_nc)
                        _tel.incr("tool_normalization")
                    elif ctv == "usage":
                        if _nc.usage_in or _nc.usage_out:
                            usage = types.SimpleNamespace(
                                prompt_tokens=_nc.usage_in,
                                completion_tokens=_nc.usage_out,
                                total_tokens=(_nc.usage_in + _nc.usage_out),
                            )
                # A chunk that carried visible CONTENT but normalized to
                # nothing. (Tool-call deltas are legitimately buffered by the
                # normalizer until finish_reason=="tool_calls", so they are NOT
                # malformed when a single chunk yields no emission.)
                if not _norm and getattr(getattr(choice, "delta", None), "content", None):
                    _tel.incr("malformed_chunk")
                # Re-project onto delta so downstream code is identical.
                # tool_calls is always a list (never None) to prevent DeltaMessage
                # validation failures when providers omit the field (BUG 1).
                _tc_list = _adapter_tool_deltas_to_openai(_tool_deltas)
                delta = types.SimpleNamespace(
                    content=_txt or None,
                    reasoning_content=_rsn or None,
                    tool_calls=_tc_list if _tc_list else [],
                )

            # TTFT: first observable output (content / reasoning / tool delta).
            if not _tel_ttft_done and (
                getattr(delta, "content", None)
                or getattr(delta, "reasoning_content", None)
                or getattr(delta, "tool_calls", None)
            ):
                _tel_ttft_done = True
                _tel.observe_ttft(_tel_provider, time.monotonic() - _stream_open)

            # --- Separate reasoning field (OpenAI o1, DeepSeek-R1 API, etc.) ---
            reasoning_piece = (
                getattr(delta, "reasoning_content", None)
                or getattr(delta, "reasoning", None)
                or getattr(delta, "thinking", None)
                or getattr(delta, "reasoning_text", None)
            )
            if reasoning_piece:
                if _reasoning_start is None:
                    _reasoning_start = time.monotonic()
                elapsed = time.monotonic() - _reasoning_start
                ui.llm_reasoning_chunk(reasoning_piece, elapsed=elapsed)
                reasoning_chunks.append(reasoning_piece)
                saw_reasoning = True

            # --- Content stream (may contain <think> blocks for some models) ---
            if delta.content:
                raw_piece = delta.content

                # Strip Llama/Meta special tokens before any other processing
                # so they never reach the renderer or accumulator.
                if "<|" in raw_piece:
                    raw_piece = re.sub(r"<\|[a-z_]+\|>", "", raw_piece)
                    if not raw_piece:
                        continue

                # Detect in-content <think> / </think> tags (Qwen3, local models).
                # Count opens BEFORE closes so a chunk like "text</think>" that exits
                # the block is handled correctly within the same chunk.
                low_piece = raw_piece.lower()
                for tag in ("<think>", "<thinking>", "<seed:think>"):
                    _think_depth += low_piece.count(tag)
                for tag in ("</think>", "</thinking>", "</seed:think>"):
                    _think_depth = max(0, _think_depth - low_piece.count(tag))

                if _think_depth > 0:
                    # Still inside a <think> block — treat as reasoning, not content.
                    if _reasoning_start is None:
                        _reasoning_start = time.monotonic()
                    elapsed = time.monotonic() - _reasoning_start
                    ui.llm_reasoning_chunk(raw_piece, elapsed=elapsed)
                    saw_reasoning = True
                    reasoning_chunks.append(raw_piece)
                    # Do NOT append to content_chunks — think-block text is
                    # internal reasoning, not the final answer.
                    continue

                # --- Boundary detection ---
                # Before the first protocol tag, route free-form text as reasoning.
                # Models are prompted to start their answer with a tag like <display>;
                # anything they write beforehand is their implicit reasoning chain.
                if not _content_started:
                    _pre_tag_buf += raw_piece
                    # A dangling reasoning-close (</think> with no open) means
                    # everything before it was chain-of-thought streamed in the
                    # content channel — route it to reasoning, keep only the
                    # text after the close as the visible answer.
                    mclose = _REASONING_CLOSE_RE.search(_pre_tag_buf)
                    m = _PROTOCOL_BOUNDARY_RE.search(_pre_tag_buf)
                    if mclose and (m is None or mclose.start() <= m.start()):
                        pre = _pre_tag_buf[:mclose.start()]
                        rest = _pre_tag_buf[mclose.end():]
                        _pre_tag_buf = ""
                        _content_started = True
                        if pre.strip():
                            if _reasoning_start is None:
                                _reasoning_start = time.monotonic()
                            elapsed = time.monotonic() - _reasoning_start
                            ui.llm_reasoning_chunk(pre, elapsed=elapsed)
                            reasoning_chunks.append(pre)
                            saw_reasoning = True
                        raw_piece = rest.lstrip("\n")  # fall through as content
                        if not raw_piece:
                            continue
                    elif m:
                        # Found the boundary — everything before it is reasoning.
                        pre = _pre_tag_buf[:m.start()]
                        rest = _pre_tag_buf[m.start():]
                        _pre_tag_buf = ""
                        _content_started = True
                        if pre.strip():
                            if _reasoning_start is None:
                                _reasoning_start = time.monotonic()
                            elapsed = time.monotonic() - _reasoning_start
                            ui.llm_reasoning_chunk(pre, elapsed=elapsed)
                            reasoning_chunks.append(pre)
                            saw_reasoning = True
                        raw_piece = rest  # fall through as normal content
                    elif len(_pre_tag_buf) >= _PRE_TAG_FLUSH:
                        # No tag seen after grace window — plain-text model; flush as content.
                        raw_piece = _pre_tag_buf
                        _pre_tag_buf = ""
                        _content_started = True
                    else:
                        continue  # still accumulating, nothing to emit yet

                # Real content (outside think blocks, past the boundary tag)
                if saw_reasoning and not saw_content:
                    ui.llm_reasoning_end()

                if raw_piece.strip():
                    ui.llm_content_chunk(raw_piece)
                    saw_content = True

                content_chunks.append(raw_piece)
                _reasoning_start = None

            # Periodic degenerate-tail check
            if delta.content or reasoning_piece:
                chunks_since_check += 1
            if chunks_since_check >= _DEGEN_CHECK_EVERY:
                chunks_since_check = 0
                if _is_degenerate_tail("".join(content_chunks)):
                    degenerate = True
                    try:
                        stream.close()
                    except Exception:
                        pass
                    break
                if _is_degenerate_tail("".join(reasoning_chunks)):
                    degenerate = True
                    try:
                        stream.close()
                    except Exception:
                        pass
                    break

            # Defensive normalization: tool_calls=None / missing / non-list
            # must never crash orchestration — normalize to [] in all cases.
            _raw_tcs = getattr(delta, "tool_calls", None)
            _safe_tcs: list = _raw_tcs if isinstance(_raw_tcs, list) else []
            if _safe_tcs:
                for dc in _safe_tcs:
                    try:
                        _merge_tool_call_delta(tool_calls_accum, dc)
                    except Exception:
                        pass  # malformed delta — skip silently, never crash
                # Show a live spinner while tool arguments stream in
                # (large file writes produce many silent chunks with no content).
                if not saw_content and not saw_reasoning:
                    _tool_gen_chunks += 1
                    if _tool_gen_chunks % 10 == 1:
                        # Try to read the tool name + key arg from the partial accum
                        try:
                            slot = tool_calls_accum.get(0, {})
                            fn_name = slot.get("function", {}).get("name", "")
                            partial_args = slot.get("function", {}).get("arguments", "")
                            if fn_name in ("write_file", "edit_file", "multi_edit"):
                                # Extract path from partial JSON (best-effort)
                                import re as _re
                                m = _re.search(r'"path"\s*:\s*"([^"]{1,60})"', partial_args)
                                p = m.group(1) if m else ""
                                _gen_label = f"Generating {p}…" if p else f"Generating {fn_name}…"
                            elif fn_name:
                                _gen_label = f"◈ {fn_name}…"
                        except Exception:
                            pass
                        ui.llm_waiting(f"◈ {_gen_label}")

            if choice.finish_reason:
                finish_reason = choice.finish_reason
        # Stream loop finished cleanly (no break/exception).
        _tel.incr("stream_completion")
        _tel.record_event_order(_tel_event_order)
    except KeyboardInterrupt:
        try:
            stream.close()
        except Exception:
            pass
        if _pre_tag_buf:
            content_chunks.append(_pre_tag_buf)
            _pre_tag_buf = ""
        # Always close the streaming phase so the activity indicator
        # (spinner Live) is released — pure-tool-call responses never
        # see a chunk and would otherwise leave the spinner running.
        if saw_content:
            ui.llm_content_end(render_markdown=False)
        elif saw_reasoning:
            ui.llm_reasoning_end()
        else:
            ui.llm_content_end(render_markdown=False)
        ui.warn("(LLM stream interrupted)")
        content_text = (
            _filter_leaks("".join(content_chunks)) or "".join(reasoning_chunks) or None
        )
        return {
            "content": content_text,
            "tool_calls": None,
            "finish_reason": "interrupted",
            "usage": None,
            "interrupted": True,
        }
    except APIStatusError as e:
        try:
            stream.close()
        except Exception:
            pass
        if saw_content:
            ui.llm_content_end(render_markdown=False)
        elif saw_reasoning:
            ui.llm_reasoning_end()
        else:
            ui.llm_content_end(render_markdown=False)
        return _api_error_response("ask_llm_stream (mid-stream)", e)
    except Exception as e:
        try:
            stream.close()
        except Exception:
            pass
        if _pre_tag_buf:
            content_chunks.append(_pre_tag_buf)
            _pre_tag_buf = ""
        if saw_content:
            ui.llm_content_end(render_markdown=False)
        elif saw_reasoning:
            ui.llm_reasoning_end()
        else:
            ui.llm_content_end(render_markdown=False)
        partial_calls = []
        for _k in sorted(tool_calls_accum.keys()):
            _tc = tool_calls_accum[_k]
            if not isinstance(_tc, dict):
                continue
            _fn = _tc.get("function") or {}
            if not isinstance(_fn, dict):
                _fn = {}
            _name = _fn.get("name") or ""
            if _name:
                _fn["name"] = _sanitize_tool_name(_name)
            _tc["function"] = _fn
            partial_calls.append(_tc)
        raw_partial = "".join(content_chunks)

        # Recover text-format tool calls (Hermes/Qwen or XML) BEFORE filtering
        # strips the blocks — otherwise recovery never fires.
        _recovered = []
        if "<tool_call>" in raw_partial or "<seed:tool_call>" in raw_partial:
            _recovered = _recover_hermes_tool_calls(raw_partial)
        if not _recovered and "<tool " in raw_partial:
            _recovered = _recover_xml_tool_calls(raw_partial)
        if not _recovered and "[TOOL_CALLS]" in raw_partial:
            _recovered = _recover_bracket_tool_calls(raw_partial)
        if not _recovered and "```" in raw_partial:
            _recovered = _recover_fence_tool_calls(raw_partial)
        if _recovered:
            ui.llm_hermes_recovery(len(_recovered))
            return {
                "content": None,
                "tool_calls": _recovered,
                "finish_reason": "recovered_after_stream_error",
                "usage": usage,
                "interrupted": False,
            }

        partial_text = _filter_leaks(raw_partial) or "".join(reasoning_chunks) or None
        ui.llm_error(
            label="ask_llm_stream: stream dropped",
            message=f"{type(e).__name__}: {str(e)[:200]}",
            hint=(
                "Common causes: malformed provider stream chunks, gateway "
                "connection drops, max_tokens too large, or transient load."
            ),
        )
        return {
            "content": partial_text,
            "tool_calls": partial_calls or None,
            "finish_reason": "stream_error",
            "usage": None,
            "interrupted": True,
        }

    # Flush any pre-tag buffer that was still accumulating when the stream ended
    # (model never emitted a protocol tag — treat as plain content).
    if _pre_tag_buf:
        if saw_reasoning and not saw_content:
            ui.llm_reasoning_end()
            saw_reasoning = False
        if _pre_tag_buf.strip():
            ui.llm_content_chunk(_pre_tag_buf)
            saw_content = True
        content_chunks.append(_pre_tag_buf)
        _pre_tag_buf = ""

    # Close the streaming phase cleanly so the renderer finalises the
    # activity indicator. Pure tool_call responses (no content, no
    # reasoning) still need this — otherwise the waiting spinner stays
    # alive and the next Rich Live region (e.g. the permission prompt)
    # clashes with it.
    if saw_content:
        ui.llm_content_end(render_markdown=True)
    elif saw_reasoning:
        ui.llm_reasoning_end()
    else:
        ui.llm_content_end(render_markdown=False)

    # Assemble tool calls — normalize every entry defensively so invalid
    # types never reach the dispatcher.  Converts non-dict entries to []
    # (dropped below) and fixes missing/non-string names.
    _raw_accum_calls = [
        tool_calls_accum[k] for k in sorted(tool_calls_accum.keys())
    ]
    tool_calls = []
    for _tc in _raw_accum_calls:
        if not isinstance(_tc, dict):
            continue  # invalid type — drop silently
        _fn = _tc.get("function")
        if not isinstance(_fn, dict):
            _tc["function"] = {"name": "", "arguments": "{}"}
            _fn = _tc["function"]
        if not isinstance(_fn.get("name"), str):
            _fn["name"] = ""
        if not isinstance(_fn.get("arguments"), str):
            try:
                _fn["arguments"] = json.dumps(_fn.get("arguments") or {})
            except Exception:
                _fn["arguments"] = "{}"
        # Sanitize function names assembled from streaming deltas — Harmony/gpt-oss
        # models can bleed channel tokens into the name field.
        if _fn["name"]:
            _clean = _sanitize_tool_name(_fn["name"])
            if _clean != _fn["name"]:
                try:
                    from agent.model import telemetry as _tel2
                    _tel2.incr("harmony_sanitization")
                except Exception:
                    pass
            _fn["name"] = _clean
        tool_calls.append(_tc)

    usage_dict = None
    if usage is not None:
        usage_dict = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }

    # Reasoning-only completions (no content, no tool_calls) are common on
    # nemotron / step models. Surface the reasoning text so the user
    # isn't staring at a silent "done".
    raw_content = "".join(content_chunks)

    # Recover text-format tool calls (Hermes/Qwen or XML) BEFORE _filter_leaks
    # strips the blocks — otherwise recovery never fires.
    if not tool_calls:
        _recovered = []
        if "<tool_call>" in raw_content or "<seed:tool_call>" in raw_content:
            _recovered = _recover_hermes_tool_calls(raw_content)
        if not _recovered and "<tool " in raw_content:
            _recovered = _recover_xml_tool_calls(raw_content)
        if not _recovered and "[TOOL_CALLS]" in raw_content:
            _recovered = _recover_bracket_tool_calls(raw_content)
        if not _recovered and "```" in raw_content:
            _recovered = _recover_fence_tool_calls(raw_content)
        if _recovered:
            ui.llm_hermes_recovery(len(_recovered))
            tool_calls = _recovered
            content_text = ""
        else:
            content_text = _filter_leaks(raw_content)
    else:
        content_text = _filter_leaks(raw_content)

    if not content_text and not tool_calls and reasoning_chunks:
        content_text = "".join(reasoning_chunks)

    if degenerate and content_text:
        ui.llm_degenerate()
        block = content_text[-_DEGEN_TAIL_BLOCK:].rstrip()
        if len(block) >= 4:
            cut = content_text.rfind(block)
            if cut > 0:
                content_text = content_text[:cut].rstrip()

    if degenerate and not tool_calls:
        return {
            "content": content_text or None,
            "tool_calls": None,
            "finish_reason": "stream_error",
            "usage": usage_dict,
            "interrupted": True,
        }

    return {
        "content": content_text or None,
        "tool_calls": tool_calls or None,
        "finish_reason": finish_reason,
        "usage": usage_dict,
        "interrupted": False,
    }





_CRITIC_SYSTEM = """You are a senior reviewer auditing a change another
agent just made. Your job: find correctness bugs, missed imports,
broken assumptions, security holes, and subtle regressions. NOT style,
not personal-preference cleanup.

Rules:
- Output 0-8 bullet findings, each on its own line, prefixed with the
  severity tag: [BUG] / [RISK] / [SUSPECT].
- Each bullet is one sentence. Cite the file and line if the diff
  shows them.
- If the change looks correct, output the single line: LGTM.
- No headers, no preamble, no markdown, no closing summary."""


def critique(diff_text: str, intent: str = "") -> str:
    """Run a cheap second-opinion pass on a code change.

    ``diff_text`` is a unified diff (or a concatenation of diffs from
    several files). ``intent`` is a one-line description of what the
    change was meant to achieve — gives the critic the success criterion.

    Returns the model's findings as plain text. Empty string on any API
    failure (critique is opportunistic — its absence must never block
    the agent loop).
    """
    if not diff_text or not diff_text.strip():
        return ""

    blob = diff_text[:80000]
    user = (
        (f"Intent: {intent.strip()}\n\n" if intent else "")
        + "Diff:\n" + blob
    )

    try:
        response = _retry(
            "critique",
            client.chat.completions.create,
            model=PLANNER_MODEL,
            messages=[
                {"role": "system", "content": _CRITIC_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0,
            max_tokens=4096,
        )
    except KeyboardInterrupt:
        raise
    except APIStatusError as e:
        ui.muted(f"(critique skipped: {_format_api_error(e)})")
        return ""
    except Exception as e:
        ui.muted(f"(critique unavailable: {type(e).__name__})")
        return ""

    msg = response.choices[0].message
    text = (getattr(msg, "content", None) or "").strip()
    return text


_DIAGNOSE_SYSTEM = """You are a senior engineer triaging a failed
command. Given the command, its stdout/stderr, and exit code, identify
the MOST LIKELY cause and one or two concrete fixes the agent can try
next. Be terse — the agent will read this in a tight loop.

Output exactly three sections (no markdown, no preamble):

CAUSE: one-sentence diagnosis.
FIX: imperative steps the agent should run / change next, comma-separated.
CONFIDENCE: low | medium | high

Rules:
- Never invent file paths or library APIs that aren't visible in the
  evidence. If the cause is unclear, say so and propose a probe.
- Prefer fixes the agent can execute itself (edit X, install Y, run Z).
- Do not suggest 'ask a human' unless the failure is genuinely
  environmental (broken network, missing credentials)."""


def diagnose_error(
    command: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    context_hint: str = "",
) -> str:
    """Ask the planner-model to identify a likely cause + fix for a
    failed command. Returns the structured ``CAUSE / FIX / CONFIDENCE``
    block as plain text. Empty string on any API failure — diagnosis is
    opportunistic and must never block the agent.
    """
    stdout = (stdout or "")[-20000:]
    stderr = (stderr or "")[-20000:]
    payload = (
        f"Command: {command}\n"
        f"Exit code: {exit_code}\n"
    )
    if context_hint.strip():
        payload += f"Context: {context_hint.strip()[:400]}\n"
    payload += f"\n--- stderr (tail) ---\n{stderr}\n\n--- stdout (tail) ---\n{stdout}"

    try:
        response = _retry(
            "diagnose_error",
            client.chat.completions.create,
            model=PLANNER_MODEL,
            messages=[
                {"role": "system", "content": _DIAGNOSE_SYSTEM},
                {"role": "user", "content": payload},
            ],
            temperature=0,
            max_tokens=4096,
        )
    except KeyboardInterrupt:
        raise
    except APIStatusError as e:
        ui.muted(f"(diagnose skipped: {_format_api_error(e)})")
        return ""
    except Exception as e:
        ui.muted(f"(diagnose unavailable: {type(e).__name__})")
        return ""

    msg = response.choices[0].message
    return (getattr(msg, "content", None) or "").strip()
