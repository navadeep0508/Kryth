from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.reflection.experience_record import (
    ExperienceRecord, KnowledgeLesson, LessonKind, LessonScope,
    ReflectionInput, ReflectionOutput, ReflectionStatus,
)
from agent.reflection.extractor import KnowledgeExtractor, build_default_extractor
from agent.reflection.scoring_engine import ReflectionScoringEngine, ScoringContext, ScoringWeights
from agent.reflection.noise_filter import NoiseFilter

try:
    from agent.memory.approval import ask_approval_if_needed
except ImportError:
    def ask_approval_if_needed(*a, **kw):  # type: ignore[misc]
        class _R:
            approved = True
        return _R()

try:
    from agent.memory.memory_manager import (
        get_memory_manager,
        LAYER_EXECUTION,
        LAYER_PROJECT,
        LAYER_LONG_TERM,
        LAYER_WORKFLOW,
    )
except ImportError:
    get_memory_manager = None  # type: ignore[assignment]
    LAYER_EXECUTION = "execution"
    LAYER_PROJECT = "project"
    LAYER_LONG_TERM = "long_term"
    LAYER_WORKFLOW = "workflow"

try:
    from agent.memory.knowledge_graph import get_knowledge_graph
except ImportError:
    get_knowledge_graph = None  # type: ignore[assignment]


@dataclass
class PipelineConfig:
    weights: Optional[ScoringWeights] = None
    promotion_threshold: float = 0.60
    approval_threshold: float = 0.72

    min_noise_score: float = 0.30
    max_value_size: int = 2000

    use_memory_manager: bool = True
    use_knowledge_graph: bool = True
    persist_experience: bool = True

    auto_approve_below: float = 0.60
    require_approval_above: float = 0.72

    ui_callback: Optional[Callable] = field(default=None, repr=False)


