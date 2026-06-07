from __future__ import annotations

import re
from typing import Any, Dict, List

from agent.reflection.experience_record import (
    KnowledgeLesson, LessonKind, LessonScope, ReflectionInput,
)
from agent.reflection.extractor import BaseReflector

_TEST_CMDS = re.compile(
    r"\b(pytest|unittest|npm\s+test|jest|cargo\s+test|go\s+test|"
    r"rspec|mocha|phpunit|mvn\s+test|gradle\s+test|vitest|bun\s+test)\b",
    re.IGNORECASE,
)
_GIT_COMMIT = re.compile(r"\bgit\s+commit\b", re.IGNORECASE)


class WorkflowReflector(BaseReflector):
    name = "workflow_reflector"

    def extract(self, inp: ReflectionInput) -> List[KnowledgeLesson]:
        lessons: List[KnowledgeLesson] = []
        task_slug = self._slugify(inp.task_description, max_len=32)
        dag = inp.dag_tasks

        if dag:
            lessons.extend(self._extract_dag_lessons(inp, dag, task_slug))
        else:
            steps = self._infer_steps_from_tool_calls(inp.tool_calls)
            if steps:
                lessons.append(self._make_lesson(
                    key=f"workflow:inferred:{task_slug}",
                    kind=LessonKind.WORKFLOW,
                    scope=LessonScope.WORKFLOW,
                    value={
                        "task_description": inp.task_description[:120],
                        "inferred_steps": steps,
                        "source": "tool_call_sequence",
                    },
                    importance=0.6,
                    confidence=0.5,
                    novelty=0.5,
                ))

        if self._has_test_before_commit(inp.terminal_events):
            lessons.append(self._make_lesson(
                key="workflow:test_before_commit",
                kind=LessonKind.WORKFLOW,
                scope=LessonScope.WORKFLOW,
                value={
                    "note": "Tests passed before committing — good practice reinforced",
                    "task_description": inp.task_description[:80],
                },
                importance=0.9,
                confidence=0.9,
                reuse_potential=0.9,
            ))

        if inp.duration_sec > 300:
            lessons.append(self._make_lesson(
                key=f"workflow:long_task:{task_slug}",
                kind=LessonKind.WORKFLOW,
                scope=LessonScope.WORKFLOW,
                value={
                    "task_description": inp.task_description[:120],
                    "duration_sec": inp.duration_sec,
                    "note": "Complex long-running task — worth caching workflow",
                },
                importance=0.75,
                confidence=0.8,
            ))

        return lessons

    # ------------------------------------------------------------------
    # DAG-based lesson extraction
    # ------------------------------------------------------------------

    def _extract_dag_lessons(
        self,
        inp: ReflectionInput,
        dag: List[Dict[str, Any]],
        task_slug: str,
    ) -> List[KnowledgeLesson]:
        lessons: List[KnowledgeLesson] = []
        completed = [t for t in dag if t.get("status") == "completed"]
        failed = [t for t in dag if t.get("status") == "failed"]
        risk_counts = self._detect_risk_levels(dag)
        has_high_risk = bool(risk_counts.get("HIGH", 0) + risk_counts.get("CRITICAL", 0))

        # Rule 1: fully successful workflow
        if completed and not failed:
            lessons.append(self._make_lesson(
                key=f"workflow:success:{task_slug}",
                kind=LessonKind.WORKFLOW,
                scope=LessonScope.WORKFLOW,
                value={
                    "task_description": inp.task_description[:120],
                    "steps": [t["name"] for t in dag],
                    "success_rate": 1.0,
                    "duration_sec": inp.duration_sec,
                    "risk_levels": risk_counts,
                },
                importance=0.85,
                confidence=0.85,
                success_rate=1.0,
            ))

        # Rule 2: partial workflow
        elif completed and failed:
            step_summary = self._summarize_steps(dag)
            lessons.append(self._make_lesson(
                key=f"workflow:partial:{task_slug}",
                kind=LessonKind.WORKFLOW,
                scope=LessonScope.WORKFLOW,
                value={
                    "task_description": inp.task_description[:120],
                    "succeeded_steps": step_summary["succeeded"],
                    "failed_steps": step_summary["failed"],
                    "failure_point": failed[0].get("name", "unknown"),
                    "duration_sec": inp.duration_sec,
                },
                importance=0.7,
                confidence=0.75,
                success_rate=len(completed) / len(dag),
            ))

        # Rule 4: high-risk workflow
        if has_high_risk:
            high_risk_tasks = [
                t["name"] for t in dag
                if t.get("risk_level") in ("HIGH", "CRITICAL")
            ]
            lessons.append(self._make_lesson(
                key=f"workflow:high_risk:{task_slug}",
                kind=LessonKind.WORKFLOW,
                scope=LessonScope.WORKFLOW,
                value={
                    "task_description": inp.task_description[:120],
                    "high_risk_steps": high_risk_tasks,
                    "risk_counts": risk_counts,
                    "note": "Workflow contained high/critical-risk steps — proceed carefully",
                },
                importance=0.8,
                confidence=0.8,
            ))

        # Rule 7: parallel opportunity
        parallel_groups = self._find_parallel_opportunities(dag)
        if len(parallel_groups) > 1:
            lessons.append(self._make_lesson(
                key=f"workflow:parallel_opportunity:{task_slug}",
                kind=LessonKind.WORKFLOW,
                scope=LessonScope.WORKFLOW,
                value={
                    "task_description": inp.task_description[:120],
                    "parallel_groups": parallel_groups,
                    "note": "Some tasks share no files and could run in parallel",
                },
                importance=0.7,
                confidence=0.65,
            ))

        return lessons

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _infer_steps_from_tool_calls(self, tool_calls: list) -> List[str]:
        """Collapse consecutive identical tool names into a readable sequence."""
        steps: List[str] = []
        prev = None
        for call in tool_calls:
            name = call.get("tool", call.get("name", "unknown"))
            if name != prev:
                steps.append(name)
                prev = name
        return steps

    def _detect_risk_levels(self, dag_tasks: list) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for task in dag_tasks:
            level = task.get("risk_level", "UNKNOWN")
            counts[level] = counts.get(level, 0) + 1
        return counts

    def _summarize_steps(self, dag_tasks: list) -> Dict[str, Any]:
        succeeded = [t["name"] for t in dag_tasks if t.get("status") == "completed"]
        failed = [t["name"] for t in dag_tasks if t.get("status") == "failed"]
        return {"succeeded": succeeded, "failed": failed}

    def _find_parallel_opportunities(self, dag_tasks: list) -> List[List[str]]:
        """Group tasks that share no affected_files — candidates for parallel execution."""
        groups: List[List[str]] = []
        assigned: set = set()

        for i, task in enumerate(dag_tasks):
            if i in assigned:
                continue
            files_i = set(task.get("affected_files") or [])
            group = [task.get("name", f"task_{i}")]
            for j, other in enumerate(dag_tasks):
                if j <= i or j in assigned:
                    continue
                files_j = set(other.get("affected_files") or [])
                if not files_i & files_j:
                    group.append(other.get("name", f"task_{j}"))
                    assigned.add(j)
            if len(group) > 1:
                groups.append(group)
                assigned.add(i)

        return groups

    def _has_test_before_commit(self, terminal_events: list) -> bool:
        """Return True when a passing test run is immediately followed by a git commit."""
        last_test_passed = False
        for event in terminal_events:
            cmd = event.get("command", "")
            exit_code = event.get("exit_code", 1)
            if _TEST_CMDS.search(cmd):
                last_test_passed = (exit_code == 0)
            elif _GIT_COMMIT.search(cmd) and last_test_passed:
                return True
        return False
