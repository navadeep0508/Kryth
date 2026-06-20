"""Ponytail — lazy senior dev execution philosophy (V5 Worker Execution Layer).

Source: https://github.com/DietrichGebert/ponytail  (MIT)

  "He says nothing. He writes one line. It works."

Ponytail is NOT an orchestration system. It changes nothing about the
Planner, DAG, MilestoneEngine, Scheduler, Team Leads, Recovery, or the
Organizational Runtime. It is a worker execution philosophy: a ruleset
injected into worker system prompts, plus lightweight heuristics that
measure whether workers actually followed it.

The ladder (stop at the first rung that holds):
    1. Does this need to exist at all?      (YAGNI)
    2. Does the standard library do this?   → use it
    3. Does a native platform feature do this? → use it
    4. Does an already-installed dependency do this? → use it
    5. Can this be one line?                → make it one line
    6. Only then: write the minimum code that works.

Not lazy about: trust-boundary validation, data-loss handling, security,
accessibility, anything explicitly requested.

This module provides:
  * PONYTAIL_RULES        — the worker contract text (Phase 2)
  * file_creation_guard()  — should a new file really be created? (Phase 3)
  * dependency_reuse_hints() — known framework/stdlib shortcuts (Phase 4)
  * AbstractionDetector    — flags wrapper/pass-through/single-use code (Phase 5)
  * OverengineeringScore   — 0-100, lower is better (Phase 6)
  * classify_task_for_ponytail() — planner-facing NORMAL/PONYTAIL decision (Phase 8)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ── Phase 2: Worker contract ──────────────────────────────────────────────────

PONYTAIL_RULES = """PONYTAIL: minimum code. Before writing, check: stdlib? framework feature? existing dep? one line? Only then write minimum that works. No unnecessary abstractions, wrappers, or new files. Boring over clever."""


def inject_ponytail_contract(base_prompt: str) -> str:
    """Append the Ponytail ruleset to a worker's system prompt."""
    return f"{base_prompt}\n\n{PONYTAIL_RULES}"


# ── Phase 3: File creation guard ──────────────────────────────────────────────

@dataclass
class FileCreationDecision:
    requested_path: str
    should_create: bool
    reason: str
    existing_candidate: Optional[str] = None


@dataclass
class FileGuardLedger:
    """Tracks file creation decisions across a worker's run for reporting."""
    files_created: List[str] = field(default_factory=list)
    files_reused: List[str] = field(default_factory=list)
    files_avoided: List[str] = field(default_factory=list)   # creation requested but redirected to existing file

    def record(self, decision: FileCreationDecision) -> None:
        if decision.should_create:
            self.files_created.append(decision.requested_path)
        elif decision.existing_candidate:
            self.files_avoided.append(decision.requested_path)
            self.files_reused.append(decision.existing_candidate)
        else:
            self.files_avoided.append(decision.requested_path)


# Heuristics for "this new file probably duplicates an existing one"
_SIMILAR_SUFFIXES = (
    "_helper", "_helpers", "_util", "_utils", "_service", "_wrapper",
    "_manager", "_handler", "_provider",
)


def _basename_stem(path: str) -> str:
    base = os.path.basename(path)
    stem = base.rsplit(".", 1)[0]
    return stem.lower()


