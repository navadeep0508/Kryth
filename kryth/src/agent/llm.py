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
import os
import re
import time

from agent import ui
from agent.env import getenv

load_dotenv()

# Provider endpoint + models are env-configurable so users can swap
# providers (OpenAI, NVIDIA, freemodel.dev, OpenRouter, local llama.cpp)
# without editing this file.
BASE_URL = getenv("KRYTH_BASE_URL", "https://api.openai.com/v1")

# Each tier is env-overridable. Set ``KRYTH_MAIN_MODEL`` (etc.) in
# .env if your provider exposes different names.
MAIN_MODEL = getenv("KRYTH_MAIN_MODEL", "gpt-4o-mini")
PLANNER_MODEL = getenv("KRYTH_PLANNER_MODEL", "gpt-4o-mini")
SUMMARIZER_MODEL = getenv("KRYTH_SUMMARIZER_MODEL", "gpt-4o-mini")

# Transient errors worth retrying. Auth / bad-request / not-found are
# terminal — retrying won't help.
RETRYABLE = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)
RETRY_DELAYS = (0.5, 1.5, 4.0)


def _make_client() -> OpenAI:
    """Create the OpenAI client, reading env vars at call time."""
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key or key == "not-set":
        key = "not-configured"
    return OpenAI(
        base_url=getenv("KRYTH_BASE_URL", BASE_URL),
        api_key=key,
        timeout=httpx.Timeout(connect=15.0, read=180.0, write=60.0, pool=15.0),
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
    except Exception:
        pass

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
    """Return schema/text tool delivery mode for the active provider."""
    mode = getenv("KRYTH_TOOL_MODE", "auto").strip().lower()
    if mode in {"schema", "text"}:
        return mode
    return "text" if _is_nvidia_endpoint() else "schema"


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
        return True
    if code == 404 and _is_nvidia_endpoint():
        detail = _status_detail(err).lower()
        return "page not found" in detail or "not found" in detail
    return False


def _tool_text_fallback(tools) -> str:
    if not tools:
        return ""
    lines = [
        "Provider compatibility mode: structured tool schemas were not accepted.",
        "When you need a tool, output exactly one tool call block and no prose:",
        '<tool_call>{"name":"tool_name","arguments":{"arg":"value"}}</tool_call>',
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


def _chat_completion_with_compat(call_label: str, *, compat_fallback: bool = True, **kwargs):
    """Create a chat completion, retrying once with provider-safe args.

    NVIDIA's OpenAI-compatible endpoint supports the main chat shape, but
    individual models/gateways can reject optional extras such as stop
    sequences, stream_options, or tool schemas with a misleading 404.
    """
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
        # from Hermes/Qwen-style content.
        if safe.get("tools") is not None:
            safe["messages"] = _messages_with_tool_text_fallback(
                safe.get("messages") or [], safe.get("tools")
            )
            safe.pop("tools", None)

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

_HERMES_BLOCK_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>",
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


# Stop sequences sent to the gateway. NVIDIA's OpenAI-compatible
# endpoint honours these for most models, terminating the response
# before the model can spam its way to max_tokens. They are sequences
# no legitimate output would emit consecutively.
_STOP_SEQUENCES = [
    "</function></function></function>",
    "</tool_call></tool_call></tool_call>",
    "</parameter></parameter></parameter></parameter>",
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
    if "<tool_call>" not in content:
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
                name = payload.get("name")
                args = payload.get("arguments", {})
                if name in known_names:
                    arguments = (
                        json.dumps(args)
                        if isinstance(args, (dict, list))
                        else str(args)
                    )
                    calls.append({
                        "id": f"hermes_{i}",
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    })
                    continue

        # Shape 2: <function=NAME>...<parameter=KEY>VALUE</parameter>...
        fn_match = _HERMES_FN_RE.search(block)
        name = fn_match.group(1) if fn_match else None

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
                "id": f"hermes_{i}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(params),
                },
            })

    return calls


# ---------------------------------------------------------------------------
# Reasoning leak filter
# ---------------------------------------------------------------------------

_LEAK_PATTERNS = [
    re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<function=[^>]+>.*?</function>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<parameter=[^>]+>.*?</parameter>", re.DOTALL | re.IGNORECASE),
    re.compile(r"</?tool_call\s*/?>", re.IGNORECASE),
    re.compile(r"</?function[^>]*>", re.IGNORECASE),
    re.compile(r"</?parameter[^>]*>", re.IGNORECASE),
    re.compile(r"</?think\s*>", re.IGNORECASE),
    re.compile(r"</?thinking\s*>", re.IGNORECASE),
]

