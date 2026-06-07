from __future__ import annotations
import time, uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum


class LessonScope(str, Enum):
    WORKING   = "working"
    SESSION   = "session"
    PROJECT   = "project"
    EXECUTION = "execution"
    WORKFLOW  = "workflow"
    LONG_TERM = "long_term"
    ARCHIVE   = "archive"


class LessonKind(str, Enum):
    COMMAND     = "command"
    REPAIR      = "repair"
    WORKFLOW    = "workflow"
    TEAM        = "team"
    DECISION    = "decision"
    FILE_CHANGE = "file_change"
    COMPLEXITY  = "complexity"
    USER_FACT   = "user_fact"
    BUILD       = "build"
    DEPLOY      = "deploy"
    TEST        = "test"
    SECURITY    = "security"
    PERFORMANCE = "performance"


class ReflectionStatus(str, Enum):
    PENDING  = "pending"
    SCORED   = "scored"
    FILTERED = "filtered"
    APPROVED = "approved"
    REJECTED = "rejected"
    STORED   = "stored"
    ARCHIVED = "archived"


@dataclass
class KnowledgeLesson:
    key: str
    kind: LessonKind
    scope: LessonScope
    value: Any
    importance: float = 0.5
    confidence: float = 0.5
    novelty: float = 0.5
    reuse_potential: float = 0.5
    success_rate: float = 0.5
    source: str = ""
    lesson_id: str = field(default_factory=lambda: f"lesson_{uuid.uuid4().hex[:8]}")
    created_at: float = field(default_factory=time.time)
    ttl: Optional[float] = None
    project_hash: str = ""
    agent_id: str = ""
    status: ReflectionStatus = ReflectionStatus.PENDING
    composite_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "kind": self.kind.value, "scope": self.scope.value,
            "value": self.value, "importance": self.importance,
            "confidence": self.confidence, "novelty": self.novelty,
            "reuse_potential": self.reuse_potential, "success_rate": self.success_rate,
            "source": self.source, "lesson_id": self.lesson_id,
            "created_at": self.created_at, "ttl": self.ttl,
            "project_hash": self.project_hash, "agent_id": self.agent_id,
            "status": self.status.value, "composite_score": self.composite_score,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KnowledgeLesson":
        d = dict(d)
        d["kind"] = LessonKind(d["kind"])
        d["scope"] = LessonScope(d["scope"])
        d["status"] = ReflectionStatus(d.get("status", "pending"))
        return cls(**d)


@dataclass
class ReflectionInput:
    task_description: str
    task_id: str = ""
    project_hash: str = ""
    agent_id: str = ""

    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    session_messages: List[Dict[str, Any]] = field(default_factory=list)
    final_output: str = ""

    # {command, exit_code, output, duration_sec, parsed_errors?}
    terminal_events: List[Dict[str, Any]] = field(default_factory=list)

    # {error_type, error_message, repair_command, success, attempts}
    repair_events: List[Dict[str, Any]] = field(default_factory=list)

    # {task_id, name, status, affected_files, risk_level, duration_sec}
    dag_tasks: List[Dict[str, Any]] = field(default_factory=list)

    # {agent_id, role, tasks_completed, tasks_failed, turns_used}
    team_events: List[Dict[str, Any]] = field(default_factory=list)

    # {title, choice, rationale, alternatives}
    decisions: List[Dict[str, Any]] = field(default_factory=list)

    # {path, lines_added, lines_removed, operation}
    file_changes: List[Dict[str, Any]] = field(default_factory=list)

    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None

    @property
    def duration_sec(self) -> float:
        if self.ended_at:
            return self.ended_at - self.started_at
        return 0.0

    @property
    def total_tool_calls(self) -> int:
        return len(self.tool_calls)

    @property
    def failed_tool_calls(self) -> int:
        return sum(1 for c in self.tool_calls if not c.get("success", True))


