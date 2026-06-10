"""Work Partitioner — splits a domain's work into N non-overlapping chunks.

Given the directories an AgentRole owns, this module:
  1. Walks those directories to count source files
  2. Calculates N = min(MAX_AGENTS, ceil(file_count / FILES_PER_AGENT))
  3. Distributes subdirectories round-robin across N chunks
  4. Returns a list of DomainChunk objects, each with exclusive dir ownership

When no directories are populated (new project with no files yet), an LLM
fallback splits the task description into N logical sub-task descriptions.

Usage:
    from agent.orchestration.work_partitioner import partition_domain
    chunks = partition_domain(agent.owns.directories, repo_root=".", role="frontend")
    # chunks[0].dirs, chunks[1].dirs ... are non-overlapping
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from agent.context import IGNORE_DIRS

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FILES_PER_AGENT = 20   # target files per sub-agent
MAX_AGENTS = 10        # hard cap on sub-agents per domain
MIN_FILES_TO_SPLIT = 15  # don't split if fewer than this many files


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DomainChunk:
    """A non-overlapping slice of work for one sub-agent."""
    index: int
    dirs: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    description: str = ""
    estimated_files: int = 0


# ---------------------------------------------------------------------------
# Directory scanner
# ---------------------------------------------------------------------------

_SOURCE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
    ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".swift", ".kt",
    ".vue", ".svelte", ".html", ".css", ".scss", ".sass", ".less",
    ".json", ".yaml", ".yml", ".toml", ".md",
}


def _count_source_files(directory: str, repo_root: str) -> int:
    """Count source files in a directory, respecting IGNORE_DIRS."""
    root = Path(repo_root) / directory if not Path(directory).is_absolute() else Path(directory)
    if not root.exists():
        return 0
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for f in filenames:
            if Path(f).suffix.lower() in _SOURCE_EXTS:
                count += 1
    return count


def _list_subdirs(directory: str, repo_root: str) -> List[str]:
    """List immediate subdirectories (non-ignored), relative to repo_root."""
    root = Path(repo_root) / directory if not Path(directory).is_absolute() else Path(directory)
    if not root.exists():
        return [directory]

    subdirs = []
    try:
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and entry.name not in IGNORE_DIRS:
                # Make relative to repo_root
                try:
                    rel = str(entry.relative_to(repo_root))
                except ValueError:
                    rel = str(entry)
                subdirs.append(rel)
    except PermissionError:
        pass

    return subdirs if subdirs else [directory]


# ---------------------------------------------------------------------------
# Scaling formula
# ---------------------------------------------------------------------------

def _calculate_n_agents(total_files: int, estimated_turns: int) -> int:
    """How many parallel agents should handle this domain?"""
    if total_files < MIN_FILES_TO_SPLIT:
        return 1
    # File-based estimate
    file_n = math.ceil(total_files / FILES_PER_AGENT)
    # Turn-based estimate (each agent should run ~60 turns max)
    turn_n = max(1, estimated_turns // 60)
    n = min(MAX_AGENTS, file_n, turn_n)
    return max(1, n)


# ---------------------------------------------------------------------------
# Directory partitioner
# ---------------------------------------------------------------------------

def partition_domain(
    owned_dirs: List[str],
    repo_root: str = ".",
    role: str = "",
    estimated_turns: int = 80,
    max_agents: int = MAX_AGENTS,
    user_input: str = "",
) -> List[DomainChunk]:
    """Partition owned directories into N non-overlapping DomainChunks.

    Returns a single-element list when no split is warranted (small task).
    Returns N chunks with non-overlapping dirs when splitting is beneficial.
    """
    if not owned_dirs:
        # No directories populated — new project. Try LLM fallback to split
        # the task conceptually, or return a single no-dir chunk.
        return _llm_fallback_partition(role, estimated_turns, user_input, max_agents)

    # Collect all leaf subdirectories and count files per dir
    all_subdirs: List[str] = []
    for d in owned_dirs:
        subs = _list_subdirs(d, repo_root)
        all_subdirs.extend(subs)

    # Deduplicate while preserving order
    seen: set = set()
    unique_subdirs = [s for s in all_subdirs if not (s in seen or seen.add(s))]

    # Count total files
    total_files = sum(_count_source_files(d, repo_root) for d in unique_subdirs)
    if total_files == 0:
        # Fall back to the original dirs as single chunk
        return [DomainChunk(
            index=0, dirs=list(owned_dirs),
            description=f"Complete {role} work",
            estimated_files=0,
        )]

    n = min(max_agents, _calculate_n_agents(total_files, estimated_turns))
    if n <= 1:
        return [DomainChunk(
            index=0, dirs=list(owned_dirs),
            description=f"Complete {role} work",
            estimated_files=total_files,
        )]

    # Round-robin distribute subdirs across N chunks (preserves locality:
    # adjacent dirs usually belong to the same feature area)
    chunks: List[List[str]] = [[] for _ in range(n)]
    for i, d in enumerate(unique_subdirs):
        chunks[i % n].append(d)

    result = []
    for i, chunk_dirs in enumerate(chunks):
        if not chunk_dirs:
            continue
        est = sum(_count_source_files(d, repo_root) for d in chunk_dirs)
        # Build a human-readable description from dir names
        short_names = [Path(d).name for d in chunk_dirs[:3]]
        desc = f"{role} #{i+1} — {', '.join(short_names)}"
        if len(chunk_dirs) > 3:
            desc += f" (+{len(chunk_dirs)-3} more)"
        result.append(DomainChunk(
            index=i, dirs=chunk_dirs,
            description=desc,
            estimated_files=est,
        ))

    return result if result else [DomainChunk(index=0, dirs=list(owned_dirs))]


# ---------------------------------------------------------------------------
# LLM fallback for new projects (no existing files)
# ---------------------------------------------------------------------------

_PARTITION_SYSTEM = """You are splitting a large engineering task between parallel agents of the same type.
Given the role and task description, divide the work into 2-5 independent sub-tasks.

