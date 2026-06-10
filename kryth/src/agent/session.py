from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Optional


def estimate_tokens(text: str) -> int:
    # Use //3 rather than //4: code and JSON tokenize denser than prose,
    # so //4 underestimates badly and triggers compaction too late.
    return max(1, len(text) // 3)


def message_tokens(msg: dict) -> int:
    total = 4
    for key in ("content", "name", "role"):
        v = msg.get(key)
        if isinstance(v, str):
            total += estimate_tokens(v)
    for call in msg.get("tool_calls") or []:
        fn = call.get("function", {})
        total += estimate_tokens(fn.get("name", ""))
        total += estimate_tokens(fn.get("arguments", "") or "")
    return total


@dataclass
class Session:
    messages: list = field(default_factory=list)
    todos: list = field(default_factory=list)
    tool_call_count: int = 0
    system_prompt: str = ""
    project_map: str = ""
    cumulative_in_tokens: int = 0
    cumulative_out_tokens: int = 0
    mode: str = "default"
    can_spawn: bool = True  # Only coordinator (depth=0) can spawn agents
    # Permission profile (readonly / safe / default / auto / yolo).
    # Resolved against ``agent.profiles`` to decide how aggressively to
    # auto-allow tool calls. Per-session so /resume restores the
    # operator's chosen autonomy level.
    profile: str = "default"
    remembered_permissions: dict = field(default_factory=dict)
    # (tool_name, args_signature) → consecutive denial count. Reset
    # whenever the same tool runs successfully. Used by the agent loop
    # to escalate after repeated denials so the model isn't stuck in a
    # loop trying the same blocked call.
    denial_counts: dict = field(default_factory=dict)
    depth: int = 0
    # Multi-agent orchestration mode. Controls whether parallel/sequential
    # agent teams are used and how approval is obtained.
    # Values: "ASK" | "AUTO" | "SESSION_APPROVED" | "ALWAYS_SINGLE"
    multi_agent_mode: str = "ASK"

    def reset(self) -> None:
        self.messages = []
        self.todos = []
        self.tool_call_count = 0
        self.cumulative_in_tokens = 0
        self.cumulative_out_tokens = 0
        self.mode = "default"
        self.remembered_permissions = {}
        self.denial_counts = {}
        # Profile is intentionally NOT reset: /clear is "fresh
        # transcript", not "fresh autonomy level". The operator's
        # explicit /profile choice survives until they change it.

    def total_tokens(self) -> int:
        return sum(message_tokens(m) for m in self.messages)

    def ensure_system(self) -> None:
        if not self.messages or self.messages[0].get("role") != "system":
            self.messages.insert(
                0,
                {"role": "system", "content": self.system_prompt},
            )

    def append(self, msg: dict) -> None:
        self.messages.append(msg)
        # Persistence is opt-out via KRYTH_NO_PERSIST. The store
        # silently drops the write if no session has been started, so
        # calling this when persistence is off costs nothing.
        try:
            from agent.persistence import session_store
            store = session_store()
            store.append_message(msg)
        except Exception:
            # Persistence is a side-channel — its failure must not
            # break the agent loop.
            pass


# Active session for the current execution context.
#
# Implemented as a ``ContextVar`` rather than a module-global stack so:
#   * Tests can run in isolation — each ``Context`` gets its own session.
#   * Future async work (parallel tool dispatch, concurrent subagents)
#     gets free per-task isolation.
#   * push/pop is implicit: ``set()`` returns a Token that ``reset()``
#     uses, no manual stack mutation.
#
# The default ``None`` means "no session yet"; ``get_session`` lazily
# creates one and pins it so the first caller materialises the
# top-level session for the REPL.
_current: contextvars.ContextVar[Optional[Session]] = contextvars.ContextVar(
    "kryth_session", default=None,
)


PushToken = contextvars.Token


def get_session() -> Session:
    s = _current.get()
    if s is None:
        s = Session()
        _current.set(s)
    return s


def push_session(session: Session) -> PushToken:
    """Make ``session`` the current session and return a token the
    caller passes to ``pop_session`` to restore the previous one.

    Depth is tracked on the session itself: a child sits one level
    deeper than whichever session was active when ``push`` was called.
    """
    parent = _current.get()
    session.depth = 0 if parent is None else parent.depth + 1
    return _current.set(session)


def pop_session(token: PushToken | None = None) -> None:
    """Restore the previous session.

    When called with a token (recommended), use ``ContextVar.reset``.
    The legacy zero-arg form is supported for callers that haven't
    migrated yet: it just sets the current session to ``None`` so the
    next ``get_session`` lazily creates a fresh top-level one.
    """
    if token is not None:
        try:
            _current.reset(token)
            return
        except (ValueError, LookupError):
            # Token from a different context; fall through to the
            # zero-arg behaviour.
            pass
    _current.set(None)


def reset_session() -> None:
    s = _current.get()
    if s is not None:
        s.reset()
    # A reset is the user saying "start fresh" — give them a fresh
    # persisted transcript too so /resume doesn't surface a half-empty
    # log. Failures are silent (persistence is a side-channel).
    try:
        from agent.persistence import session_store
        session_store().start_new(".")
    except Exception:
        pass