@dataclass
class ReflectionOutput:
    task_id: str
    task_summary: str
    lessons: List[KnowledgeLesson] = field(default_factory=list)
    promoted_keys: List[str] = field(default_factory=list)
    rejected_keys: List[str] = field(default_factory=list)
    filtered_keys: List[str] = field(default_factory=list)
    reflection_time_sec: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def lesson_count(self) -> int:
        return len(self.lessons)

    @property
    def promoted_count(self) -> int:
        return len(self.promoted_keys)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_summary": self.task_summary,
            "lessons": [l.to_dict() for l in self.lessons],
            "promoted_keys": self.promoted_keys,
            "rejected_keys": self.rejected_keys,
            "filtered_keys": self.filtered_keys,
            "reflection_time_sec": self.reflection_time_sec,
            "error": self.error,
            "metadata": self.metadata,
        }


@dataclass
class ExperienceRecord:
    """Structured record for the future Experience Engine to query."""
    record_id: str = field(default_factory=lambda: f"exp_{uuid.uuid4().hex[:12]}")
    task_description: str = ""
    task_id: str = ""
    project_hash: str = ""
    agent_ids: List[str] = field(default_factory=list)

    task_category: str = ""
    technologies: List[str] = field(default_factory=list)
    outcome: str = ""

    lessons: List[KnowledgeLesson] = field(default_factory=list)
    repair_patterns: List[Dict[str, Any]] = field(default_factory=list)
    workflow_steps: List[str] = field(default_factory=list)
    team_composition: List[str] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)

    duration_sec: float = 0.0
    total_tool_calls: int = 0
    repair_count: int = 0
    file_change_count: int = 0
    lines_added: int = 0
    lines_removed: int = 0

    content_hash: str = ""

    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "lessons"}
        d["lessons"] = [l.to_dict() for l in self.lessons]
        return d

    @classmethod
    def from_reflection_output(cls, output: ReflectionOutput, inp: ReflectionInput) -> "ExperienceRecord":
        """Build an ExperienceRecord from a completed reflection."""
        lines_added = sum(f.get("lines_added", 0) for f in inp.file_changes)
        lines_removed = sum(f.get("lines_removed", 0) for f in inp.file_changes)
        team = [t.get("agent_id", t.get("role", "")) for t in inp.team_events]
        decisions = inp.decisions or []

        repair_lessons = [l for l in output.lessons if l.kind == LessonKind.REPAIR]
        repair_patterns = [l.value for l in repair_lessons if isinstance(l.value, dict)]

        workflow_lessons = [l for l in output.lessons if l.kind == LessonKind.WORKFLOW]
        steps = []
        for wl in workflow_lessons:
            if isinstance(wl.value, dict):
                steps.extend(wl.value.get("steps", []))

        fp_text = f"{inp.project_hash}:{inp.task_description[:80]}:{inp.started_at}"
        try:
            import xxhash
            content_hash = xxhash.xxh64(fp_text).hexdigest()
        except ImportError:
            import hashlib
            content_hash = hashlib.md5(fp_text.encode()).hexdigest()[:16]

        outcome = "success" if inp.failed_tool_calls == 0 else (
            "partial" if inp.failed_tool_calls < inp.total_tool_calls else "failure"
        )

        return cls(
            task_description=inp.task_description,
            task_id=inp.task_id or output.task_id,
            project_hash=inp.project_hash,
            agent_ids=list({t.get("agent_id", "") for t in inp.team_events} - {""}),
            outcome=outcome,
            lessons=output.lessons,
            repair_patterns=repair_patterns,
            workflow_steps=steps,
            team_composition=team,
            decisions=decisions,
            duration_sec=inp.duration_sec,
            total_tool_calls=inp.total_tool_calls,
            repair_count=len(inp.repair_events),
            file_change_count=len(inp.file_changes),
            lines_added=lines_added,
            lines_removed=lines_removed,
            content_hash=content_hash,
        )
