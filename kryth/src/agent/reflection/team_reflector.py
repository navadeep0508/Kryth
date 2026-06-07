from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from agent.reflection.experience_record import (
    KnowledgeLesson, LessonKind, LessonScope, ReflectionInput,
)
from agent.reflection.extractor import BaseReflector


class TeamReflector(BaseReflector):
    name = "team_reflector"

    def extract(self, inp: ReflectionInput) -> List[KnowledgeLesson]:
        team = inp.team_events
        if not team:
            return []

        lessons: List[KnowledgeLesson] = []
        task_slug = self._slugify(inp.task_description, max_len=32)
        avg = self._avg_turns(team)
        total_failed = sum(e.get("tasks_failed", 0) for e in team)

        # Rule 1: fully successful team composition
        if total_failed == 0:
            roles = [e.get("role", "") for e in team]
            lessons.append(self._make_lesson(
                key=f"team:success_composition:{self._role_hash(roles)}",
                kind=LessonKind.TEAM,
                scope=LessonScope.PROJECT,
                value={
                    "roles": roles,
                    "agent_count": len(team),
                    "description": "Team that succeeded at this task type",
                    "task_description": inp.task_description[:80],
                },
                importance=0.8,
                confidence=0.8,
                success_rate=1.0,
            ))

        # Rule 6: single-agent success
        if len(team) == 1 and total_failed == 0:
            lessons.append(self._make_lesson(
                key="team:solo_success",
                kind=LessonKind.TEAM,
                scope=LessonScope.PROJECT,
                value={
                    "role": team[0].get("role", "unknown"),
                    "task_description": inp.task_description[:80],
                    "note": "This task type can be handled by a single agent",
                },
                importance=0.8,
                confidence=0.8,
            ))

        # Rule 4: optimal team size
        if len(team) <= 2 and total_failed == 0:
            lessons.append(self._make_lesson(
                key=f"team:small_team_success:{task_slug}",
                kind=LessonKind.TEAM,
                scope=LessonScope.PROJECT,
                value={
                    "agent_count": len(team),
                    "task_description": inp.task_description[:80],
                    "note": "Small team achieved full success — lean composition preferred",
                },
                importance=0.7,
                confidence=0.75,
            ))
        elif len(team) > 5 and total_failed > 0:
            lessons.append(self._make_lesson(
                key=f"team:too_large:{task_slug}",
                kind=LessonKind.TEAM,
                scope=LessonScope.PROJECT,
                value={
                    "agent_count": len(team),
                    "tasks_failed": total_failed,
                    "task_description": inp.task_description[:80],
                    "note": "Large team with failures — consider reducing team size",
                },
                importance=0.7,
                confidence=0.65,
            ))

        for agent in team:
            role = agent.get("role", "unknown")
            completed = agent.get("tasks_completed", 0)
            failed = agent.get("tasks_failed", 0)
            turns = agent.get("turns_used", 0)

            # Rule 2: unused agent
            if completed == 0 and failed == 0:
                lessons.append(self._make_lesson(
                    key=f"team:unused_agent:{self._slugify(role)}",
                    kind=LessonKind.TEAM,
                    scope=LessonScope.PROJECT,
                    value={
                        "role": role,
                        "note": "Agent was created but not needed — consider removing from team for this task type",
                    },
                    importance=0.75,
                    confidence=0.8,
                ))

            # Rule 3: overloaded agent
            if avg > 0 and turns > 2 * avg:
                lessons.append(self._make_lesson(
                    key=f"team:overloaded:{self._slugify(role)}",
                    kind=LessonKind.TEAM,
                    scope=LessonScope.PROJECT,
                    value={
                        "role": role,
                        "turns_used": turns,
                        "avg_turns": round(avg, 2),
                        "note": "Consider splitting responsibilities",
                    },
                    importance=0.7,
                    confidence=0.75,
                ))

            # Rule 5: specialization match
            spec = agent.get("specialization", "")
            if spec and _specialization_matches(spec, inp.task_description):
                lessons.append(self._make_lesson(
                    key=f"team:specialization_match:{self._slugify(role)}",
                    kind=LessonKind.TEAM,
                    scope=LessonScope.PROJECT,
                    value={
                        "role": role,
                        "specialization": spec,
                        "task_description": inp.task_description[:80],
                        "note": "Specialization aligned with task — good assignment",
                    },
                    importance=0.75,
                    confidence=0.7,
                ))

        # Rule 7: shared files across agents
        shared = self._find_shared_files(team)
        for file_path in shared:
            file_slug = self._slugify(file_path.replace("/", "_").replace("\\", "_"), max_len=32)
            lessons.append(self._make_lesson(
                key=f"team:shared_file:{file_slug}",
                kind=LessonKind.TEAM,
                scope=LessonScope.PROJECT,
                value={
                    "file": file_path,
                    "note": "File was touched by multiple agents — potential merge conflict zone",
                },
                importance=0.65,
                confidence=0.7,
            ))

        return lessons

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _avg_turns(self, team: list) -> float:
        if not team:
            return 0.0
        total = sum(e.get("turns_used", 0) for e in team)
        return total / len(team)

    def _role_hash(self, roles: List[str]) -> str:
        """Short stable hash of sorted role list."""
        combined = ",".join(sorted(roles))
        return hashlib.md5(combined.encode()).hexdigest()[:8]

    def _find_shared_files(self, team: list) -> List[str]:
        """Return file paths that appear in more than one agent's tasks_completed."""
        file_counts: Dict[str, int] = {}
        for agent in team:
            completed = agent.get("tasks_completed", [])
            # tasks_completed may be an int (count) or a list of task dicts/strings
            if not isinstance(completed, (list, tuple)):
                continue
            for item in completed:
                if isinstance(item, dict):
                    files = item.get("affected_files") or []
                elif isinstance(item, str):
                    files = [item]
                else:
                    files = []
                for f in files:
                    file_counts[f] = file_counts.get(f, 0) + 1
        return [f for f, count in file_counts.items() if count > 1]


def _specialization_matches(specialization: str, task_description: str) -> bool:
    """Rough keyword overlap check between specialization and task description."""
    spec_words = set(specialization.lower().split())
    task_words = set(task_description.lower().split())
    return bool(spec_words & task_words)
