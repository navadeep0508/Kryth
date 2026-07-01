"""Context Builder — single source of truth for prompt construction.

Replaces: build_initial_system, dynamic system message injection,
experience injection, graph injection, browser/streaming/ponytail injection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from agent.env import getenv
from agent.tools import TOOL_SPECS, READ_ONLY_TOOLS


@dataclass(frozen=True)
class PromptContext:
    """Immutable prompt context — all data needed to render the system prompt."""
    task_type: Literal["simple", "build", "web", "research", "complex"]
    is_trivial: bool
    is_read_only: bool
    cwd: str
    project_map: str
    git_state: str
    project_doc: str
    available_tools: list[str]
    has_browser: bool
    has_streaming: bool
    experience_summary: str = ""
    file_preload: str = ""
    graph_context: str = ""


class ContextBuilder:
    """Builds PromptContext from raw inputs — no prompt rendering logic."""

    def __init__(self):
        self._project_map_cache: dict[str, str] = {}

    def build(
        self,
        user_input: str,
        session_id: str,
        *,
        is_trivial: bool,
        task_type: str,
    ) -> PromptContext:
        """Build context for a new task. Pure function — no side effects."""
        cwd = os.getcwd()

        # Project map (expensive, cached per session)
        project_map = self._get_project_map(session_id, user_input)

        # Git state
        git_state = self._get_git_state()

        # Project doc
        project_doc = self._get_project_doc()

        # Tool availability
        available_tools = [t["function"]["name"] for t in TOOL_SPECS]
        has_browser = "browser_use_task" in available_tools
        has_streaming = "write_file_begin" in available_tools

        # Read-only detection
        is_read_only = self._detect_read_only(user_input)

        # Experience (cheap summary, not full injection)
        experience_summary = self._get_experience_summary(user_input)

        # File preload (just the file list, not content)
        file_preload = self._get_file_preload(user_input)

        # Graph context (summary, not full)
        graph_context = self._get_graph_context(user_input)

        return PromptContext(
            task_type=task_type,
            is_trivial=is_trivial,
            is_read_only=is_read_only,
            cwd=cwd,
            project_map=project_map,
            git_state=git_state,
            project_doc=project_doc,
            available_tools=available_tools,
            has_browser=has_browser,
            has_streaming=has_streaming,
            experience_summary=experience_summary,
            file_preload=file_preload,
            graph_context=graph_context,
        )

    def _get_project_map(self, session_id: str, user_input: str) -> str:
        if session_id in self._project_map_cache:
            return self._project_map_cache[session_id]
        try:
            from agent.context import build_project_map
            result = build_project_map(user_input)
            self._project_map_cache[session_id] = result
            return result
        except Exception:
            return ""

    def _get_git_state(self) -> str:
        try:
            from agent.project_context import git_status_snapshot
            return git_status_snapshot() or ""
        except Exception:
            return ""

    def _get_project_doc(self) -> str:
        try:
            from agent.project_context import load_context_file
            return load_context_file() or ""
        except Exception:
            return ""

    def _detect_read_only(self, user_input: str) -> bool:
        """Detect pure read intent from user input."""
        # All inputs now go through the agent loop for processing
        # No longer using build verb detection for read-only classification
        return False

    def _get_experience_summary(self, user_input: str) -> str:
        """Get condensed experience summary — max 200 chars."""
        try:
            from agent.memory import get_memory
            mem = get_memory()
            hits = mem.recall(user_input, k=2)
            if hits:
                return "; ".join(h[:100] for h in hits)[:200]
        except Exception:
            pass
        return ""

    def _get_file_preload(self, user_input: str) -> str:
        """Get preloaded file list — max 5 files."""
        try:
            from agent.retrieval import search_smart
            results = search_smart(user_input, k=5)
            if results:
                return "\n".join(r.path for r in results)
        except Exception:
            pass
        return ""

    def _get_graph_context(self, user_input: str) -> str:
        """Get graph context summary — max 200 chars."""
        try:
            from agent.retrieval.graphify import query_graph
            results = query_graph(user_input, limit=3)
            if results:
                return "; ".join(r[:80] for r in results)[:200]
        except Exception:
            pass
        return ""


# Singleton instance
_context_builder = ContextBuilder()


def build_prompt_context(
    user_input: str,
    session_id: str,
    *,
    is_trivial: bool,
    task_type: str,
) -> PromptContext:
    """Public API — builds immutable prompt context."""
    return _context_builder.build(user_input, session_id, is_trivial=is_trivial, task_type=task_type)