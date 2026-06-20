"""Hard token budget manager — proactive pre-call token gate.

Fires BEFORE every ask_llm_stream() call. If estimated tokens exceed the
budget tier for the current task complexity, triggers compression cascades
before the request goes out — not after the model hits a context error.

Budget tiers (overridable via env):
    TRIVIAL   KRYTH_BUDGET_TRIVIAL   = 800   tok
    MEDIUM    KRYTH_BUDGET_MEDIUM    = 5_000 tok
    COMPLEX   KRYTH_BUDGET_COMPLEX   = 30_000 tok

Usage:
    from agent.token_budget import check, BudgetResult
    result = check(session.messages, tools, complexity)
    if result.over_budget:
        # compression already applied by check()
        pass
"""
from __future__ import annotations

import os
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Budget tiers
# ---------------------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        v = os.environ.get(name, "")
        return int(v) if v else default
    except ValueError:
        return default


BUDGET_TRIVIAL = _env_int("KRYTH_BUDGET_TRIVIAL", 800)
BUDGET_MEDIUM  = _env_int("KRYTH_BUDGET_MEDIUM",  5_000)
BUDGET_COMPLEX = _env_int("KRYTH_BUDGET_COMPLEX", 30_000)

# Compression cascade thresholds as fractions of budget
_COMPRESS_AT    = 0.85   # light compress when 85% of budget used
_AGGRESSIVE_AT  = 1.00   # aggressive when at or over budget
_EMERGENCY_AT   = 1.30   # emergency archive when 130% over (severe bloat)


def get_budget(complexity: str) -> int:
    """Return token budget for a given task complexity."""
    if complexity == "simple":
        return BUDGET_TRIVIAL
    if complexity == "complex":
        return BUDGET_COMPLEX
    return BUDGET_MEDIUM


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _char_to_tok(chars: int) -> int:
    """Fast char→token estimate: 4 chars ≈ 1 token (GPT/Claude average)."""
    return max(0, chars // 4)


def estimate_messages_tokens(messages: list) -> int:
    """Estimate tokens consumed by the message history."""
    total = 0
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):
            # multi-part content (vision etc.)
            for part in content:
                if isinstance(part, dict):
                    total += _char_to_tok(len(str(part.get("text", "") or "")))
        else:
            total += _char_to_tok(len(str(content)))
        # tool_calls overhead
        for tc in m.get("tool_calls", []) or []:
            total += _char_to_tok(len(str(tc)))
    return total


def estimate_tools_tokens(tools: list) -> int:
    """Estimate tokens consumed by the tool schema list."""
    return _char_to_tok(sum(len(str(s)) for s in tools))


# ---------------------------------------------------------------------------
# Budget result
# ---------------------------------------------------------------------------

@dataclass
class BudgetResult:
    budget: int
    estimated: int
    tools_tok: int
    history_tok: int
    over_budget: bool
    overflow: int          # tokens over budget (0 when under)
    compression_level: str # "none" | "light" | "medium" | "aggressive" | "emergency"
    chars_freed: int = 0   # chars freed by compression actions taken


# ---------------------------------------------------------------------------
# Core check — call before every ask_llm_stream()
# ---------------------------------------------------------------------------

def check(
    messages: list,
    tools: list,
    complexity: str,
    *,
    session=None,
    auto_compress: bool = True,
) -> BudgetResult:
    """Estimate tokens, compare against budget, compress if over budget.

    If `session` is provided and `auto_compress=True`, applies compression
    directly to `session.messages` when over budget.

    Returns a BudgetResult describing what happened.
    """
    budget      = get_budget(complexity)
    tools_tok   = estimate_tools_tokens(tools)
    history_tok = estimate_messages_tokens(messages)
    estimated   = tools_tok + history_tok
    ratio       = estimated / max(budget, 1)
    overflow    = max(0, estimated - budget)

    # Determine what compression level is warranted
    if ratio < _COMPRESS_AT:
        level = "none"
    elif ratio < _AGGRESSIVE_AT:
        level = "light"
    elif ratio < _EMERGENCY_AT:
        level = "aggressive"
    else:
        level = "emergency"

    over_budget = ratio >= _COMPRESS_AT
    chars_freed = 0

    if auto_compress and session is not None and over_budget:
        chars_freed = _apply_compression(session, level)

    return BudgetResult(
        budget=budget,
        estimated=estimated,
        tools_tok=tools_tok,
        history_tok=history_tok,
        over_budget=over_budget,
        overflow=overflow,
        compression_level=level,
        chars_freed=chars_freed,
    )