def file_creation_guard(
    requested_path: str,
    project_root: str = ".",
    existing_files: Optional[List[str]] = None,
) -> FileCreationDecision:
    """Decide whether a new file is actually warranted.

    Checks, in order:
      1. Does the exact file already exist? → reuse it, don't "create"
      2. Is there a same-directory file with a near-identical stem
         (e.g. requesting `auth_helper.py` when `auth.py` exists)? → modify that instead
      3. Otherwise → creation is allowed

    `existing_files` lets callers pass a pre-scanned file list (avoids a
    filesystem walk per call); falls back to a direct existence check.
    """
    norm = requested_path.replace("\\", "/").lstrip("/")
    full = os.path.join(project_root, norm)

    if os.path.exists(full):
        return FileCreationDecision(
            requested_path=requested_path,
            should_create=False,
            reason="File already exists — modify it instead of recreating.",
            existing_candidate=requested_path,
        )

    stem = _basename_stem(norm)
    directory = os.path.dirname(norm)
    candidates = existing_files
    if candidates is None and project_root != "." and os.path.isdir(os.path.join(project_root, directory) or project_root):
        try:
            search_dir = os.path.join(project_root, directory) if directory else project_root
            candidates = [
                os.path.join(directory, f) if directory else f
                for f in os.listdir(search_dir)
                if os.path.isfile(os.path.join(search_dir, f))
            ]
        except OSError:
            candidates = []
    candidates = candidates or []

    # Strip helper/util/service/wrapper/manager suffixes to find the "real" stem
    core_stem = stem
    for suffix in _SIMILAR_SUFFIXES:
        if core_stem.endswith(suffix):
            core_stem = core_stem[: -len(suffix)]
            break

    if core_stem and core_stem != stem:
        for cand in candidates:
            cand_stem = _basename_stem(cand)
            if cand_stem == core_stem:
                return FileCreationDecision(
                    requested_path=requested_path,
                    should_create=False,
                    reason=(
                        f"'{requested_path}' looks like a single-purpose "
                        f"{stem[len(core_stem):]} for '{cand}' — extend that file instead."
                    ),
                    existing_candidate=cand,
                )

    return FileCreationDecision(
        requested_path=requested_path,
        should_create=True,
        reason="No existing file covers this — creation is warranted.",
    )


# ── Phase 4: Dependency reuse engine ──────────────────────────────────────────

# Known shortcuts: capability keyword → (what to use instead, example)
DEPENDENCY_REUSE_MAP: Dict[str, Tuple[str, str]] = {
    "date picker":       ("native <input type=\"date\">",            "browser has one"),
    "debounce":          ("lodash.debounce (if lodash installed) or a 5-line setTimeout", "stdlib-adjacent"),
    "uuid":              ("crypto.randomUUID() / Python uuid module", "stdlib"),
    "validation":        ("Pydantic / Zod / framework validators",    "framework feature"),
    "routing":           ("Next.js file-based routing / FastAPI APIRouter", "framework feature"),
    "authentication":    ("framework's built-in auth (Django auth, NextAuth, FastAPI security)", "framework feature"),
    "dependency injection": ("FastAPI `Depends`",                     "framework feature"),
    "state management":  ("React hooks (useState/useReducer/useContext)", "framework feature"),
    "http client":       ("fetch / requests (already a dependency in most stacks)", "stdlib/installed dep"),
    "password hashing":  ("passlib / bcrypt (commonly already installed)", "installed dep"),
    "csv parsing":       ("csv module (Python) / no library needed for simple cases", "stdlib"),
    "date formatting":   ("Intl.DateTimeFormat / datetime.strftime",  "stdlib"),
    "env config":        ("os.environ / process.env",                "stdlib"),
    "logging":           ("logging module / framework logger",       "stdlib"),
    "rate limiting":     ("framework middleware (e.g. slowapi for FastAPI)", "installed dep"),
    "email validation":  ("a single regex or the framework's EmailStr/EmailField", "one line"),
}


def dependency_reuse_hints(goal_text: str, tech_stack: Optional[Dict[str, str]] = None) -> List[str]:
    """Return relevant 'use this instead of building it' hints for a task goal."""
    low = (goal_text or "").lower()
    hints = []
    for keyword, (use_instead, tier) in DEPENDENCY_REUSE_MAP.items():
        if keyword in low:
            hints.append(f"{keyword} → {use_instead}  [{tier}]")
    return hints


# ── Phase 5: Abstraction detector ─────────────────────────────────────────────

@dataclass
class AbstractionFlag:
    kind: str             # "wrapper" | "pass_through" | "single_use_helper" | "redundant_interface"
    location: str
    detail: str
    can_inline: bool = True


