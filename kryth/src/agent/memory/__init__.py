"""Agent memory package — unified 5-layer memory architecture.

Memory Layers:
  1. WorkingMemory    — per-turn reasoning state (objective, blockers, next_action)
  2. RepoMemory       — repository structure (files, symbols, routes, frameworks)
  3. ExecutionMemory  — command execution history (prevent duplicate runs)
  4. EpisodicMemory   — mission-level task memory (root causes, edits, decisions)
  5. LongTermMemory   — persistent strategic memory across sessions (SQLite)

Controllers:
  - WriteController        — single writer for all memory layers
  - RetrievalController    — selective memory retrieval for LLM context
  - DuplicateDetector      — detect duplicate reads/commands/edits
  - CompressionController  — prevent uncontrolled memory growth

Single entrypoint: MemoryManager (session.memory_manager)
"""

from agent.memory.memory_manager import MemoryManager
from agent.memory.working_memory import WorkingMemory
from agent.memory.repo_memory import RepoMemory, RepoMemoryManager, FileRecord
from agent.memory.execution_memory import ExecutionMemory, ExecutionMemoryManager, CommandRecord
from agent.memory.episodic_memory import Episode, EpisodicMemoryManager
from agent.memory.long_term_memory import LongTermMemory, LTMemoryEntry
from agent.memory.mutation_memory import MutationRecord, MutationMemory, MutationMemoryManager
from agent.memory.controllers import (
    WriteController,
    RetrievalController,
    DuplicateDetector,
    CompressionController,
    compute_state_hash,
)

# ── Backward compatibility: keep old caches working ─────────────────────

from agent.memory.read_memory import (
    ReadMemory,
    ReadMemoryManager,
    CachedRead,
    get_read_memory,
    record_read_file,
    get_cached_read,
    record_list_files,
    get_cached_listdir,
    record_search,
    get_cached_search,
    get_context_summary,
    should_reread_file,
    clear_session,
)

from agent.memory.post_read_summarizer import (
    FileSummary,
    PostReadSummarizer,
    summarize_file,
    get_cached_summary,
    invalidate_summary,
)

__all__ = [
    # New public API
    "MemoryManager",
    "WorkingMemory",
    "RepoMemory",
    "RepoMemoryManager",
    "FileRecord",
    "ExecutionMemory",
    "ExecutionMemoryManager",
    "CommandRecord",
    "Episode",
    "EpisodicMemoryManager",
    "LongTermMemory",
    "LTMemoryEntry",
    "WriteController",
    "RetrievalController",
    "DuplicateDetector",
    "CompressionController",
    # Backward compat
    "ReadMemory",
    "ReadMemoryManager",
    "CachedRead",
    "get_read_memory",
    "record_read_file",
    "get_cached_read",
    "record_list_files",
    "get_cached_listdir",
    "record_search",
    "get_cached_search",
    "get_context_summary",
    "should_reread_file",
    "clear_session",
    "FileSummary",
    "PostReadSummarizer",
    "summarize_file",
    "get_cached_summary",
    "invalidate_summary",
]