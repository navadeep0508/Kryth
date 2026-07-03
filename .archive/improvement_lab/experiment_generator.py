"""Translates optimization recommendations into safe, isolated experiments.

Every experiment is a list of FileChange objects — exact string substitutions
applied only to the sandbox, never to production files. If a template cannot
locate its target fragment, it returns None and the generator tries the next
template in the registry.

Safe experiment types:
  scheduler, worker_pool, parallel_batching, memory_preload, tool_dispatch,
  streaming, context_compression, recovery_logic, prompt_optimization,
  model_routing, dashboard_refresh

NEVER experiment on: auth, permissions, safety systems, benchmark history.
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class FileChange:
    """A single, reversible string-substitution change."""
    relative_path: str     # relative to project root
    old_fragment: str      # exact string to replace
    new_fragment: str      # replacement
    description: str       # what this changes (for the report)


@dataclass
class Experiment:
    """Fully specified experiment ready to run in the sandbox."""
    id: str
    type: str              # e.g. "recovery_logic", "prompt_optimization"
    category: str          # recommendation category: API / PARALLEL / LLM / MEMORY / etc.
    hypothesis: str        # testable prediction: "X will improve Y by ~Z%"
    description: str       # human-readable summary
    changes: list[FileChange]
    expected_gain_pct: float
    confidence: float
    source_recommendation: str   # the text of the recommendation that triggered this
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── Template base ─────────────────────────────────────────────────────────────

class ExperimentTemplate(ABC):
    """Generates an Experiment for a specific bottleneck category."""

    @property
    @abstractmethod
    def handles_category(self) -> str:
        """Return the recommendation category this template handles."""

    @abstractmethod
    def generate(
        self,
        recommendation_text: str,
        expected_gain_pct: float,
        confidence: float,
        project_root: Path,
    ) -> Optional[Experiment]:
        """Return an Experiment, or None if the target fragment cannot be found."""


def _read(project_root: Path, relative_path: str) -> Optional[str]:
    """Read file content, returning None if not found."""
    p = project_root / relative_path
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def _make_id() -> str:
    return f"exp-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


# ── Concrete templates ────────────────────────────────────────────────────────

class InterMissionDelayTemplate(ExperimentTemplate):
    """API rate pressure → increase inter-mission cool-down."""

    handles_category = "API"

    def generate(self, recommendation_text, expected_gain_pct, confidence, project_root):
        target = "benchmark/run_benchmark.py"
        src = _read(project_root, target)
        if src is None:
            return None
        old_frag = '"KRYTH_INTER_MISSION_DELAY", "15"'
        new_frag = '"KRYTH_INTER_MISSION_DELAY", "30"'
        if old_frag not in src:
            return None
        return Experiment(
            id=_make_id(),
            type="recovery_logic",
            category="API",
            hypothesis=(
                "Doubling the inter-mission delay (15s → 30s) reduces API rate-limit "
                "pressure, lowering retry count and total wall-clock time per run."
            ),
            description="Increase KRYTH_INTER_MISSION_DELAY default from 15s to 30s",
            changes=[FileChange(
                relative_path=target,
                old_fragment=old_frag,
                new_fragment=new_frag,
                description="Inter-mission delay default 15s → 30s",
            )],
            expected_gain_pct=expected_gain_pct,
            confidence=confidence,
            source_recommendation=recommendation_text,
        )


class ApiRetryDelayTemplate(ExperimentTemplate):
    """API retry pressure → increase backoff base."""

    handles_category = "API"

    def generate(self, recommendation_text, expected_gain_pct, confidence, project_root):
        target = "kryth/src/agent/agent_loop.py"
        src = _read(project_root, target)
        if src is None:
            return None
        old_frag = '"KRYTH_API_RETRY_DELAY", "20"'
        new_frag = '"KRYTH_API_RETRY_DELAY", "25"'
        if old_frag not in src:
            return None
        return Experiment(
            id=_make_id(),
            type="recovery_logic",
            category="API",
            hypothesis=(
                "Increasing the base retry delay (20s → 25s) gives the rate limiter "
                "more recovery time, reducing cascading failures."
            ),
            description="Increase KRYTH_API_RETRY_DELAY default from 20s to 25s",
            changes=[FileChange(
                relative_path=target,
                old_fragment=old_frag,
                new_fragment=new_frag,
                description="API retry base delay 20s → 25s",
            )],
            expected_gain_pct=expected_gain_pct,
            confidence=confidence,
            source_recommendation=recommendation_text,
        )


class ParallelNudgeTemplate(ExperimentTemplate):
    """Low parallel efficiency → add explicit batch-dispatch instruction to nudge."""

    handles_category = "PARALLEL"

    _OLD = (
        '"[system] You must call tools immediately. "'
        "\n                \"Do not write text — dispatch tool calls now. \""
        "\n                \"For a BUILD task: call todo_write then write_file for every file in parallel. \""
        "\n                \"For a FIX task: call read_file on the relevant file now.\""
    )
    _NEW = (
        '"[system] CRITICAL: You MUST call multiple tools in PARALLEL right now. "'
        "\n                \"Do not write any text. Do not describe your plan. Just dispatch tools. \""
        "\n                \"For a BUILD task: call write_file for ALL required files simultaneously in one batch. \""
        "\n                \"For a FIX task: call read_file on ALL relevant files simultaneously now.\""
    )

    def generate(self, recommendation_text, expected_gain_pct, confidence, project_root):
        target = "kryth/src/agent/agent_loop.py"
        src = _read(project_root, target)
        if src is None:
            return None
        # Search for the nudge message loosely
        if "You must call tools immediately" not in src:
            return None
        # Find the exact block we need to replace
        pattern = re.compile(
            r'"(\[system\] You must call tools immediately[^"]*)"'
            r'(\s+"Do not write text[^"]*")'
            r'(\s+"For a BUILD task[^"]*")'
            r'(\s+"For a FIX task[^"]*")',
        )
        m = pattern.search(src)
        if not m:
            return None
        old_frag = m.group(0)
        new_frag = (
            '"[system] CRITICAL: You MUST call multiple tools in PARALLEL right now. "'
            + m.group(2).replace("Do not write text — dispatch tool calls now.",
                                  "Do not write any text. Do not describe your plan. Just dispatch tools.")
            + m.group(3).replace(
                "For a BUILD task: call todo_write then write_file for every file in parallel.",
                "For a BUILD task: call write_file for ALL required files simultaneously in one batch.")
            + m.group(4).replace(
                "For a FIX task: call read_file on the relevant file now.",
                "For a FIX task: call read_file on ALL relevant files simultaneously now.")
        )
        return Experiment(
            id=_make_id(),
            type="prompt_optimization",
            category="PARALLEL",
            hypothesis=(
                "Strengthening the parallel tool dispatch nudge will increase the fraction "
                "of tool calls dispatched in parallel batches, reducing total mission time."
            ),
            description="Strengthen nudge message to emphasize parallel tool dispatch",
            changes=[FileChange(
                relative_path=target,
                old_fragment=old_frag,
                new_fragment=new_frag,
                description="Nudge message strengthened for parallel batching",
            )],
            expected_gain_pct=expected_gain_pct,
            confidence=confidence,
            source_recommendation=recommendation_text,
        )


class SilentTurnTemplate(ExperimentTemplate):
    """High silent turns → make the no-idle rule more explicit."""

    handles_category = "LLM"

    def generate(self, recommendation_text, expected_gain_pct, confidence, project_root):
        target = "kryth/src/agent/agent_loop.py"
        src = _read(project_root, target)
        if src is None:
            return None
        old_frag = '"Do not write text — dispatch tool calls now. "'
        new_frag = '"DO NOT write any text. IMMEDIATELY dispatch tool calls. Every turn must produce tool calls. "'
        if old_frag not in src:
            return None
        return Experiment(
            id=_make_id(),
            type="prompt_optimization",
            category="LLM",
            hypothesis=(
                "Making the no-idle rule more explicit will reduce silent turns "
                "(text responses with no tool calls), lowering dead-time per mission."
            ),
            description="Harden no-idle instruction in silent-turn nudge",
            changes=[FileChange(
                relative_path=target,
                old_fragment=old_frag,
                new_fragment=new_frag,
                description="No-idle nudge message made more explicit",
            )],
            expected_gain_pct=expected_gain_pct,
            confidence=confidence,
            source_recommendation=recommendation_text,
        )


class ContextCompactionTemplate(ExperimentTemplate):
    """Context bloat → trigger summarization 10% earlier."""

    handles_category = "LLM"

    def generate(self, recommendation_text, expected_gain_pct, confidence, project_root):
        target = "kryth/src/agent/agent_loop.py"
        src = _read(project_root, target)
        if src is None:
            return None
        # Look for the compaction threshold — common patterns
        for old_frag, new_frag, desc in [
            ("context_ratio > 0.85", "context_ratio > 0.75",
             "Context compaction threshold 85% → 75%"),
            ("tokens_ratio > 0.85", "tokens_ratio > 0.75",
             "Context compaction threshold 85% → 75%"),
            ('"context_ratio", 0.85', '"context_ratio", 0.75',
             "Context compaction threshold 85% → 75%"),
        ]:
            if old_frag in src:
                return Experiment(
                    id=_make_id(),
                    type="context_compression",
                    category="LLM",
                    hypothesis=(
                        "Triggering context compaction at 75% instead of 85% will "
                        "prevent context bloat from slowing down later turns."
                    ),
                    description="Lower context compaction trigger from 85% to 75%",
                    changes=[FileChange(
                        relative_path=target,
                        old_fragment=old_frag,
                        new_fragment=new_frag,
                        description=desc,
                    )],
                    expected_gain_pct=expected_gain_pct,
                    confidence=confidence,
                    source_recommendation=recommendation_text,
                )
        return None


class MemoryPreloadTemplate(ExperimentTemplate):
    """Memory subsystems inactive → expand speculative preload keywords."""

    handles_category = "MEMORY"

    def generate(self, recommendation_text, expected_gain_pct, confidence, project_root):
        target = "kryth/src/agent/orchestration/__init__.py"
        src = _read(project_root, target)
        if src is None:
            return None
        # Find a keyword list to extend
        old_frag = '"auth"'
        if old_frag not in src:
            return None
        new_frag = '"auth", "oauth", "jwt", "session", "token", "login", "signup"'
        if new_frag in src:
            return None  # already expanded
        return Experiment(
            id=_make_id(),
            type="memory_preload",
            category="MEMORY",
            hypothesis=(
                "Adding domain-specific auth/session keywords to speculative preload "
                "will increase preload hit rate, reducing per-turn file read latency."
            ),
            description="Expand speculative preload keyword set with auth/session terms",
            changes=[FileChange(
                relative_path=target,
                old_fragment=old_frag,
                new_fragment=new_frag,
                description="Added oauth/jwt/session/token keywords to preload list",
            )],
            expected_gain_pct=expected_gain_pct,
            confidence=confidence,
            source_recommendation=recommendation_text,
        )


class WorkerPoolTemplate(ExperimentTemplate):
    """Worker under-provisioning → lower complexity threshold for multi-agent spawn."""

    handles_category = "WORKER"

    def generate(self, recommendation_text, expected_gain_pct, confidence, project_root):
        target = "kryth/src/agent/orchestration/team_scaler.py"
        src = _read(project_root, target)
        if src is None:
            return None
        # Typical pattern: files_written_threshold = 10 or similar
        for old_frag, new_frag, desc in [
            ("files_threshold = 10", "files_threshold = 7",
             "Multi-agent spawn threshold files_written 10 → 7"),
            ("MIN_FILES_FOR_SPLIT = 10", "MIN_FILES_FOR_SPLIT = 7",
             "Multi-agent spawn threshold 10 → 7"),
            ('"files_written", 10', '"files_written", 7',
             "Multi-agent spawn files_written threshold 10 → 7"),
        ]:
            if old_frag in src:
                return Experiment(
                    id=_make_id(),
                    type="worker_pool",
                    category="WORKER",
                    hypothesis=(
                        "Lowering the multi-agent spawn threshold will allow "
                        "medium-complexity missions to use parallel sub-agents, "
                        "reducing total mission time."
                    ),
                    description="Lower multi-agent spawn threshold for earlier parallelism",
                    changes=[FileChange(
                        relative_path=target,
                        old_fragment=old_frag,
                        new_fragment=new_frag,
                        description=desc,
                    )],
                    expected_gain_pct=expected_gain_pct,
                    confidence=confidence,
                    source_recommendation=recommendation_text,
                )
        return None


# ── Generator ─────────────────────────────────────────────────────────────────

_TEMPLATES: list[ExperimentTemplate] = [
    InterMissionDelayTemplate(),
    ApiRetryDelayTemplate(),
    ParallelNudgeTemplate(),
    SilentTurnTemplate(),
    ContextCompactionTemplate(),
    MemoryPreloadTemplate(),
    WorkerPoolTemplate(),
]

# Map category → ordered list of templates to try
_CATEGORY_REGISTRY: dict[str, list[ExperimentTemplate]] = {}
for _t in _TEMPLATES:
    _CATEGORY_REGISTRY.setdefault(_t.handles_category, []).append(_t)


class ExperimentGenerator:
    """Translates the top optimization recommendation into an Experiment."""

    def generate(
        self,
        recommendations: list,   # list[Recommendation] from recommendation_engine
        project_root: str | Path,
    ) -> Optional[Experiment]:
        """Return the first viable Experiment, or None if no template applies."""
        root = Path(project_root)
        for rec in recommendations:
            templates = _CATEGORY_REGISTRY.get(rec.category, [])
            for template in templates:
                try:
                    exp = template.generate(rec.text, rec.expected_gain_pct, rec.confidence, root)
                    if exp is not None:
                        return exp
                except Exception:
                    continue
        return None