_WRAPPER_CLASS_RE = re.compile(
    r"class\s+(\w+)\s*[:\(].*?\n(\s+)def\s+__init__.*?\n(?:\s+self\.\w+\s*=\s*\w+.*?\n)*\s+def\s+(\w+)\(self.*?\):\s*\n\s+return\s+self\.\w+\.\3\(",
    re.DOTALL,
)
_SINGLE_LINE_PASSTHROUGH_RE = re.compile(
    r"def\s+(\w+)\([^)]*\)\s*(?:->[^:]+)?:\s*\n\s+return\s+(\w+)\([^)]*\)\s*$",
    re.MULTILINE,
)


class AbstractionDetector:
    """Heuristically flags over-abstraction in worker-produced code.

    This is a static-text scan (no AST dependency required), intentionally
    conservative — false negatives are fine, false positives should be rare.
    """

    def scan(self, filename: str, source: str) -> List[AbstractionFlag]:
        flags: List[AbstractionFlag] = []

        # 1. Wrapper classes — __init__ stores one dependency, every method
        #    is a single `return self.x.method(...)` forward.
        for m in _WRAPPER_CLASS_RE.finditer(source):
            flags.append(AbstractionFlag(
                kind="wrapper",
                location=f"{filename}:{m.group(1)}",
                detail=f"Class '{m.group(1)}' appears to be a pure pass-through wrapper.",
            ))

        # 2. Single-use helper functions that are just a one-line passthrough
        for m in _SINGLE_LINE_PASSTHROUGH_RE.finditer(source):
            fn_name, target = m.group(1), m.group(2)
            if fn_name != target:    # ignore recursive/self calls
                flags.append(AbstractionFlag(
                    kind="pass_through",
                    location=f"{filename}:{fn_name}",
                    detail=f"Function '{fn_name}' only forwards to '{target}(...)' — call '{target}' directly.",
                ))

        # 3. "Single-use" helper: a private function (_prefixed) defined and
        #    called exactly once in the same file.
        for fn_match in re.finditer(r"^def\s+(_\w+)\(", source, re.MULTILINE):
            fn_name = fn_match.group(1)
            call_count = len(re.findall(rf"\b{re.escape(fn_name)}\(", source)) - 1  # minus its def
            if call_count == 1:
                flags.append(AbstractionFlag(
                    kind="single_use_helper",
                    location=f"{filename}:{fn_name}",
                    detail=f"'{fn_name}' is defined and called exactly once — consider inlining.",
                ))

        # 4. Interface/ABC with exactly one concrete implementation in the same file
        abc_classes = re.findall(r"class\s+(\w+)\(.*?(?:ABC|Protocol|metaclass=ABCMeta).*?\):", source)
        for cls in abc_classes:
            impls = re.findall(rf"class\s+\w+\({re.escape(cls)}\)", source)
            if len(impls) <= 1:
                flags.append(AbstractionFlag(
                    kind="redundant_interface",
                    location=f"{filename}:{cls}",
                    detail=f"Interface '{cls}' has only one implementation in this file — "
                           f"may not need the abstraction yet.",
                ))

        return flags

    def scan_files(self, files: Dict[str, str]) -> List[AbstractionFlag]:
        """Scan multiple files (path → source) and aggregate flags."""
        all_flags: List[AbstractionFlag] = []
        for path, source in files.items():
            all_flags.extend(self.scan(path, source))
        return all_flags


# ── Phase 6: Overengineering score ────────────────────────────────────────────

@dataclass
class OverengineeringFactors:
    files_created: int = 0
    abstractions_created: int = 0   # classes/interfaces introduced
    helpers_created: int = 0        # single-use helper functions
    services_created: int = 0       # *_service.py / *Service classes
    wrappers_created: int = 0       # pass-through wrappers
    dependencies_added: int = 0     # new packages added to requirements/package.json

    # Baselines for "expected" complexity for this task — set by caller from
    # the contract's deliverable count, so a 5-file CRUD module isn't flagged
    # just for needing 5 files.
    expected_files: int = 1
    expected_abstractions: int = 0


