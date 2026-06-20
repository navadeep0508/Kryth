"""MissionDigitalTwin — V5 Phase 5.

Tracks the divergence between the planned mission state and actual execution.

  Planned State   vs   Actual State
  ─────────────────────────────────
  Deliverables         Deliverables produced
  Files                Files on disk
  Tests                Tests executed
  Progress %           Actual progress %
  Milestones           Milestones completed

Provides:
  * Gap Analysis   — what is planned but missing
  * File Diff      — expected files not yet created
  * Test Coverage  — expected tests not yet run
  * Progress Delta — planned% vs actual%

The twin is the mission's ground truth — it overrides optimistic self-reports
from workers.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class DeliverableGap:
    name: str
    module: str
    status: str     # "missing" | "partial" | "present"
    evidence: str = ""


@dataclass
class FileGap:
    path: str
    module: str
    exists: bool
    size_bytes: int = 0


@dataclass
class TwinSnapshot:
    """Point-in-time snapshot of planned vs actual."""
    # Deliverables
    planned_deliverables: int = 0
    actual_deliverables: int  = 0
    deliverable_gaps: List[DeliverableGap] = field(default_factory=list)

    # Files
    planned_files: int = 0
    actual_files: int  = 0
    file_gaps: List[FileGap] = field(default_factory=list)

    # Milestones
    planned_milestones: int = 0
    actual_milestones: int  = 0

    # Progress
    planned_progress_pct: float = 0.0
    actual_progress_pct: float  = 0.0

    @property
    def deliverable_gap_count(self) -> int:
        return sum(1 for g in self.deliverable_gaps if g.status == "missing")

    @property
    def file_gap_count(self) -> int:
        return sum(1 for f in self.file_gaps if not f.exists)

    @property
    def progress_delta(self) -> float:
        return self.actual_progress_pct - self.planned_progress_pct

    def gap_report(self) -> str:
        lines = [
            "═══ DIGITAL TWIN GAP ANALYSIS ═══",
            f"  Deliverables: {self.actual_deliverables}/{self.planned_deliverables}  "
            f"({self.deliverable_gap_count} missing)",
            f"  Files:        {self.actual_files}/{self.planned_files}  "
            f"({self.file_gap_count} missing)",
            f"  Milestones:   {self.actual_milestones}/{self.planned_milestones}",
            f"  Progress:     planned {self.planned_progress_pct:.0f}%  "
            f"actual {self.actual_progress_pct:.0f}%  "
            f"(delta {self.progress_delta:+.0f}%)",
        ]
        if self.file_gaps:
            lines.append("  Missing files:")
            for fg in self.file_gaps[:6]:
                if not fg.exists:
                    lines.append(f"    ✗ {fg.path}  [{fg.module}]")
        if self.deliverable_gap_count:
            lines.append("  Missing deliverables:")
            for dg in self.deliverable_gaps[:6]:
                if dg.status == "missing":
                    lines.append(f"    ✗ {dg.name}  [{dg.module}]")
        lines.append("═══════════════════════════════")
        return "\n".join(lines)


class MissionDigitalTwin:
    """Maintains a live digital twin of a mission.

    Updated incrementally as milestones complete.
    """

    def __init__(self, plan) -> None:  # plan: ProjectPlan
        self._plan = plan
        self._actual_deliverables: Dict[str, List[str]] = {}   # module → list of produced deliverables
        self._actual_files: Set[str] = set()
        self._completed_milestones: Set[str] = set()
        self._project_root: str = "."

    def set_project_root(self, root: str) -> None:
        self._project_root = root

    # ── Update methods ────────────────────────────────────────────────────────

    def record_deliverables(self, module_name: str, deliverables: List[str]) -> None:
        existing = self._actual_deliverables.get(module_name, [])
        self._actual_deliverables[module_name] = list(set(existing) | set(deliverables))

    def record_milestone_complete(self, milestone_name: str) -> None:
        self._completed_milestones.add(milestone_name)

    def scan_files(self, expected_files: Optional[List[str]] = None) -> None:
        """Scan project root for expected files; update actual file set."""
        if not expected_files or self._project_root == ".":
            return
        for fpath in expected_files:
            full = os.path.join(self._project_root, fpath.lstrip("/\\"))
            if os.path.exists(full):
                self._actual_files.add(fpath)

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> TwinSnapshot:
        """Compute current planned-vs-actual snapshot."""
        plan = self._plan

        # Planned deliverables
        planned_delivs: List[Tuple[str, str]] = []   # (module, deliverable)
        planned_files_list: List[Tuple[str, str]] = []  # (module, filepath)
        for m in plan.modules:
            for d in m.deliverables:
                planned_delivs.append((m.name, d))
            for f in m.files_owned:
                if not f.endswith("/"):
                    planned_files_list.append((m.name, f))

        # Actual deliverables
        actual_deliv_set: Set[str] = set()
        for delivs in self._actual_deliverables.values():
            actual_deliv_set.update(delivs)

        # Deliverable gaps
        deliv_gaps: List[DeliverableGap] = []
        for mod_name, deliv_name in planned_delivs:
            status = "present" if deliv_name in actual_deliv_set else "missing"
            deliv_gaps.append(DeliverableGap(
                name=deliv_name, module=mod_name, status=status
            ))

        # Scan files if root is set
        if self._project_root != ".":
            self.scan_files([fp for _, fp in planned_files_list])

        file_gaps: List[FileGap] = []
        for mod_name, fpath in planned_files_list:
            exists = fpath in self._actual_files or os.path.exists(
                os.path.join(self._project_root, fpath.lstrip("/\\"))
            )
            full = os.path.join(self._project_root, fpath.lstrip("/\\"))
            size = os.path.getsize(full) if exists and os.path.isfile(full) else 0
            file_gaps.append(FileGap(
                path=fpath, module=mod_name, exists=exists, size_bytes=size
            ))

        # Progress
        total_ms   = len(plan.structured_milestones or plan.ensure_structured_milestones())
        done_ms    = len(self._completed_milestones)
        total_delivs_n = len(planned_delivs)
        done_delivs_n  = sum(1 for dg in deliv_gaps if dg.status == "present")

        planned_pct = 100.0 * done_ms / total_ms if total_ms else 0.0
        actual_pct  = 100.0 * done_delivs_n / total_delivs_n if total_delivs_n else 0.0

        return TwinSnapshot(
            planned_deliverables=total_delivs_n,
            actual_deliverables=done_delivs_n,
            deliverable_gaps=deliv_gaps,
            planned_files=len(planned_files_list),
            actual_files=sum(1 for fg in file_gaps if fg.exists),
            file_gaps=file_gaps,
            planned_milestones=total_ms,
            actual_milestones=done_ms,
            planned_progress_pct=planned_pct,
            actual_progress_pct=actual_pct,
        )
