"""Memory controllers — Write, Retrieval, DuplicateDetector, Compression, Confidence, Policy.

Every tool call that produces knowledge must go through WriteController.
Every prompt injection must go through RetrievalController.
Every duplicate check must go through DuplicateDetector.
Memory growth is managed by CompressionController.
Memory reliability is scored by ConfidenceController.
Action selection (REUSE/READ/EXECUTE/INVESTIGATE) is gated by PolicyController.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

_logger = logging.getLogger(__name__)


# ── State hash (incremental with dirty flags) ───────────────────────────

_LOCK_FILES = [
    "requirements.txt", "Pipfile", "Pipfile.lock",
    "pyproject.toml", "poetry.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.toml", "Cargo.lock",
    "go.mod", "go.sum",
    "Gemfile", "Gemfile.lock",
]

_ENV_VARS = ("VIRTUAL_ENV", "CONDA_PREFIX", "NODE_ENV", "PYTHON_VERSION", "NODE_VERSION")

_SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java"}


class StateHashCache:
    """Incremental state hash with dirty-flag detection.

    On each call, checks which state dimensions changed (cheap mtime / HEAD
    comparisons) and only recomputes what is dirty. A full git diff is ~50ms;
    a cache hit with no changes is ~0.05ms.
    """

    _data: dict[str, dict] = {}
    _lock = threading.RLock()

    @classmethod
    def get(cls, cwd: str = "") -> str:
        cwd = cwd or os.getcwd()
        with cls._lock:
            entry = cls._data.get(cwd)
            if entry is None:
                return cls._build_full(cwd)

            dirty = cls._detect_dirty(cwd, entry)
            if not dirty:
                return entry["hash"]

            return cls._rebuild(cwd, entry, dirty)

    @classmethod
    def invalidate(cls, cwd: str = "") -> None:
        cwd = cwd or os.getcwd()
        with cls._lock:
            cls._data.pop(cwd, None)

    # ── Snapshot helpers ─────────────────────────────────────────────

    @classmethod
    def _snapshot_git_head(cls, cwd: str) -> str:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, timeout=3, cwd=cwd,
            )
            if r.returncode == 0:
                return r.stdout.decode().strip()[:16]
        except Exception as _e:
            _logger.debug("StateHashCache git rev-parse: %s", _e)
        return ""

    @classmethod
    def _snapshot_git_diff(cls, cwd: str) -> bytes:
        try:
            diff = subprocess.run(
                ["git", "diff", "--no-color"],
                capture_output=True, timeout=5, cwd=cwd,
            )
            staged = subprocess.run(
                ["git", "diff", "--no-color", "--cached"],
                capture_output=True, timeout=5, cwd=cwd,
            )
            return (diff.stdout + staged.stdout)[:100000]
        except Exception as _e:
            _logger.debug("_snapshot_git_diff: %s", _e)
            return b""

    @classmethod
    def _snapshot_file_mtimes(cls, cwd: str, names: list[str]) -> dict[str, float]:
        result = {}
        for name in names:
            try:
                p = Path(cwd) / name
                result[name] = p.stat().st_mtime if p.is_file() else 0.0
            except Exception as _e:
                _logger.debug("_snapshot_file_mtimes: %s", _e)
                result[name] = 0.0
        return result

    @classmethod
    def _snapshot_source_mtimes(cls, cwd: str) -> dict[str, float]:
        result = {}
        try:
            for f in Path(cwd).iterdir():
                if f.is_file() and f.suffix in _SOURCE_EXTENSIONS:
                    try:
                        result[f.name] = f.stat().st_mtime
                    except Exception as _e:
                        _logger.debug("_snapshot_source_mtimes stat: %s", _e)
        except Exception as _e:
            _logger.debug("_snapshot_source_mtimes iterdir: %s", _e)
        return result

    @classmethod
    def _snapshot_env(cls) -> dict[str, str]:
        return {v: os.environ.get(v, "") for v in _ENV_VARS}

    # ── Dirty detection ──────────────────────────────────────────────

    @classmethod
    def _detect_dirty(cls, cwd: str, entry: dict) -> set[str]:
        dirty: set[str] = set()

        curr_head = cls._snapshot_git_head(cwd)
        if curr_head != entry.get("git_head"):
            dirty.add("git")

        curr_lock_mtimes = cls._snapshot_file_mtimes(cwd, _LOCK_FILES)
        for name, mtime in curr_lock_mtimes.items():
            if mtime != entry.get("lock_mtimes", {}).get(name, -1):
                dirty.add("lock_files")
                break

        curr_src_mtimes = cls._snapshot_source_mtimes(cwd)
        for name, mtime in curr_src_mtimes.items():
            if mtime != entry.get("source_mtimes", {}).get(name, -1):
                dirty.add("source_files")
                break

        for var in _ENV_VARS:
            if os.environ.get(var, "") != entry.get("env_vars", {}).get(var, ""):
                dirty.add("env")
                break

        return dirty

    # ── Build / rebuild ──────────────────────────────────────────────

    @classmethod
    def _compute_core(cls, cwd: str, flags: set[str]) -> dict:
        """Compute the state dict, optionally skipping clean dimensions."""
        hasher = hashlib.sha256()
        hasher.update(cwd.encode())

        if "git" in flags:
            diff_bytes = cls._snapshot_git_diff(cwd)
            hasher.update(diff_bytes)
        elif "git" not in flags and "full_diff" in flags:
            # On full rebuild always include diff
            diff_bytes = cls._snapshot_git_diff(cwd)
            hasher.update(diff_bytes)

        if "lock_files" in flags or "full" in flags:
            for fname in _LOCK_FILES:
                fpath = Path(cwd) / fname
                if fpath.is_file():
                    try:
                        h = hashlib.sha256(fpath.read_bytes()).hexdigest()[:16]
                        hasher.update(f"{fname}:{h}".encode())
                    except Exception as _e:
                        _logger.debug("_compute_core lock_files hash: %s", _e)

        if "source_files" in flags or "full" in flags:
            for f in sorted(Path(cwd).iterdir(), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True)[:20]:
                if f.is_file() and f.suffix in _SOURCE_EXTENSIONS:
                    try:
                        h = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
                        hasher.update(f"{f.name}:{h}".encode())
                    except Exception as _e:
                        _logger.debug("_compute_core source_files hash: %s", _e)

        if "env" in flags or "full" in flags:
            for var in _ENV_VARS:
                val = os.environ.get(var, "")
                if val:
                    hasher.update(f"{var}={val}".encode())

        return {
            "hash": hasher.hexdigest()[:16],
            "git_head": cls._snapshot_git_head(cwd),
            "lock_mtimes": cls._snapshot_file_mtimes(cwd, _LOCK_FILES),
            "source_mtimes": cls._snapshot_source_mtimes(cwd),
            "env_vars": cls._snapshot_env(),
        }

    @classmethod
    def _build_full(cls, cwd: str) -> str:
        entry = cls._compute_core(cwd, {"full"})
        cls._data[cwd] = entry
        return entry["hash"]

    @classmethod
    def _rebuild(cls, cwd: str, _entry: dict, dirty: set[str]) -> str:
        entry = cls._compute_core(cwd, dirty | {"full"})
        cls._data[cwd] = entry
        return entry["hash"]


def compute_state_hash(cwd: str = "") -> str:
    """Compute a robust state hash for duplicate command detection.

    Includes git diff, lock files, recent source hashes, and env.
    Uses StateHashCache internally for incremental dirty-flag detection.
    """
    return StateHashCache.get(cwd)


# ── Duplicate Detector ──────────────────────────────────────────────────

class DuplicateDetector:
    """Soft duplicate detection — returns memory summaries, never hard-blocks."""

    def check_read(self, repo_memory, session_id: int, path: str) -> Optional[dict]:
        """Check if file was already read. Returns a memory hit dict or None."""
        file_rec = repo_memory.get_file(session_id, path)
        if not file_rec:
            return None
        try:
            resolved = path
            if not os.path.isabs(path):
                from agent.project_context import project_root
                root = str(project_root())
                candidate = os.path.join(root, path)
                if os.path.exists(candidate):
                    resolved = candidate
                else:
                    resolved = os.path.abspath(path)
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                current_hash = hashlib.sha256(f.read().replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]
            if current_hash != file_rec.hash:
                return None
        except Exception:
            return None

        summary = self._build_read_summary(file_rec)
        return {
            "duplicate": True,
            "type": "read",
            "path": path,
            "turn": file_rec.last_read_turn,
            "read_count": file_rec.read_count,
            "summary": summary,
        }

    def _build_read_summary(self, rec) -> str:
        parts = [f"{rec.path} ({rec.language}, {rec.lines} lines, read #{rec.read_count})"]
        if rec.purpose:
            parts.append(f"   Purpose: {rec.purpose[:80]}")
        if rec.functions:
            parts.append(f"   Functions: {', '.join(rec.functions[:8])}")
        if rec.classes:
            parts.append(f"   Classes: {', '.join(rec.classes[:5])}")
        if rec.routes:
            parts.append(f"   Routes: {', '.join(rec.routes[:8])}")
        if rec.imports:
            parts.append(f"   Imports: {', '.join(rec.imports[:6])}")
        return "\n".join(parts)

    def check_command(
        self, execution_memory, session_id: int, command: str, cwd: str, state_hash: str = ""
    ) -> Optional[dict]:
        """Check if identical command was already run. Returns a memory hit dict or None."""
        dup = execution_memory.find_duplicate(session_id, command, cwd, state_hash)
        if dup is None:
            return None
        return {
            "duplicate": True,
            "type": "command",
            "command": command[:80],
            "exit_code": dup.exit_code,
            "status": "success" if dup.exit_code == 0 else "failed",
            "previous_output": dup.output_summary[:200],
            "run_count": dup.run_count,
        }

    def check_edit(
        self, mutation_memory, session_id: int, path: str, old_string: str, new_string: str
    ) -> Optional[dict]:
        """Check if the same edit was already applied. Prevents edit loops."""
        mutations = mutation_memory.get_by_path(session_id, path)
        edit_signature = f"{old_string[:80]}→{new_string[:80]}"
        for m in mutations:
            if edit_signature in m.diff_summary:
                return {
                    "duplicate": True,
                    "type": "edit",
                    "path": path,
                    "change_count": mutation_memory.get_file_change_count(session_id, path),
                    "summary": m.diff_summary,
                }
        return None


# ── Write Controller ────────────────────────────────────────────────────

def _refresh_repo_state(memory_manager, session_id: int, path: str, turn: int) -> None:
    """Re-read file from disk and refresh RepoMemory semantic data.

    Called after every mutation so RepoMemory always reflects current
    file contents — functions, classes, imports, routes, purpose.
    Also re-caches full content in ReadMemory so subsequent
    duplicate-read detection returns the complete file, not just
    a sparse metadata summary.
    """
    import os as _os
    try:
        if not _os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8", errors="replace") as _f:
            content = _f.read()
        memory_manager.repo.add_file(session_id, path, content, turn)
        try:
            from agent.memory.read_memory import _read_memory_manager
            _read_memory_manager.record_read_file(
                session_id, path, {"path": path}, content,
            )
        except Exception as _e:
            _logger.debug("_refresh_repo_state record_read_file: %s", _e)
    except Exception as _e:
        _logger.debug("_refresh_repo_state open/read: %s", _e)


def _invalidate_read_memory(session_id: int, path: str) -> None:
    """Invalidate ReadMemory cache entry so next read gets fresh data."""
    try:
        from agent.memory.read_memory import _read_memory_manager
        mem = _read_memory_manager._get_memory(session_id)
        resolved = _read_memory_manager._resolve_read_key(path, set(mem.files.keys()))
        mem.files.pop(resolved, None)
        mem.file_hashes.pop(resolved, None)
    except Exception as _e:
        _logger.debug("_invalidate_read_memory: %s", _e)


class WriteController:
    """Single writer for all memory layers."""

    def on_tool_result(
        self,
        tool_name: str,
        args: dict,
        result: Any,
        error: bool,
        session_id: int,
        memory_manager,
        turn: int,
    ) -> None:
        if error:
            return

        if tool_name == "read_file":
            path = args.get("path", "")
            if path and result:
                content = str(result)
                memory_manager.repo.add_file(session_id, path, content, turn)
                memory_manager.working.add_finding(f"Read: {path}")

        elif tool_name in ("run_command", "shell_exec"):
            command = args.get("command", "") or args.get("cmd", "")
            cwd = args.get("cwd") or os.getcwd()
            exit_code = 0
            output_summary = str(result)[:500] if result else ""
            if "ERROR" in output_summary.upper() or "FAILED" in output_summary.upper():
                exit_code = 1
            env_hash = hashlib.sha256(str(sorted(os.environ.items())).encode()).hexdigest()[:16]
            state_hash = compute_state_hash(cwd)
            memory_manager.execution.record(
                session_id, command, cwd, exit_code, output_summary,
                env_hash=env_hash, state_hash=state_hash,
            )

        elif tool_name in ("edit_file", "multi_edit"):
            path = args.get("path", "")
            if path:
                memory_manager.repo.invalidate_file(session_id, path)
                _invalidate_read_memory(session_id, path)
                desc = args.get("oldString", "")[:60] or args.get("newString", "")[:60]
                desc = desc or f"edit {os.path.basename(path)}"
                memory_manager.episodic.add_edit(session_id, path, desc)
                StateHashCache.invalidate()
                # Record in MutationMemory
                try:
                    from agent.memory.mutation_memory import _compute_diff_summary, _compute_file_hash
                    _summary, _added, _removed = _compute_diff_summary(tool_name, args, str(result) if result else "")
                    _after = _compute_file_hash(path)
                    memory_manager.mutation.record(
                        session_id, tool_name, path,
                        after_hash=_after,
                        diff_summary=_summary,
                        lines_added=_added,
                        lines_removed=_removed,
                    )
                except Exception as _e:
                    _logger.debug("WriteController edit_file mutation record: %s", _e)
                try:
                    _refresh_repo_state(memory_manager, session_id, path, turn)
                except Exception as _e:
                    _logger.debug("WriteController edit_file refresh_repo: %s", _e)

        elif tool_name == "write_file":
            path = args.get("path", "")
            if path:
                memory_manager.repo.invalidate_file(session_id, path)
                _invalidate_read_memory(session_id, path)
                memory_manager.episodic.add_edit(session_id, path, "wrote file")
                StateHashCache.invalidate()
                # Record in MutationMemory
                try:
                    from agent.memory.mutation_memory import _compute_diff_summary, _compute_file_hash
                    _summary, _added, _removed = _compute_diff_summary(tool_name, args, str(result) if result else "")
                    _after = _compute_file_hash(path)
                    content_length = len(str(result)) if result else 0
                    memory_manager.mutation.record(
                        session_id, tool_name, path,
                        after_hash=_after,
                        diff_summary=_summary,
                        lines_added=_added,
                        lines_removed=_removed,
                        content_length=content_length,
                    )
                except Exception as _e:
                    _logger.debug("WriteController write_file mutation record: %s", _e)
                try:
                    _refresh_repo_state(memory_manager, session_id, path, turn)
                except Exception as _e:
                    _logger.debug("WriteController write_file refresh_repo: %s", _e)

        elif tool_name == "delete_file":
            path = args.get("path", "")
            if path:
                memory_manager.repo.remove_file(session_id, path)
                _invalidate_read_memory(session_id, path)
                # Record in MutationMemory
                try:
                    from agent.memory.mutation_memory import _compute_diff_summary
                    _summary, _added, _removed = _compute_diff_summary(tool_name, args, str(result) if result else "")
                    memory_manager.mutation.record(
                        session_id, tool_name, path,
                        after_hash="",
                        diff_summary=_summary,
                    )
                except Exception as _e:
                    _logger.debug("WriteController delete_file mutation record: %s", _e)

        elif tool_name in ("run_tests",):
            memory_manager.episodic.add_result(session_id, str(result)[:200] if result else "tests completed")

    def set_objective(self, memory_manager, session_id: int, objective: str) -> None:
        memory_manager.working.set_objective(objective)
        if session_id:
            try:
                memory_manager.episodic.set_objective(session_id, objective)
            except Exception as _e:
                _logger.debug("WriteController set_objective: %s", _e)


# ── Retrieval Controller ────────────────────────────────────────────────

class RetrievalController:
    """Retrieve only relevant memory, capped at 800 tokens (~3200 chars).

    Priority order:
      1. Current objective + WorkingMemory
      2. Top repo entries (ranked by importance + relevance)
      3. Recent command results
      4. Critical episodic findings
    """

    MAX_CHARS = 3200

    @property
    def semantic(self) -> SemanticRetriever:
        if not hasattr(self, "_semantic"):
            self._semantic = SemanticRetriever()
        return self._semantic

    _RELEVANCE_KEYWORDS = {
        "auth": ["login", "auth", "oauth", "session", "token", "password", "user", "permission", "jwt"],
        "api": ["route", "api", "endpoint", "rest", "controller", "handler", "middleware"],
        "db": ["db", "database", "sql", "model", "schema", "migration", "query", "orm"],
        "config": ["config", "setting", "env", "yaml", "json", "toml", "ini"],
        "test": ["test", "spec", "mock", "assert", "pytest", "jest"],
        "frontend": ["component", "react", "vue", "svelte", "template", "css", "html", "ui"],
        "build": ["build", "webpack", "vite", "rollup", "babel", "tsconfig", "make"],
        "deploy": ["deploy", "docker", "ci", "cd", "pipeline", "action", "workflow"],
    }

    def build_prompt_block(
        self,
        memory_manager,
        session_id: int,
        include_long_term: bool = False,
        user_input: str = "",
    ) -> str:
        parts = []

        wm = memory_manager.working.to_prompt_block(max_chars=600)
        if wm:
            parts.append(wm)

        remaining = self.MAX_CHARS - sum(len(p) + 2 for p in parts)

        repo = self._get_repo_block(memory_manager, session_id, user_input, max_chars=min(remaining, 1200))
        if repo:
            parts.append(repo)
            remaining = self.MAX_CHARS - sum(len(p) + 2 for p in parts)

        exec_block = memory_manager.execution.get_prompt_block(session_id)
        if exec_block:
            if len(exec_block) + 2 > remaining:
                exec_lines = exec_block.splitlines()
                exec_block = "\n".join(exec_lines[:4])
                if len(exec_block) + 2 > remaining:
                    exec_block = exec_block[:remaining - 2]
            if exec_block:
                parts.append(exec_block)
                remaining = self.MAX_CHARS - sum(len(p) + 2 for p in parts)

        ep = memory_manager.episodic.get_prompt_block(session_id, max_chars=min(remaining, 800))
        if ep:
            parts.append(ep)
            remaining = self.MAX_CHARS - sum(len(p) + 2 for p in parts)

        mutation_block = memory_manager.mutation.get_prompt_block(session_id)
        if mutation_block:
            if len(mutation_block) + 2 > remaining:
                mutation_lines = mutation_block.splitlines()
                mutation_block = "\n".join(mutation_lines[:4])
            if mutation_block:
                parts.append(mutation_block)
                remaining = self.MAX_CHARS - sum(len(p) + 2 for p in parts)

        if include_long_term and remaining > 200:
            ltm = memory_manager.long_term.to_prompt_block(max_chars=min(remaining, 400))
            if ltm:
                parts.append(ltm)

        block = "\n\n".join(parts)
        if len(block) > self.MAX_CHARS:
            block = block[:self.MAX_CHARS] + "\n... (truncated)"
        return block

    def _get_repo_block(
        self, memory_manager, session_id: int, user_input: str, max_chars: int
    ) -> str:
        mem = memory_manager.repo._get(session_id)
        if not mem.files:
            return ""

        query_tokens = set(re.findall(r'\w+', user_input.lower())) if user_input else set()
        scored = []
        for path, rec in mem.files.items():
            if query_tokens:
                text_blob = f"{rec.path} {rec.purpose} {' '.join(rec.functions)} {' '.join(rec.classes)} {' '.join(rec.imports)}"
                relevance = self.semantic.relevance(user_input, text_blob)
            else:
                relevance = 0.5
            score = 0.7 * rec.importance_score + 0.3 * relevance
            scored.append((score, path, rec))

        scored.sort(key=lambda x: -x[0])

        parts = ["KNOWN REPO STATE"]
        if mem.framework:
            parts.append(f"Framework: {mem.framework}")

        file_lines = []
        for score, path, rec in scored[:8]:
            desc = f"  {path} ({rec.language}, {rec.lines} lines, imp={score:.2f})"
            if rec.purpose:
                desc += f" \u2014 {rec.purpose[:50]}"
            file_lines.append(desc)
        if file_lines:
            parts.append(f"Files ({len(mem.files)}):\n" + "\n".join(file_lines))

        sym_lines = []
        for score, path, rec in scored[:5]:
            syms = (rec.functions + rec.classes)[:6]
            if syms:
                sym_lines.append(f"  {path}: {', '.join(syms)}")
        if sym_lines:
            parts.append("Key symbols:\n" + "\n".join(sym_lines))

        if mem.entrypoints:
            parts.append("Entry points:\n  " + "\n  ".join(mem.entrypoints[:3]))

        if mem.routes:
            parts.append("Routes:\n  " + ", ".join(mem.routes[:10]))

        block = "\n\n".join(parts)
        if len(block) > max_chars:
            return block[:max_chars] + "\n... (truncated)"
        return block

    def get_priority_ranking(
        self, memory_manager, session_id: int, user_input: str = "",
    ) -> dict[str, str]:
        return {
            "objective": memory_manager.working.objective,
            "repo_files": str(len(memory_manager.repo._get(session_id).files)),
            "recent_commands": str(len(memory_manager.execution._get(session_id).commands)),
            "findings": str(len(memory_manager.episodic._get(session_id).root_causes)),
        }


# ── Compression Controller ──────────────────────────────────────────────

class CompressionController:
    """Prevent uncontrolled memory growth."""

    def compress_episodic(self, episodic_memory, session_id: int) -> None:
        episode = episodic_memory._get(session_id)
        if len(episode.root_causes) > 10:
            episode.root_causes = episode.root_causes[-10:]
        if len(episode.hypotheses) > 10:
            episode.hypotheses = episode.hypotheses[-10:]
        if len(episode.edits) > 20:
            episode.edits = episode.edits[-20:]
        if len(episode.decisions) > 20:
            episode.decisions = episode.decisions[-20:]
        if len(episode.successful_strategies) > 10:
            episode.successful_strategies = episode.successful_strategies[-10:]
        if len(episode.failed_strategies) > 10:
            episode.failed_strategies = episode.failed_strategies[-10:]

    def compress_long_term(self, long_term_memory) -> int:
        return long_term_memory.compress(keep_top=500)

    def compress_execution(self, execution_memory, session_id: int) -> None:
        mem = execution_memory._get(session_id)
        if len(mem.commands) > mem.max_commands:
            mem.commands = mem.commands[-mem.max_commands:]
            keys = list(mem._index.keys())
            for k in keys[:max(0, len(keys) - mem.max_commands)]:
                mem._index.pop(k, None)


# ── Semantic Retriever (v3 placeholder) ─────────────────────────────────

class SemanticRetriever:
    """Placeholder for future embedding-based semantic retrieval.

    Designed so that swapping in a real embedding model only requires
    implementing ``_embed(text) -> list[float]`` and updating
    ``is_available()`` to return ``True``.

    Current behaviour: falls back to keyword/TF-IDF-like scoring via
    ``_keyword_score()`` when no embedding provider is configured.
    """

    _EMBEDDING_DIM = 384  # typical for all-MiniLM-L6-v2; adjust per model

    def __init__(self) -> None:
        self._provider_name: str | None = None  # set to "openai", "sentence-transformers", etc.

    # ── Public API ──────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True when a real embedding provider is configured."""
        return self._provider_name is not None

    def configure(self, provider: str, **kwargs) -> None:
        """Register an embedding provider (stub — no-op until integration)."""
        self._provider_name = provider
        # TODO: initialise model client here once a provider is integrated

    def rank_texts(
        self,
        query: str,
        texts: list[tuple[str, str]],  # (id, text) pairs
        top_k: int = 8,
    ) -> list[tuple[str, float]]:
        """Return top-k (id, score) pairs sorted by relevance to query.

        Falls back to keyword scoring when no embedding provider
        is configured.
        """
        if self.is_available():
            return self._rank_semantic(query, texts, top_k)
        return self._rank_keyword(query, texts, top_k)

    # ── Embedding stubs ─────────────────────────────────────────────────

    def _embed(self, text: str) -> list[float]:
        """Return embedding vector. Stub — override when provider added."""
        if not self.is_available():
            return []
        # TODO: call external embedding API / model here
        return [0.0] * self._EMBEDDING_DIM

    def _similarity(self, a: list[float], b: list[float]) -> float:
        """Cosine similarity between two vectors."""
        if not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    # ── Ranking strategies ──────────────────────────────────────────────

    def _rank_semantic(
        self,
        query: str,
        texts: list[tuple[str, str]],
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Rank by embedding cosine similarity (stub — returns keyword scores)."""
        return self._rank_keyword(query, texts, top_k)

    def relevance(self, query: str, text: str) -> float:
        """Single relevance score 0.0–1.0 between query and a text blob.

        Uses embedding similarity when available, otherwise keyword overlap.
        """
        if not query or not text:
            return 0.5
        if self.is_available():
            q_emb = self._embed(query)
            t_emb = self._embed(text)
            return self._similarity(q_emb, t_emb)
        q_tokens = set(re.findall(r'\w+', query.lower()))
        text_lower = text.lower()
        if not q_tokens:
            return 0.5
        matches = sum(1 for t in q_tokens if t in text_lower)
        return min(1.0, matches / len(q_tokens))

    def _rank_keyword(
        self,
        query: str,
        texts: list[tuple[str, str]],
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Rank by simple keyword overlap (working fallback)."""
        if not query or not texts:
            return texts[:top_k] if texts else []

        q_tokens = set(re.findall(r'\w+', query.lower()))
        if not q_tokens:
            return [(tid, 0.5) for tid, _ in texts[:top_k]]

        scored = []
        for tid, text in texts:
            text_lower = text.lower()
            matches = sum(1 for t in q_tokens if t in text_lower)
            score = matches / max(1, len(q_tokens))
            scored.append((tid, round(score, 4)))

        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


# ── Confidence Controller (v3) ──────────────────────────────────────────

class ConfidenceController:
    """Score memory reliability — age, edits, hash matches, success rates.

    Every score is 0.0–1.0. Used by PolicyController to decide
    whether to REUSE cached knowledge or READ/EXECUTE fresh.
    """

    def score_file(
        self, repo_memory, session_id: int, path: str, current_turn: int,
    ) -> float:
        """Confidence that cached file summary is still accurate."""
        rec = repo_memory.get_file(session_id, path)
        if not rec:
            return 0.0

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                current_hash = hashlib.sha256(f.read().replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]
            hash_match = 1.0 if current_hash == rec.hash else 0.0
        except Exception:
            hash_match = 0.5

        recency = min(1.0, 20.0 / max(1, current_turn - rec.last_read_turn + 1))
        read_depth = min(1.0, rec.read_count / 5.0)

        score = (
            0.35 * rec.importance_score +
            0.25 * recency +
            0.20 * read_depth +
            0.20 * hash_match
        )
        return round(min(1.0, max(0.0, score)), 3)

    def score_command(
        self, execution_memory, session_id: int, command: str, cwd: str,
        current_state_hash: str = "",
    ) -> float:
        """Confidence that cached command result is still valid."""
        mem = execution_memory._get(session_id)
        matching = [c for c in mem.commands if c.command == command and c.cwd == cwd]
        if not matching:
            return 0.0

        latest = matching[-1]
        success_rate = sum(1 for c in matching if c.exit_code == 0) / max(1, len(matching))

        age_s = time.time() - latest.timestamp
        recency = max(0.0, 1.0 - age_s / 3600.0)

        state_stable = 1.0
        if current_state_hash and latest.state_hash:
            state_stable = 1.0 if current_state_hash == latest.state_hash else 0.3

        score = (
            0.50 * success_rate +
            0.20 * recency +
            0.15 * state_stable +
            0.15 * min(1.0, latest.duration_ms / 30000.0 if latest.duration_ms > 0 else 0.5)
        )
        return round(min(1.0, max(0.0, score)), 3)

    def score_strategy(self, episodic_memory, session_id: int, strategy: str) -> float:
        """Confidence that a strategy will work (0.0–1.0)."""
        ep = episodic_memory._get(session_id)
        result = ep.was_strategy_tried(strategy)
        if result is None:
            return 0.5
        return 0.85 if result == "success" else 0.15

    def detect_conflicts(self, repo_memory, session_id: int) -> list[dict]:
        """Return list of file hash mismatches (file changed on disk)."""
        mem = repo_memory._get(session_id)
        conflicts = []
        for path, rec in mem.files.items():
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    current_hash = hashlib.sha256(f.read().replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]
                if current_hash != rec.hash:
                    conflicts.append({
                        "type": "file_hash_mismatch",
                        "path": path,
                        "stored_hash": rec.hash,
                        "current_hash": current_hash,
                    })
            except Exception as _e:
                _logger.debug("detect_file_conflicts open: %s", _e)
        return conflicts

    def score_overall(self, memory_manager, session_id: int, current_turn: int) -> float:
        """Aggregate confidence across all memory layers (0.0–1.0)."""
        scores = []
        repo = memory_manager.repo._get(session_id)
        if repo.files:
            file_confs = [
                self.score_file(memory_manager.repo, session_id, p, current_turn)
                for p in list(repo.files.keys())[:5]
            ]
            scores.append((sum(file_confs) / max(1, len(file_confs))) * 0.5)

        exec_mem = memory_manager.execution._get(session_id)
        if exec_mem.commands:
            recent = exec_mem.commands[-5:]
            cmd_scores = [1.0 if c.exit_code == 0 else 0.0 for c in recent]
            scores.append((sum(cmd_scores) / len(cmd_scores)) * 0.3)

        scores.append(memory_manager.working.confidence * 0.2)

        return round(min(1.0, max(0.0, sum(scores))), 3)


# ── Policy Controller (v3) ──────────────────────────────────────────────

class PolicyController:
    """Action selection — decides REUSE / READ / EXECUTE / INVESTIGATE / OVERRIDE.

    REUSE        — confidence ≥ 0.75 (files) / 0.80 (commands): skip tool,
                   inject cached knowledge directly.
    READ/EXECUTE — confidence 0.35–0.75: allow tool, attach memory context.
    INVESTIGATE  — confidence < 0.35 or no cache: allow tool, note insufficient.
    OVERRIDE     — user explicitly requested execution.
    """

    ACTIONS = ("REUSE", "READ", "EXECUTE", "INVESTIGATE", "OVERRIDE")

    def decide(
        self,
        memory_manager,
        session_id: int,
        tool_name: str,
        args: dict,
        current_turn: int,
        user_override: bool = False,
    ) -> str:
        """Determine the best action for this tool call."""
        if user_override:
            return "OVERRIDE"

        if tool_name == "read_file":
            return self._decide_read(memory_manager, session_id, args, current_turn)

        if tool_name in ("run_command", "shell_exec"):
            return self._decide_execute(memory_manager, session_id, args, current_turn)

        if tool_name in ("grep", "search_code", "semantic_search", "fts_search", "ast_search"):
            return self._decide_search(memory_manager, session_id, args)

        return "EXECUTE"

    def _decide_read(self, memory_manager, session_id, args, current_turn) -> str:
        path = args.get("path", "")
        if not path:
            return "EXECUTE"

        conflicts = memory_manager.confidence.detect_conflicts(memory_manager.repo, session_id)
        if any(c.get("path") == path for c in conflicts):
            return "READ"

        conf = memory_manager.confidence.score_file(
            memory_manager.repo, session_id, path, current_turn,
        )

        if conf >= 0.75:
            return "REUSE"
        if conf >= 0.35:
            return "READ"
        return "INVESTIGATE"

    def _decide_execute(self, memory_manager, session_id, args, current_turn) -> str:
        command = args.get("command", "") or args.get("cmd", "")
        if not command:
            return "EXECUTE"

        cwd = args.get("cwd") or os.getcwd()
        state_hash = compute_state_hash(cwd)
        conf = memory_manager.confidence.score_command(
            memory_manager.execution, session_id, command, cwd, state_hash,
        )

        if conf >= 0.80:
            return "REUSE"
        if conf >= 0.35:
            return "EXECUTE"
        return "INVESTIGATE"

    def _decide_search(self, memory_manager, session_id, args) -> str:
        repo = memory_manager.repo._get(session_id)
        if len(repo.files) >= 5 and len(repo.files) >= len(repo.entrypoints) * 2:
            return "INVESTIGATE"
        return "EXECUTE"


# ── Retry Controller (v3) ───────────────────────────────────────────────

class RetryController:
    """3-tier retry strategy for tool execution failures.

    Tier 1 — RETRY:      Same tool, same args — try once more.
    Tier 2 — ALTERNATE:  Same goal, modified args (flags, encoding, etc).
    Tier 3 — SURFACE:    Admit failure, return descriptive error.

    Tiers reset per (tool_name, args_signature) when a call succeeds.
    """

    MAX_RETRIES = {
        "run_command": 1,      # one retry before alternate
        "shell_exec": 1,
        "read_file": 1,
        "write_file": 1,
        "edit_file": 1,
        "multi_edit": 1,
        "grep": 0,             # no retry — re-run with different pattern
        "glob": 0,
    }

    def __init__(self):
        self._failures: dict[str, int] = {}  # key → consecutive failure count
        self._lock = threading.RLock()

    def _key(self, tool_name: str, args: dict) -> str:
        p = args.get("path") or args.get("command") or args.get("pattern") or ""
        return f"{tool_name}::{p[:80]}"

    def record_success(self, tool_name: str, args: dict) -> None:
        with self._lock:
            self._failures.pop(self._key(tool_name, args), None)

    def get_tier(self, tool_name: str, args: dict) -> str:
        """Return 'RETRY', 'ALTERNATE', or 'SURFACE'."""
        key = self._key(tool_name, args)
        with self._lock:
            count = self._failures.get(key, 0)
            self._failures[key] = count + 1

            max_retries = self.MAX_RETRIES.get(tool_name, 1)
            if count < max_retries:
                return "RETRY"
            if count < max_retries + 1:
                return "ALTERNATE"
            return "SURFACE"

    def suggest_alternative(self, tool_name: str, args: dict) -> dict | None:
        """Return modified args for Tier 2 ALTERNATE, or None if no
        sensible alternative exists."""
        if tool_name in ("run_command", "shell_exec"):
            cmd = args.get("command", "") or args.get("cmd", "")
            if not cmd:
                return None
            alt = dict(args)
            if "|" in cmd:
                alt["command"] = cmd.rsplit("|", 1)[0].strip()
                return alt
            if "--force" not in cmd and "--yes" not in cmd and "-y" not in cmd:
                base = cmd.split("|")[0].strip()
                alt["command"] = base + " --force"
                return alt
            return None

        if tool_name == "read_file":
            alt = dict(args)
            alt["encoding"] = alt.get("encoding") or "utf-8"
            return alt

        if tool_name == "write_file":
            path = args.get("path", "")
            if not path:
                return None
            alt = dict(args)
            parent = os.path.dirname(path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
                return alt
            return None

        if tool_name in ("edit_file", "multi_edit"):
            path = args.get("path", "")
            if path:
                alt = dict(args)
                alt["re_read"] = True
                return alt
            return None

        if tool_name in ("grep", "glob"):
            alt = dict(args)
            alt["case_sensitive"] = False
            return alt

        return None