@dataclass
class OverengineeringScore:
    score: float                 # 0-100, lower is better
    factors: OverengineeringFactors
    complexity_added: List[str] = field(default_factory=list)
    complexity_avoided: List[str] = field(default_factory=list)

    def grade(self) -> str:
        if self.score < 15:
            return "lean"
        if self.score < 35:
            return "acceptable"
        if self.score < 60:
            return "bloated"
        return "overengineered"


def compute_overengineering_score(
    factors: OverengineeringFactors,
    *,
    files_avoided: int = 0,
    abstractions_avoided: int = 0,
    dependencies_reused: int = 0,
) -> OverengineeringScore:
    """Score 0-100 — penalizes complexity beyond what the task required.

    Each unit of "excess" (created beyond expected baseline) contributes
    weighted points; reuse/avoidance reduces nothing from the score itself
    (the score measures what WAS added) but is reported separately as the
    complexity that was avoided, which is the Ponytail payoff metric.
    """
    excess_files   = max(0, factors.files_created - factors.expected_files)
    excess_abs     = max(0, factors.abstractions_created - factors.expected_abstractions)

    raw = (
        excess_files * 8
        + excess_abs * 12
        + factors.helpers_created * 6
        + factors.services_created * 10
        + factors.wrappers_created * 15
        + factors.dependencies_added * 10
    )
    score = min(100.0, raw)

    added: List[str] = []
    if excess_files:
        added.append(f"{excess_files} extra file(s) beyond what the task required")
    if excess_abs:
        added.append(f"{excess_abs} new abstraction(s)")
    if factors.helpers_created:
        added.append(f"{factors.helpers_created} single-use helper(s)")
    if factors.services_created:
        added.append(f"{factors.services_created} service layer(s)")
    if factors.wrappers_created:
        added.append(f"{factors.wrappers_created} wrapper(s)")
    if factors.dependencies_added:
        added.append(f"{factors.dependencies_added} new dependency(ies)")

    avoided: List[str] = []
    if files_avoided:
        avoided.append(f"{files_avoided} file(s) avoided by reusing existing code")
    if abstractions_avoided:
        avoided.append(f"{abstractions_avoided} abstraction(s) avoided")
    if dependencies_reused:
        avoided.append(f"{dependencies_reused} dependency addition(s) avoided (reused existing)")

    return OverengineeringScore(
        score=score,
        factors=factors,
        complexity_added=added,
        complexity_avoided=avoided,
    )


# ── Phase 8: Planner-facing task classification ──────────────────────────────

_PONYTAIL_KEYWORDS = {
    "fix", "bug", "bugfix", "patch", "small", "tweak", "typo", "rename",
    "refactor", "cleanup", "simplify", "crud", "endpoint", "minor",
    "adjust", "update", "small feature",
}
_NORMAL_KEYWORDS = {
    "architecture", "redesign", "platform", "system design", "migrate",
    "rewrite", "scale", "distributed", "microservice", "infrastructure",
    "saas platform", "multi-tenant", "framework", "from scratch",
}


def classify_task_for_ponytail(
    user_input: str,
    module_count: int = 1,
    estimated_files: int = 0,
) -> Tuple[str, str]:
    """Planner-facing decision: 'ponytail' or 'normal'.

    Heuristic only — the Planner LLM call remains the source of truth when
    available; this is the additive fallback that requires no LLM round trip.
    Large/architectural plans (many modules, many files) bias toward NORMAL;
    small/contained asks bias toward PONYTAIL.
    """
    low = (user_input or "").lower()

    normal_hits   = sum(1 for k in _NORMAL_KEYWORDS if k in low)
    ponytail_hits = sum(1 for k in _PONYTAIL_KEYWORDS if k in low)

    if normal_hits > 0 and normal_hits >= ponytail_hits:
        return "normal", f"Architecture/scale language detected ({normal_hits} signal(s))"

    if module_count >= 5 or estimated_files >= 15:
        return "normal", f"Large plan ({module_count} modules, ~{estimated_files} files) — quality first"

    if ponytail_hits > 0:
        return "ponytail", f"Small/contained task language detected ({ponytail_hits} signal(s))"

    if module_count <= 2 and estimated_files <= 6:
        return "ponytail", f"Small plan ({module_count} modules, ~{estimated_files} files)"

    return "normal", "No strong signal — defaulting to standard execution"


