"""KRYTH Experience Engine — public API.

This is a pure reasoning layer above the existing memory systems.
It never duplicates storage — it reads from:
  ExecutionMemory, WorkflowMemory, FailureMemory, DecisionMemory,
  TerminalMemory, TeamMemory, KnowledgeGraph

And writes only lightweight scored pointer records to:
  ~/.kryth/experience/<project_hash>.db

Usage:

    from agent.experience import get_experience

    exp = get_experience(".")
    similar   = exp.search("build a SaaS dashboard")
    rec       = exp.recommend("team", "build a SaaS dashboard")
    pred      = exp.predict("build a SaaS dashboard")
    exp.learn("task", title="SaaS", summary="...", success=True)
    exp.report()
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.experience.store import ExperienceRecord, ExperienceStore, get_store, project_hash
from agent.experience.similar_task import SimilarTaskEngine, SimilarTaskSearchResult
from agent.experience.team_experience import TeamExperienceEngine, TeamRecommendation
from agent.experience.workflow_experience import WorkflowExperienceEngine, WorkflowExperience
from agent.experience.repair_experience import RepairExperienceEngine, RepairExperience
from agent.experience.terminal_experience import TerminalExperienceEngine, TerminalExperience
from agent.experience.decision_experience import DecisionExperienceEngine, DecisionExperience
from agent.experience.arch_experience import ArchExperienceEngine, ArchExperience
from agent.experience.parallel_experience import ParallelExperienceEngine
from agent.experience.prediction import Prediction, predict
from agent.experience.dashboard import render_dashboard


class ExperienceManager:
    """Unified reasoning layer over all memory systems.

    API:
        search(query)          → SimilarTaskSearchResult
        recommend(kind, query) → typed recommendation
        predict(query)         → Prediction
        rank(records)          → sorted List[ExperienceRecord]
        learn(kind, **kwargs)  → record an outcome
        report()               → rich dashboard dict
    """

    def __init__(self, root: str = ".") -> None:
        self._root = root
        self._store = get_store(root)
        self._similar = SimilarTaskEngine(self._store)
        self._team = TeamExperienceEngine(self._store)
        self._workflow = WorkflowExperienceEngine(self._store)
        self._repair = RepairExperienceEngine(self._store)
        self._terminal = TerminalExperienceEngine(self._store)
        self._decision = DecisionExperienceEngine(self._store)
        self._arch = ArchExperienceEngine(self._store)
        self._parallel = ParallelExperienceEngine(self._store)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 5) -> SimilarTaskSearchResult:
        """Search all memory systems for similar past tasks."""
        return self._similar.search(query, top_k=top_k)

    # ------------------------------------------------------------------
    # Recommend
    # ------------------------------------------------------------------

    def recommend(self, kind: str, query: str) -> Optional[Any]:
        """Return a recommendation for the given kind and query.

        kind: "team" | "workflow" | "repair" | "terminal" | "decision" | "architecture" | "parallel"
        """
        if kind == "team":
            return self._team.recommend(query)
        if kind == "workflow":
            return self._workflow.recommend(query)
        if kind == "repair":
            parts = query.split(":", 1)
            error_type = parts[0].strip()
            error_msg = parts[1].strip() if len(parts) > 1 else query
            return self._repair.lookup(error_type, error_msg)
        if kind == "terminal":
            parts = query.split(":", 1)
            cmd_kind = parts[0].strip()
            stack = parts[1].strip() if len(parts) > 1 else ""
            return self._terminal.lookup(cmd_kind, stack)
        if kind == "decision":
            return self._decision.lookup(query)
        if kind == "architecture":
            return self._arch.lookup(query)
        if kind == "parallel":
            ac, conf = self._parallel.recommend_agent_count(query)
            return {"recommended_agent_count": ac, "confidence": conf}
        return None

    # ------------------------------------------------------------------
    # Predict
    # ------------------------------------------------------------------

    def predict(self, query: str) -> Prediction:
        """Predict success probability, repairs, team size, workflow."""
        similar = self.search(query)
        return predict(query, similar, self._root)

    # ------------------------------------------------------------------
    # Rank
    # ------------------------------------------------------------------

    def rank(self, records: List[ExperienceRecord]) -> List[ExperienceRecord]:
        """Sort experience records by composite score descending."""
        return sorted(records, key=lambda r: -r.composite_score)

    # ------------------------------------------------------------------
    # Learn
    # ------------------------------------------------------------------

    def learn(self, kind: str, **kwargs) -> None:
        """Record a new experience outcome.

        kwargs vary by kind:
          task:        title, summary, tags, success, extra
          team:        task_description, roles, strategy, execution_turns,
                       repair_count, merge_conflicts, success
          workflow:    name, steps, intent_type, success, duration_sec
          repair:      error_type, error_message, repair_commands, success
          terminal:    cmd_kind, stack, commands, success
          decision:    title, choice, rationale, alternatives, outcome
          architecture:project_type, folder_structure, dependency_layout, success
          parallel:    task_type, agent_count, parallel_depth,
                       execution_turns, conflict_count, merge_problems, success
        """
        import time

        if kind == "task":
            rec = ExperienceRecord(
                id=None,
                project_hash=self._store._ph,
                kind="task",
                title=kwargs.get("title", "untitled"),
                summary=kwargs.get("summary", ""),
                tags=kwargs.get("tags", []),
                source_refs=kwargs.get("source_refs", {}),
                outcome="success" if kwargs.get("success", True) else "failure",
                importance=kwargs.get("importance", 0.6),
                confidence=kwargs.get("confidence", 0.7),
                success_rate=1.0 if kwargs.get("success", True) else 0.0,
                reuse_count=0,
                novelty=kwargs.get("novelty", 0.5),
                recency_ts=time.time(),
                created_at=time.time(),
                composite_score=0.0,
                extra=kwargs.get("extra", {}),
            )
            self._store.insert(rec)

        elif kind == "team":
            self._team.record(
                task_description=kwargs.get("task_description", ""),
                roles=kwargs.get("roles", []),
                strategy=kwargs.get("strategy", "sequential"),
                execution_turns=kwargs.get("execution_turns", 0),
                repair_count=kwargs.get("repair_count", 0),
                merge_conflicts=kwargs.get("merge_conflicts", 0),
                success=kwargs.get("success", True),
            )

        elif kind == "workflow":
            self._workflow.record(
                name=kwargs.get("name", "workflow"),
                steps=kwargs.get("steps", []),
                intent_type=kwargs.get("intent_type", "feature"),
                success=kwargs.get("success", True),
                duration_sec=kwargs.get("duration_sec", 0.0),
            )

        elif kind == "repair":
            self._repair.record(
                error_type=kwargs.get("error_type", "unknown"),
                error_message=kwargs.get("error_message", ""),
                repair_commands=kwargs.get("repair_commands", []),
                success=kwargs.get("success", True),
            )

        elif kind == "terminal":
            self._terminal.record(
                kind=kwargs.get("cmd_kind", "build"),
                stack=kwargs.get("stack", ""),
                commands=kwargs.get("commands", []),
                success=kwargs.get("success", True),
            )

        elif kind == "decision":
            self._decision.record(
                title=kwargs.get("title", ""),
                choice=kwargs.get("choice", ""),
                rationale=kwargs.get("rationale", ""),
                alternatives=kwargs.get("alternatives", []),
                outcome=kwargs.get("outcome", "successful"),
            )

        elif kind == "architecture":
            self._arch.record(
                project_type=kwargs.get("project_type", ""),
                folder_structure=kwargs.get("folder_structure", []),
                dependency_layout=kwargs.get("dependency_layout", {}),
                success=kwargs.get("success", True),
                database_design=kwargs.get("database_design", ""),
                service_layout=kwargs.get("service_layout", []),
                api_design=kwargs.get("api_design", ""),
            )

        elif kind == "parallel":
            self._parallel.record(
                task_type=kwargs.get("task_type", ""),
                agent_count=kwargs.get("agent_count", 1),
                parallel_depth=kwargs.get("parallel_depth", 1),
                execution_turns=kwargs.get("execution_turns", 0),
                conflict_count=kwargs.get("conflict_count", 0),
                merge_problems=kwargs.get("merge_problems", 0),
                success=kwargs.get("success", True),
            )

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def report(self, query: Optional[str] = None, render: bool = True) -> Dict[str, Any]:
        """Generate and optionally render the experience dashboard."""
        stats = self._store.stats()

        if query:
            similar = self.search(query)
            pred = self.predict(query)
            if render:
                render_dashboard(similar, pred)
            return {
                "stats": stats,
                "similar": [m.__dict__ for m in similar.matches],
                "prediction": pred.__dict__,
                "confidence": similar.confidence,
            }

        return {"stats": stats}


# ------------------------------------------------------------------
# Module-level singletons
# ------------------------------------------------------------------

_managers: dict[str, ExperienceManager] = {}


def get_experience(root: str = ".") -> ExperienceManager:
    """Return the ExperienceManager for the given project root."""
    import os
    key = os.path.abspath(root)
    if key not in _managers:
        _managers[key] = ExperienceManager(root)
    return _managers[key]


__all__ = [
    "ExperienceManager",
    "get_experience",
    "SimilarTaskSearchResult",
    "TeamRecommendation",
    "WorkflowExperience",
    "RepairExperience",
    "TerminalExperience",
    "DecisionExperience",
    "ArchExperience",
    "Prediction",
]