# ---------------------------------------------------------------------------
# Compression actions
# ---------------------------------------------------------------------------

def _apply_compression(session, level: str) -> int:
    """Apply the appropriate compression level to session.messages in-place.

    Returns total chars freed.
    """
    freed = 0
    try:
        from agent.context_supervisor import _compress_messages

        if level in ("light",):
            new_msgs, freed = _compress_messages(session.messages, aggressive=False)
            session.messages = new_msgs

        elif level in ("aggressive",):
            new_msgs, freed = _compress_messages(session.messages, aggressive=True)
            session.messages = new_msgs
            # Also checkpoint if checkpoint_manager is available
            try:
                from agent.checkpoint_manager import apply_checkpoint, should_checkpoint
                if should_checkpoint(session):
                    new_msgs, archived = apply_checkpoint(session.messages)
                    session.messages = new_msgs
                    freed += archived
            except Exception:
                pass

        elif level == "emergency":
            # Keep only system messages + last 4 turns
            _keep = 4
            sys_msgs  = [m for m in session.messages if m.get("role") == "system"]
            rest      = [m for m in session.messages if m.get("role") != "system"]
            pre_chars = sum(len(str(m.get("content") or "")) for m in session.messages)
            keep      = rest[-_keep:]
            session.messages = sys_msgs + keep
            post_chars = sum(len(str(m.get("content") or "")) for m in session.messages)
            freed = max(0, pre_chars - post_chars)

    except Exception:
        pass

    return freed


# ---------------------------------------------------------------------------
# Audit report helper
# ---------------------------------------------------------------------------

def audit_report(messages: list, tools: list, complexity: str) -> str:
    """Return a human-readable token forensics report (for /audit command)."""
    budget    = get_budget(complexity)
    tools_tok = estimate_tools_tokens(tools)

    # Break down message history by role
    sys_tok  = 0
    user_tok = 0
    asst_tok = 0
    tool_tok = 0
    for m in messages:
        chars = len(str(m.get("content") or ""))
        role  = m.get("role", "")
        tok   = _char_to_tok(chars)
        if role == "system":
            sys_tok += tok
        elif role == "user":
            user_tok += tok
        elif role == "assistant":
            asst_tok += tok
        elif role == "tool":
            tool_tok += tok

    total = tools_tok + sys_tok + user_tok + asst_tok + tool_tok
    pct   = int(total / max(budget, 1) * 100)

    lines = [
        "── Token Forensics ──────────────────────────",
        f"  Budget tier  : {complexity} → {budget:,} tok",
        f"  Tool schemas : {tools_tok:,} tok  ({len(tools)} tools)",
        f"  System msgs  : {sys_tok:,} tok",
        f"  User msgs    : {user_tok:,} tok",
        f"  Assistant    : {asst_tok:,} tok",
        f"  Tool results : {tool_tok:,} tok",
        f"  ─────────────────────────────────────────",
        f"  Total est.   : {total:,} tok  ({pct}% of budget)",
    ]
    if pct >= 100:
        lines.append(f"  ⚠ OVER BUDGET by {total - budget:,} tok")
    elif pct >= 85:
        lines.append(f"  ◈ Near budget — compression will fire at next call")
    return "\n".join(lines)