def ponytail_enabled() -> bool:
    """Ponytail is ON by default for all direct / single-agent runs.

    The lazy-senior-dev philosophy reduces over-thinking and keeps the agent
    acting rather than deliberating.

    Disable:      KRYTH_PONYTAIL=0   or   KRYTH_EXEC_PROFILE=standard
    Force on:     KRYTH_PONYTAIL=1   or   KRYTH_EXEC_PROFILE=ponytail
    """
    from agent.env import getenv, getenv_bool
    profile = getenv("KRYTH_EXEC_PROFILE").strip().lower()
    if profile in ("standard", "full", "normal", "off"):
        return False
    if profile in ("ponytail", "lean", "lazy", "minimal"):
        return True
    # Default ON — direct/single-agent mode benefits most from this
    return getenv_bool("KRYTH_PONYTAIL", True)


# ── Phase 7: Team Lead simplicity review ──────────────────────────────────────
#
# Extends (does not replace) milestone_engine.team_lead_review(). Called
# additively, only when the active execution profile is PONYTAIL, as a
# second-pass check on top of the existing pass/fail decision. A worker can
# pass the normal Team Lead review and still be sent back for rework here if
# its output shows clear overengineering signals.

@dataclass
class PonytailReviewResult:
    approved: bool
    notes: str = ""
    questions: Dict[str, bool] = field(default_factory=dict)   # the 5 simplicity questions
    overengineering: Optional[OverengineeringScore] = None


_SIMPLICITY_QUESTIONS = (
    "Was this the simplest solution?",
    "Was a new file necessary?",
    "Was a new abstraction necessary?",
    "Was an existing dependency available?",
    "Was existing code reused?",
)


def ponytail_team_lead_review(
    worker_output: str,
    files_created: Optional[List[str]] = None,
    files_touched_source: Optional[Dict[str, str]] = None,   # path -> source, for abstraction scan
    expected_files: int = 1,
) -> PonytailReviewResult:
    """Second-pass Team Lead check, run only under the PONYTAIL profile.

    Asks the 5 simplicity questions and rejects unnecessary complexity.
    This NEVER overrides an already-failed base team_lead_review() — callers
    should only invoke this after the base review has APPROVED, as the final
    Ponytail-specific gate before Planner Review.
    """
    files_created = files_created or []
    files_touched_source = files_touched_source or {}

    detector = AbstractionDetector()
    flags = detector.scan_files(files_touched_source)

    factors = OverengineeringFactors(
        files_created=len(files_created),
        abstractions_created=sum(1 for f in flags if f.kind in ("wrapper", "redundant_interface")),
        helpers_created=sum(1 for f in flags if f.kind == "single_use_helper"),
        services_created=sum(1 for f in flags if f.kind == "wrapper" and "service" in f.location.lower()),
        wrappers_created=sum(1 for f in flags if f.kind == "wrapper"),
        expected_files=expected_files,
    )
    score = compute_overengineering_score(factors)

    questions = {
        "Was this the simplest solution?": score.score < 35,
        "Was a new file necessary?": len(files_created) <= expected_files,
        "Was a new abstraction necessary?": factors.abstractions_created == 0,
        "Was an existing dependency available?": True,   # can't verify statically — assume yes unless flagged
        "Was existing code reused?": "ponytail:" in (worker_output or "").lower() or len(files_created) <= expected_files,
    }

    # Reject only on a clear, high-confidence overengineering signal —
    # mirrors team_lead_review()'s lenient bar (the base gate already passed).
    if score.score >= 60:
        return PonytailReviewResult(
            approved=False,
            notes=(
                f"Overengineering score {score.score:.0f} ({score.grade()}): "
                + "; ".join(score.complexity_added[:3])
            ),
            questions=questions,
            overengineering=score,
        )

    return PonytailReviewResult(
        approved=True,
        notes=f"Overengineering score {score.score:.0f} ({score.grade()}) — within bounds",
        questions=questions,
        overengineering=score,
    )
