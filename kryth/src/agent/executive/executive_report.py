"""Executive Report — generates a post-mission markdown report.

Written to .kryth/executive_reports/{session_id}.md after every mission.
Contains:
  - Mission summary
  - Executive decisions taken
  - Confidence timeline
  - Quality assessment
  - Budget breakdown
  - Risk assessment
  - Escalations
  - Improvement recommendations for the Improvement Lab
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent.executive.executive_state import ExecutiveState, ExecutiveDecision

logger = logging.getLogger(__name__)


class ExecutiveReport:

    def __init__(self, project_root: str | Path = ".") -> None:
        self._root = Path(project_root).resolve()

    def generate(self, state: ExecutiveState, elapsed_s: float = 0.0) -> str:
        """Write report to disk. Returns absolute path."""
        out_dir = self._root / ".kryth" / "executive_reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{state.session_id}.md"

        content = self._render(state, elapsed_s)
        path.write_text(content, encoding="utf-8")
        logger.info("Executive report: %s", path)
        return str(path)

    # ── Render ────────────────────────────────────────────────────────

    def _render(self, s: ExecutiveState, elapsed_s: float) -> str:
        c   = s.confidence
        q   = s.quality
        b   = s.budget
        h   = s.health
        rec = s.recommendation

        now = datetime.now(timezone.utc).isoformat()
        verdict = "[SUCCESS]" if rec == ExecutiveDecision.CONTINUE else f"[{rec.value.upper()}]"

        lines = [
            "# KRYTH Executive Intelligence Report",
            "",
            f"**Session:** `{s.session_id}`  "
            f"**Mission:** {s.mission}  "
            f"**Generated:** {now[:19]}",
            "",
            "---",
            "",
            "## Executive Verdict",
            "",
            f"> **{verdict}** — {s.rationale or 'Mission assessed by Executive Intelligence Layer.'}",
            "",
            "---",
            "",
            "## Mission Health",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total actions | {h.total_actions} |",
            f"| Succeeded | {h.succeeded} |",
            f"| Failed | {h.failed} |",
            f"| Skipped | {h.skipped} |",
            f"| Progress | {h.progress_pct:.1f}% |",
            f"| Replans | {h.replan_count} |",
            f"| Elapsed | {elapsed_s:.1f}s |",
            "",
            "## Confidence",
            "",
            f"| Dimension | Score |",
            f"|-----------|-------|",
            f"| Overall | {c.overall:.0%} |",
            f"| Mission | {c.mission:.0%} |",
            f"| Planning | {c.planning:.0%} |",
            f"| Execution | {c.execution:.0%} |",
            f"| Verification | {c.verification:.0%} |",
            f"| Recovery | {c.recovery:.0%} |",
            f"| Memory | {c.memory:.0%} |",
            f"| Executors | {c.executors:.0%} |",
            "",
            "## Quality",
            "",
            f"| Metric | Score |",
            f"|--------|-------|",
            f"| Overall | {q.overall:.0%} |",
            f"| Test pass rate | {q.test_pass_rate:.0%} |",
            f"| Verification rate | {q.verification_rate:.0%} |",
            f"| Security score | {q.security_score:.0%} |",
            f"| Evaluation score | {q.eval_score:.0%} |",
            "",
            "## Budget",
            "",
            f"| Resource | Used | Limit | % |",
            f"|----------|------|-------|---|",
            f"| Tokens | {b.tokens_used:,} | {b.token_limit:,} | {b.token_pct:.0f}% |",
            f"| LLM calls | {b.llm_calls} | 200 | {b.llm_calls/2:.0f}% |",
            f"| API retries | {b.api_retries} | 20 | {b.api_retries*5:.0f}% |",
            "",
            "## Risk",
            "",
            f"**Risk Level:** {s.risk.value.upper()}  ",
            f"**Risk Score:** {s.annotations.get('risk_score', '?')}",
            "",
        ]

        # Decisions log
        if s.decisions:
            lines += ["## Executive Decisions", ""]
            for ts, decision in s.decisions[-20:]:
                lines.append(f"- `{ts}` **{decision.value.upper()}**")
            lines.append("")

        # Escalations
        if s.escalations:
            lines += ["## Escalations", ""]
            for esc in s.escalations:
                lines.append(f"- {esc}")
            lines.append("")

        # Improvement recommendations
        lines += [
            "## Improvement Lab Recommendations",
            "",
            "The following dimensions should be evaluated by the Improvement Lab:",
            "",
        ]
        if h.replan_count > 1:
            lines.append(f"- Improve planning quality — {h.replan_count} replans occurred")
        if q.test_pass_rate < 0.85:
            lines.append(f"- Increase test pass rate (current: {q.test_pass_rate:.0%})")
        if q.verification_rate < 0.85:
            lines.append(f"- Improve verification coverage (current: {q.verification_rate:.0%})")
        if b.token_pct > 60:
            lines.append(f"- Reduce token spend (used {b.token_pct:.0f}% of budget)")
        if not lines[-1].startswith("-"):
            lines.append("- No critical recommendations — executive quality is good.")
        lines.append("")

        lines += [
            "---",
            "",
            "*Generated by KRYTH Executive Intelligence Layer.*",
            "*This report is advisory — no production changes were made automatically.*",
            "",
        ]
        return "\n".join(lines)