Rules:
- Each sub-task must be independent (no shared state)
- Each sub-task should own specific directories or file groups
- Return ONLY a JSON array, no prose

Example:
[
  {"description": "Build authentication pages: login, signup, password reset", "dirs": ["src/auth", "src/pages/auth"]},
  {"description": "Build dashboard pages: main, settings, profile", "dirs": ["src/dashboard", "src/pages/dashboard"]}
]"""


def _llm_fallback_partition(
    role: str,
    estimated_turns: int,
    user_input: str,
    max_agents: int,
) -> List[DomainChunk]:
    """Ask LLM to split work conceptually when no files exist yet."""
    n = min(max_agents, max(1, estimated_turns // 60))
    if n <= 1 or not user_input:
        return [DomainChunk(index=0, description=f"Complete {role} work")]

    try:
        from agent.llm import _get_client, PLANNER_MODEL
        client = _get_client()
        prompt = (
            f"Role: {role}\n"
            f"Task: {user_input[:400]}\n"
            f"Split into {n} independent sub-tasks for parallel agents."
        )
        resp = client.chat.completions.create(
            model=PLANNER_MODEL,
            messages=[
                {"role": "system", "content": _PARTITION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0, max_tokens=600, timeout=8,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        start, end = raw.find("["), raw.rfind("]")
        if start >= 0 and end > start:
            items = json.loads(raw[start:end+1])
            if isinstance(items, list) and items:
                return [
                    DomainChunk(
                        index=i,
                        dirs=list(item.get("dirs") or []),
                        description=str(item.get("description", f"{role} #{i+1}")),
                    )
                    for i, item in enumerate(items[:n])
                ]
    except Exception:
        pass

    return [DomainChunk(index=0, description=f"Complete {role} work")]
