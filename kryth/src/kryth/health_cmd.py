"""Health check - session and model diagnostics for the /health REPL command.

Shows:
- Model connectivity status
- Session metrics (tools, tokens, messages)
- Timing breakdown (analysis vs. execution)
- Context pressure (how full the window is)
"""

from __future__ import annotations

from agent.session import get_session
from agent.llm import BASE_URL, MAIN_MODEL


def get_health_report() -> str:
    """Return a multi-line health report string."""
    from agent.context_supervisor import ContextSupervisor, _model_max_tokens

    s = get_session()
    total_tokens = s.cumulative_in_tokens + s.cumulative_out_tokens

    lines: list[str] = []
    lines.append("--- Health Report ---")
    lines.append(f"  Model:       {MAIN_MODEL}")
    lines.append(f"  Endpoint:    {BASE_URL}")
    lines.append(f"  Session:     {len(s.messages)} messages, {total_tokens:,} total tokens")
    lines.append(f"  Tool calls:  {s.tool_call_count}")
    lines.append(f"  Mode:        {s.mode}")
    lines.append(f"  Profile:     {s.profile}")

    if s.analysis_time_s > 0 or s.impl_time_s > 0:
        total_time = s.analysis_time_s + s.impl_time_s
        analysis_pct = (s.analysis_time_s / total_time * 100) if total_time > 0 else 0
        lines.append(f"  Timing:      {s.analysis_time_s:.1f}s analysis / {s.impl_time_s:.1f}s exec ({analysis_pct:.0f}% read)")

    if s.duplicate_searches > 0:
        lines.append(f"  Duplicate searches: {s.duplicate_searches}")

    # Context pressure
    try:
        sup = ContextSupervisor(s)
        frac = sup.token_fraction()
        max_tok = _model_max_tokens()
        pct = frac * 100
        label = "LOW" if pct < 50 else "MEDIUM" if pct < 80 else "HIGH"
        lines.append(f"  Context:     {pct:.0f}% ({label})  max={max_tok:,}")
    except Exception:
        lines.append("  Context:     (unavailable)")

    lines.append("-------------------------")
    return "\n".join(lines)