_THINK_CLOSE_RE = re.compile(r"</think\s*>|</thinking\s*>", re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think\s*>|<thinking\s*>", re.IGNORECASE)

# Open/close tag pairs for hold-back logic
_LEAK_OPEN_TAGS = ["<think>", "<thinking>", "<tool_call>", "<function="]
_LEAK_CLOSE_TAGS = ["</think>", "</thinking>", "</tool_call>", "</function>"]


def _filter_leaks(text: str) -> str:
    """Remove complete reasoning/protocol blocks and bare tags."""
    for pat in _LEAK_PATTERNS:
        text = pat.sub("", text)
    return text


def _strip_surrogates(text: str) -> str:
    """Remove lone surrogate characters that cannot be encoded as UTF-8.

    On Windows, reading binary/non-UTF-8 files via surrogateescape produces
    lone surrogates in the string. The OpenAI SDK serialises the payload with
    json.dumps which calls str.encode('utf-8'), crashing on surrogates. Strip
    them so the request always goes through cleanly.
    """
    return text.encode("utf-8", errors="replace").decode("utf-8")


def _sanitize_messages(messages: list) -> list:
    """Return a copy of the message list with all surrogate characters removed."""
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
        out.append(m2)
    return out


def ask_llm_stream(messages, tools=None, *, routing_hints=None):
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
        )
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
    except Exception:
        pass

    request_messages = _sanitize_messages(messages)
    request_tools = tools
    if tools and _tool_mode() == "text":
        request_messages = _messages_with_tool_text_fallback(request_messages, tools)
        request_tools = None

    _max_tokens = int(getenv("KRYTH_MAX_TOKENS", "16384"))

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
            stop=_STOP_SEQUENCES,
        )
    except KeyboardInterrupt:
        raise
    except APIStatusError as e:
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
        # Retries exhausted on a transient class. Surface cleanly
        # instead of dumping a traceback.
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
    # Track open <think> tags in the content stream for models that
    # don't use a separate reasoning_content field (e.g. Qwen3, DeepSeek-R1).
    _think_depth = 0

    try:
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage

            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

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

                # Detect in-content <think> / </think> tags (Qwen3, local models).
                # Count opens BEFORE closes so a chunk like "text</think>" that exits
                # the block is handled correctly within the same chunk.
                low_piece = raw_piece.lower()
                for tag in ("<think>", "<thinking>"):
                    _think_depth += low_piece.count(tag)
                for tag in ("</think>", "</thinking>"):
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

                # Real content (outside any think block)
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

            if getattr(delta, "tool_calls", None):
                for dc in delta.tool_calls:
                    _merge_tool_call_delta(tool_calls_accum, dc)

            if choice.finish_reason:
                finish_reason = choice.finish_reason
    except KeyboardInterrupt:
        try:
            stream.close()
        except Exception:
            pass
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
        if saw_content:
            ui.llm_content_end(render_markdown=False)
        elif saw_reasoning:
            ui.llm_reasoning_end()
        else:
            ui.llm_content_end(render_markdown=False)
        partial_calls = [
            tool_calls_accum[k] for k in sorted(tool_calls_accum.keys())
        ]
        raw_partial = "".join(content_chunks)

        # Recover Hermes/Qwen tool calls from raw content BEFORE filtering
        # strips the <tool_call> blocks.
        if "<tool_call>" in raw_partial:
            recovered = _recover_hermes_tool_calls(raw_partial)
            if recovered:
                ui.llm_hermes_recovery(len(recovered))
                return {
                    "content": None,
                    "tool_calls": recovered,
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

    tool_calls = [
        tool_calls_accum[k] for k in sorted(tool_calls_accum.keys())
    ]

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

    # Recover Hermes/Qwen tool calls from raw content BEFORE _filter_leaks
    # strips the <tool_call> blocks — otherwise recovery never fires.
    if not tool_calls and "<tool_call>" in raw_content:
        recovered = _recover_hermes_tool_calls(raw_content)
        if recovered:
            ui.llm_hermes_recovery(len(recovered))
            tool_calls = recovered
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


_PLANNER_SYSTEM = """You plan small-to-medium software tasks for an
autonomous coding agent. Output a STRICT JSON object — no markdown, no
prose preamble, just the object. The object must conform to this
schema:

{
  "goal": "one sentence — what the user actually wants",
  "task_type": "build_app|build_landing|build_dashboard|build_api|build_cli|build_library|fix_bug|refactor|investigate|other",
  "required_files": [
    {"path": "relative/path.ext", "purpose": "one-line description"}
  ],
  "execution_steps": [
    "ordered, imperative steps the executor should perform"
  ],
  "validation_steps": [
    "how the executor verifies success (run X, open Y, curl Z)"
  ],
  "risks": [
    "known failure modes or unknowns worth surfacing"
  ],
  "dependencies": [
    "third-party packages/runtimes required"
  ]
}

Hard rules:
- For build_app / build_landing / build_dashboard / build_api: required_files
  MUST contain >= 3 entries with concrete paths and real purposes.
- execution_steps and validation_steps must be non-empty for any build.
- Filenames should match the language of the request (index.html,
  styles.css, script.js, app.py, requirements.txt, package.json, etc).
- Do NOT write any code. Only the plan.
- Output is parsed by ``json.loads`` — single object, no trailing comma,
  no comments."""


def _extract_json_object(text: str) -> dict | None:
    """Pull the first balanced ``{...}`` block out of ``text`` and parse
    it. Returns None on failure. Tolerates surrounding prose and
    markdown fences that some models add despite instructions.
    """
    if not text:
        return None
    # Strip markdown code fences if present.
    if "```" in text:
        # Pull contents of the first fence.
        parts = text.split("```")
        for part in parts:
            cleaned = part.lstrip("json").lstrip("\n").strip()
            if cleaned.startswith("{"):
                text = cleaned
                break

    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = text[start:i + 1]
                try:
                    obj = json.loads(blob)
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def ask_planner(user_input):
    """Return ``(structured_plan_dict_or_None, fallback_prose)``.

    On success the dict matches the schema in ``_PLANNER_SYSTEM``. On
    parse failure we return any prose the model produced so the caller
    can still display it as a hint — degrades gracefully when the
    planner ignores the JSON instruction.
    """
    # Use model_config router for planner role if available
    _planner_client = client
    _planner_model = PLANNER_MODEL
    try:
        from agent.model_config.router import get_llm as _mc_get_llm
        _planner_client, _planner_model = _mc_get_llm("planner")
    except Exception:
        pass

    try:
        response = _retry(
            "ask_planner",
            _planner_client.chat.completions.create,
            model=_planner_model,
            messages=[
                {"role": "system", "content": _PLANNER_SYSTEM},
                {"role": "user", "content": user_input},
            ],
            temperature=0,
            max_tokens=900,
        )
    except KeyboardInterrupt:
        raise
    except APIStatusError as e:
        ui.muted(f"(planner skipped: {_format_api_error(e)})")
        return None, ""
    except Exception as e:
        ui.muted(f"(planner unavailable: {type(e).__name__}; continuing)")
        return None, ""

    msg = response.choices[0].message
    text = getattr(msg, "content", None) or ""
    text = text.strip()
    if not text:
        return None, ""

    plan = _extract_json_object(text)
    if plan is None:
        # Couldn't parse — return prose so the caller can still show it.
        return None, text
    return plan, text


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

    blob = diff_text[:14000]
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
            max_tokens=600,
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
    stdout = (stdout or "")[-2000:]
    stderr = (stderr or "")[-3000:]
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
            max_tokens=400,
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


def summarize(messages_to_compress: list) -> str:  # noqa: C901
    # Use model_config router for summary role if available
    _sum_client = client
    _sum_model = SUMMARIZER_MODEL
    try:
        from agent.model_config.router import get_llm as _mc_get_llm
        _sum_client, _sum_model = _mc_get_llm("summary")
    except Exception:
        pass

    rendered = []
    for m in messages_to_compress:
        role = m.get("role", "?")
        content = m.get("content")
        if content:
            rendered.append(f"[{role}] {content}")
        for call in m.get("tool_calls") or []:
            fn = call.get("function", {})
            rendered.append(
                f"[assistant->tool] {fn.get('name')}({fn.get('arguments')})"
            )

    blob = "\n".join(rendered)[:12000]

    try:
        response = _retry(
            "summarize",
            _sum_client.chat.completions.create,
            model=_sum_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the following agent conversation. "
                        "Preserve: user intent, decisions made, files touched, "
                        "errors hit, and the current state of the task. "
                        "Be terse. No markdown."
                    ),
                },
                {"role": "user", "content": blob},
            ],
            temperature=0,
            max_tokens=400,
        )
    except KeyboardInterrupt:
        raise
    except APIStatusError as e:
        ui.muted(f"(summarizer skipped: {_format_api_error(e)})")
        return ""
    except Exception as e:
        ui.muted(f"(summarizer unavailable: {type(e).__name__})")
        return ""
    msg = response.choices[0].message
    text = getattr(msg, "content", None)
    if not text:
        text = getattr(msg, "reasoning_content", None)
    return text or ""