class ReflectionPipeline:
    """
    Orchestrates the full reflection pipeline.

    Steps:
    1. Extract lessons via all registered reflectors
    2. Score every lesson (composite_score, novelty, reuse_potential)
    3. Filter noise
    4. Route to approval gate (auto/human based on config)
    5. Store approved lessons via MemoryManager
    6. Update KnowledgeGraph with file/symbol nodes for changed files
    7. Persist ExperienceRecord to SQLite
    8. Update analytics
    """

    def __init__(
        self,
        project_hash: str = "",
        config: Optional[PipelineConfig] = None,
        extractor: Optional[KnowledgeExtractor] = None,
    ) -> None:
        self.project_hash = project_hash
        self.config = config or PipelineConfig()
        self.extractor = extractor or build_default_extractor(project_hash)
        self._scorer = ReflectionScoringEngine(weights=self.config.weights)
        self._db_path: Optional[Path] = self._resolve_db_path(project_hash)
        self._init_db()

    def run(self, inp: ReflectionInput) -> ReflectionOutput:
        """Run the complete reflection pipeline. Never raises — errors go to output.error."""
        t0 = time.monotonic()
        task_id = inp.task_id or f"task_{int(inp.started_at)}"

        try:
            # Step 1: Extract
            raw_lessons = self.extractor.extract_all(inp)

            # Step 2: Score
            ctx = self._build_scoring_context(inp)
            scored = self._scorer.score_all(raw_lessons, ctx)

            # Step 3: Filter
            noise_filter = NoiseFilter(
                min_score=self.config.min_noise_score,
                max_value_size=self.config.max_value_size,
            )
            kept, filtered_keys = noise_filter.filter(scored)

            # Step 4: Approval gate
            approved: List[KnowledgeLesson] = []
            promoted_keys: List[str] = []
            rejected_keys: List[str] = []

            for lesson in kept:
                if lesson.composite_score < self.config.promotion_threshold:
                    lesson.status = ReflectionStatus.STORED
                    approved.append(lesson)
                    continue

                if lesson.composite_score >= self.config.require_approval_above:
                    result = ask_approval_if_needed(
                        memory_key=lesson.key,
                        memory_value=lesson.value,
                        scope=lesson.scope.value,
                        reason=f"[{lesson.kind.value}] lesson from task: {inp.task_description[:80]}",
                        importance=lesson.importance,
                        project_hash=self.project_hash,
                        ui_callback=self.config.ui_callback,
                    )
                    if result.approved:
                        lesson.status = ReflectionStatus.APPROVED
                        approved.append(lesson)
                        promoted_keys.append(lesson.key)
                    else:
                        lesson.status = ReflectionStatus.REJECTED
                        rejected_keys.append(lesson.key)
                else:
                    lesson.scope = LessonScope.SESSION
                    lesson.status = ReflectionStatus.APPROVED
                    approved.append(lesson)
                    promoted_keys.append(lesson.key)

            # Step 5: Store via MemoryManager
            if self.config.use_memory_manager and get_memory_manager is not None:
                self._store_in_memory(approved, inp.project_hash or self.project_hash)

            # Step 6: Knowledge Graph
            if (
                self.config.use_knowledge_graph
                and get_knowledge_graph is not None
                and inp.file_changes
            ):
                self._update_graph(inp.file_changes, inp.project_hash or self.project_hash)

            # Step 7: Persist ExperienceRecord
            output = ReflectionOutput(
                task_id=task_id,
                task_summary=self._summarize(inp),
                lessons=approved,
                promoted_keys=promoted_keys,
                rejected_keys=rejected_keys,
                filtered_keys=filtered_keys,
                reflection_time_sec=time.monotonic() - t0,
            )
            if self.config.persist_experience:
                experience = ExperienceRecord.from_reflection_output(output, inp)
                self._persist_experience(experience)

            return output

        except Exception as exc:
            return ReflectionOutput(
                task_id=task_id,
                task_summary=f"Reflection failed: {exc}",
                reflection_time_sec=time.monotonic() - t0,
                error=str(exc),
            )

    def _build_scoring_context(self, inp: ReflectionInput) -> ScoringContext:
        """Pull existing keys from MemoryManager for novelty calculation."""
        existing: set = set()
        if self.config.use_memory_manager and get_memory_manager is not None:
            try:
                mgr = get_memory_manager(project_hash=self.project_hash)
                existing = {r["key"] for r in mgr.search("", limit=200)}
            except Exception:
                pass
        return ScoringContext(
            existing_keys=existing,
            task_category=_infer_task_category(inp.task_description),
        )

    def _store_in_memory(self, lessons: List[KnowledgeLesson], project_hash: str) -> None:
        """Store approved lessons via MemoryManager, routing by scope."""
        try:
            mgr = get_memory_manager(project_hash=project_hash)
        except Exception:
            return

        scope_map = {
            LessonScope.EXECUTION: LAYER_EXECUTION,
            LessonScope.WORKFLOW:  LAYER_WORKFLOW,
            LessonScope.PROJECT:   LAYER_PROJECT,
            LessonScope.LONG_TERM: LAYER_LONG_TERM,
        }

        for lesson in lessons:
            try:
                layer = scope_map.get(lesson.scope, LAYER_EXECUTION)
                mgr.store(
                    key=lesson.key,
                    value=lesson.value,
                    scope=layer,
                    importance=lesson.importance,
                    metadata={"kind": lesson.kind.value, "source": lesson.source},
                )
                lesson.status = ReflectionStatus.STORED
            except Exception:
                pass

    def _update_graph(self, file_changes: List[Dict[str, Any]], project_hash: str) -> None:
        """Add file nodes and modification edges to the knowledge graph."""
        try:
            graph = get_knowledge_graph(project_hash=project_hash)
        except Exception:
            return

        for change in file_changes:
            path = change.get("path", "")
            if not path:
                continue
            node_id = f"file:{path}"
            graph.add_node(
                node_id=node_id,
                label=path.split("/")[-1] or path,
                node_type="file",
                metadata={
                    "lines_added": change.get("lines_added", 0),
                    "lines_removed": change.get("lines_removed", 0),
                    "operation": change.get("operation", "edit"),
                },
            )

    def _summarize(self, inp: ReflectionInput) -> str:
        """One-sentence rule-based task summary."""
        writes  = sum(1 for c in inp.tool_calls if c.get("success") and _is_write(c.get("tool", "")))
        cmds    = sum(1 for c in inp.tool_calls if c.get("success") and _is_cmd(c.get("tool", "")))
        repairs = len(inp.repair_events)
        total   = len(inp.tool_calls)
        failed  = inp.failed_tool_calls
        desc    = (inp.task_description[:60] + "…") if len(inp.task_description) > 60 else inp.task_description

        parts = [f"Completed '{desc}'"]
        acts: List[str] = []
        if writes:  acts.append(f"{writes} file write{'s' if writes != 1 else ''}")
        if cmds:    acts.append(f"{cmds} command{'s' if cmds != 1 else ''}")
        if repairs: acts.append(f"{repairs} repair{'s' if repairs != 1 else ''}")
        if acts:    parts.append("via " + ", ".join(acts))
        if failed:  parts.append(f"({failed} of {total} tool calls failed)")
        return " ".join(parts) + "."

    # ---- Experience persistence ----

    def _resolve_db_path(self, project_hash: str) -> Optional[Path]:
        try:
            from agent.env import home_dir
            d = home_dir() / "reflection"
            d.mkdir(parents=True, exist_ok=True)
            return d / f"{project_hash or 'global'}.db"
        except Exception:
            return None

    def _init_db(self) -> None:
        if not self._db_path:
            return
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.executescript("""
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS experiences (
                        record_id TEXT PRIMARY KEY,
                        task_description TEXT,
                        task_id TEXT,
                        project_hash TEXT,
                        outcome TEXT,
                        task_category TEXT,
                        duration_sec REAL,
                        total_tool_calls INTEGER,
                        repair_count INTEGER,
                        file_change_count INTEGER,
                        lines_added INTEGER,
                        lines_removed INTEGER,
                        lesson_count INTEGER,
                        content_hash TEXT,
                        created_at REAL,
                        data_json TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_exp_project  ON experiences(project_hash);
                    CREATE INDEX IF NOT EXISTS idx_exp_outcome  ON experiences(outcome);
                    CREATE INDEX IF NOT EXISTS idx_exp_category ON experiences(task_category);
                    CREATE INDEX IF NOT EXISTS idx_exp_hash     ON experiences(content_hash);
                """)
        except Exception:
            self._db_path = None

    def _persist_experience(self, exp: ExperienceRecord) -> None:
        if not self._db_path:
            return
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO experiences VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        exp.record_id, exp.task_description[:500], exp.task_id,
                        exp.project_hash, exp.outcome, exp.task_category,
                        exp.duration_sec, exp.total_tool_calls, exp.repair_count,
                        exp.file_change_count, exp.lines_added, exp.lines_removed,
                        len(exp.lessons), exp.content_hash, exp.created_at,
                        json.dumps(exp.to_dict(), default=str),
                    ),
                )
        except Exception:
            pass

    def query_experiences(
        self,
        outcome: Optional[str] = None,
        task_category: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Query stored ExperienceRecords for the future Experience Engine."""
        if not self._db_path:
            return []
        try:
            clauses: List[str] = []
            params: List[Any] = []
            if outcome:
                clauses.append("outcome = ?")
                params.append(outcome)
            if task_category:
                clauses.append("task_category = ?")
                params.append(task_category)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"SELECT data_json FROM experiences {where} ORDER BY created_at DESC LIMIT ?",
                    params,
                ).fetchall()
            return [json.loads(r["data_json"]) for r in rows]
        except Exception:
            return []


# ---- Module-level helpers ----

def _is_write(tool: str) -> bool:
    return tool in {"write_file", "create_file", "edit_file", "write", "create", "edit", "patch"}


def _is_cmd(tool: str) -> bool:
    return tool in {"bash", "run_command", "execute", "shell", "terminal", "exec", "run"}


def _infer_task_category(description: str) -> str:
    d = description.lower()
    if any(w in d for w in ("bug", "fix", "error", "broken", "fail", "crash")):
        return "bug_fix"
    if any(w in d for w in ("add", "implement", "create", "build", "new feature")):
        return "feature"
    if any(w in d for w in ("refactor", "clean", "reorganize", "restructure")):
        return "refactor"
    if any(w in d for w in ("test", "spec", "coverage")):
        return "testing"
    if any(w in d for w in ("deploy", "release", "ship", "publish")):
        return "deploy"
    if any(w in d for w in ("optimize", "performance", "speed", "slow")):
        return "optimization"
    if any(w in d for w in ("document", "docs", "readme", "comment")):
        return "documentation"
    return "general"
