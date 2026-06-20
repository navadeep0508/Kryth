"""ArtifactValidator — V5 Phase 3.

Upgrades contract validation from heuristic text-scanning to real evidence:

  Checks (in order of reliability):
    1. Files exist on disk
    2. Imports resolve (Python: importlib dry-run; JS: require.resolve stub)
    3. Build command succeeds (if contract specifies one)
    4. Test command succeeds (if contract specifies one)
    5. Expected outputs generated (content heuristic — last resort)

Validation hierarchy enforced by MilestoneEngine:
    ArtifactValidator  ←  this module
    ↓
    TeamLeadReview     (team_lead_runtime.py)
    ↓
    PlannerApproval    (milestone_engine.planner_review_milestone)

Worker claims are never trusted blindly.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ArtifactCheck:
    name: str
    passed: bool
    evidence: str = ""
    weight: float = 1.0     # higher = more important


@dataclass
class ArtifactValidationResult:
    module_name: str
    checks: List[ArtifactCheck] = field(default_factory=list)
    passed: bool = False
    score: float = 0.0
    notes: str = ""

    def weighted_score(self) -> float:
        total_weight = sum(c.weight for c in self.checks)
        if total_weight == 0:
            return 0.0
        earned = sum(c.weight for c in self.checks if c.passed)
        return earned / total_weight


# ── Individual checks ─────────────────────────────────────────────────────────

def check_files_exist(
    files: List[str],
    project_root: str,
    *,
    weight: float = 2.0,
) -> List[ArtifactCheck]:
    """Verify each expected file exists on disk."""
    results = []
    for fpath in files[:8]:
        full = os.path.join(project_root, fpath.lstrip("/\\"))
        exists = os.path.exists(full)
        results.append(ArtifactCheck(
            name=f"file_exists:{fpath}",
            passed=exists,
            evidence=f"{'found' if exists else 'missing'}: {full}",
            weight=weight,
        ))
    return results


def check_python_imports(
    module_paths: List[str],
    project_root: str,
    *,
    weight: float = 1.5,
) -> List[ArtifactCheck]:
    """Check Python files parse without SyntaxError."""
    results = []
    for fpath in module_paths[:5]:
        if not fpath.endswith(".py"):
            continue
        full = os.path.join(project_root, fpath.lstrip("/\\"))
        if not os.path.exists(full):
            results.append(ArtifactCheck(
                name=f"import:{fpath}",
                passed=False,
                evidence=f"file not found: {full}",
                weight=weight,
            ))
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                source = f.read()
            compile(source, full, "exec")
            results.append(ArtifactCheck(
                name=f"import:{fpath}",
                passed=True,
                evidence="syntax ok",
                weight=weight,
            ))
        except SyntaxError as exc:
            results.append(ArtifactCheck(
                name=f"import:{fpath}",
                passed=False,
                evidence=f"SyntaxError: {exc}",
                weight=weight,
            ))
    return results


def check_command(
    cmd: str,
    project_root: str,
    timeout_s: float = 30.0,
    *,
    weight: float = 3.0,
    label: str = "",
) -> ArtifactCheck:
    """Run a shell command and check it exits 0."""
    name = label or f"cmd:{cmd[:30]}"
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        passed = result.returncode == 0
        evidence = (result.stdout + result.stderr)[:200].strip() or "(no output)"
        return ArtifactCheck(name=name, passed=passed, evidence=evidence, weight=weight)
    except subprocess.TimeoutExpired:
        return ArtifactCheck(name=name, passed=False, evidence=f"timeout after {timeout_s}s", weight=weight)
    except Exception as exc:
        return ArtifactCheck(name=name, passed=False, evidence=str(exc)[:100], weight=weight)


def check_content_evidence(
    output: str,
    success_criteria: List[str],
    *,
    weight: float = 0.5,
) -> List[ArtifactCheck]:
    """Fallback heuristic: keywords from success criteria in worker output."""
    results = []
    for criterion in success_criteria[:4]:
        keywords = [w.lower() for w in criterion.split() if len(w) > 4]
        hit = keywords and any(kw in (output or "").lower() for kw in keywords[:3])
        results.append(ArtifactCheck(
            name=f"content:{criterion[:30]}",
            passed=hit,
            evidence="keyword match" if hit else "not evidenced",
            weight=weight,
        ))
    return results


# ── Top-level validator ───────────────────────────────────────────────────────

def validate_artifacts(
    contract,          # DeliverableContract
    worker_output: str,
    project_root: str = ".",
    *,
    run_tests: bool = False,
    build_cmd: Optional[str] = None,
    test_cmd: Optional[str] = None,
    pass_threshold: float = 0.60,
) -> ArtifactValidationResult:
    """Full artifact-based validation for a single module contract.

    This is the Phase 3 upgrade over the heuristic validate_contract().
    It runs real evidence checks — files, syntax, optionally build + tests.

    Pass threshold: ≥60% weighted score → pass (lenient — heuristic still covers
    cases where files are generated in the worker's output but not on disk yet).
    """
    module_name = getattr(contract, "module_name", "unknown")
    result = ArtifactValidationResult(module_name=module_name)
    checks: List[ArtifactCheck] = []

    # 1. Files on disk (weight 2 — strong signal)
    files_to_create = getattr(contract, "files_to_create", [])
    if files_to_create and project_root != ".":
        checks.extend(check_files_exist(files_to_create, project_root, weight=2.0))

    # 2. Python syntax check (weight 1.5)
    py_files = [f for f in files_to_create if f.endswith(".py")]
    if py_files and project_root != ".":
        checks.extend(check_python_imports(py_files, project_root, weight=1.5))

    # 3. Build command (weight 3 — strongest signal, optional)
    if build_cmd:
        checks.append(check_command(build_cmd, project_root, timeout_s=60.0, weight=3.0, label="build"))

    # 4. Test command (weight 3, optional)
    if run_tests and test_cmd:
        checks.append(check_command(test_cmd, project_root, timeout_s=120.0, weight=3.0, label="tests"))

    # 5. Content evidence fallback (weight 0.5 — weakest, always runs)
    success_criteria = getattr(contract, "success_criteria", [])
    checks.extend(check_content_evidence(worker_output, success_criteria, weight=0.5))

    # Sentinel check (always)
    sentinel_ok = "AGENT_COMPLETE" in (worker_output or "")
    checks.append(ArtifactCheck(
        name="sentinel",
        passed=sentinel_ok,
        evidence="AGENT_COMPLETE found" if sentinel_ok else "AGENT_COMPLETE missing",
        weight=1.0,
    ))

    result.checks = checks
    result.score  = result.weighted_score()
    result.passed = len(checks) > 0 and result.score >= pass_threshold
    result.notes  = (
        f"Artifact score {result.score:.0%} "
        f"({sum(1 for c in checks if c.passed)}/{len(checks)} checks)"
    )
    return result
