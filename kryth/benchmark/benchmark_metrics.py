"""Metric data structures and the MetricsCollector context manager.

MetricsCollector subscribes to agent.ui events via the event bus and
captures fine-grained timing without modifying any agent source files.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any


# ── Fine-grained timings ──────────────────────────────────────────────────────

@dataclass
class MissionTimings:
    """All timing values are milliseconds from mission start (t=0)."""
    start_wall: float = 0.0             # absolute epoch seconds (for display)
    first_tool_call_ms: float = -1.0    # -1 = not reached
    first_read_ms: float = -1.0
    first_write_ms: float = -1.0
    first_stream_begin_ms: float = -1.0
    first_chunk_ms: float = -1.0
    first_test_ms: float = -1.0
    first_bg_validation_ms: float = -1.0
    first_dashboard_update_ms: float = -1.0
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ── Sub-system metric groups ──────────────────────────────────────────────────

@dataclass
class AgentMetrics:
    peak_active: int = 0
    total_spawned: int = 0
    dynamic_spawns: int = 0         # spawns from worker_pool early-spawn
    idle_slots_at_end: int = 0
    work_steal_count: int = 0
    utilization_pct: float = 0.0    # active_time / total_time * 100


@dataclass
class StreamingMetrics:
    streams_started: int = 0
    total_chunks: int = 0
    total_lines_streamed: int = 0
    avg_chunk_lines: float = 0.0
    syntax_errors_caught_mid_stream: int = 0
    finalized_streams: int = 0


@dataclass
class MemoryMetrics:
    speculative_preload_files: int = 0      # files loaded before LLM
    worker_pool_preload_files: int = 0      # files loaded by worker pool
    experience_hits: int = 0
    graph_hits: int = 0
    preload_wait_ms: float = 0.0            # time spent waiting for preload


@dataclass
class RecoveryMetrics:
    failures_injected: int = 0
    auto_recoveries: int = 0
    repair_loop_calls: int = 0
    human_interventions: int = 0
    recovery_rate_pct: float = 0.0


@dataclass
class ParallelMetrics:
    total_tool_batches: int = 0         # turns where tool_calls >= 1
    parallel_batches: int = 0           # turns where tool_calls > 1
    max_batch_size: int = 0             # largest single parallel dispatch
    total_parallel_calls: int = 0       # sum of all batch sizes > 1
    parallel_efficiency_pct: float = 0.0  # parallel_calls / total_calls * 100


# ── Top-level mission and run containers ──────────────────────────────────────

@dataclass
class MissionMetrics:
    mission_id: str = ""
    mission_name: str = ""
    prompt: str = ""
    workspace: str = ""
    success: bool = False
    error: str = ""
    timings: MissionTimings = field(default_factory=MissionTimings)
    tokens_in: int = 0
    tokens_out: int = 0
    total_tool_calls: int = 0
    files_written: int = 0
    files_read: int = 0
    commands_run: int = 0
    turns_used: int = 0
    agents: AgentMetrics = field(default_factory=AgentMetrics)
    streaming: StreamingMetrics = field(default_factory=StreamingMetrics)
    memory: MemoryMetrics = field(default_factory=MemoryMetrics)
    recovery: RecoveryMetrics = field(default_factory=RecoveryMetrics)
    parallel: ParallelMetrics = field(default_factory=ParallelMetrics)

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)


@dataclass
class BenchmarkRun:
    run_id: str = ""
    timestamp: str = ""
    kryth_version: str = "unknown"
    timeout_s: int = 300
    missions: list[MissionMetrics] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.missions)

    @property
    def passed(self) -> int:
        return sum(1 for m in self.missions if m.success)

    @property
    def failed(self) -> int:
        return sum(1 for m in self.missions if not m.success)

    @property
    def pass_rate_pct(self) -> float:
        return self.passed / self.total * 100 if self.total else 0.0

    @property
    def avg_duration_ms(self) -> float:
        d = [m.timings.duration_ms for m in self.missions]
        return sum(d) / len(d) if d else 0.0

    @property
    def total_tokens(self) -> int:
        return sum(m.tokens_in + m.tokens_out for m in self.missions)

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)


# ── Event-bus metrics collector ───────────────────────────────────────────────

class MetricsCollector:
    """Subscribes to agent.ui events to capture fine-grained timing.

    Usage::

        collector = MetricsCollector(metrics)
        with collector:
            run_agent(prompt)
        # metrics now populated

    The collector uses the existing agent.ui event bus — no source files
    are modified. It unsubscribes automatically on __exit__.
    """

    def __init__(self, metrics: MissionMetrics) -> None:
        self._m = metrics
        self._start: float = 0.0
        self._unsub: list[Any] = []
        self._lock = threading.Lock()
        # per-turn tool-call batch tracking
        self._current_batch: int = 0
        self._in_batch = False

    def _elapsed_ms(self) -> float:
        return (time.monotonic() - self._start) * 1000.0

    def _record_once(self, field: str, ms: float) -> None:
        with self._lock:
            if getattr(self._m.timings, field) < 0:
                setattr(self._m.timings, field, ms)

    def _on_event(self, event) -> None:
        kind = getattr(event, "kind", "") or ""
        # EventKind is a str-enum — normalise to plain string value
        kind_str = kind.value if hasattr(kind, "value") else str(kind)
        data = getattr(event, "data", {}) or {}
        ms = self._elapsed_ms()

        if kind_str == "tool.start":
            name = data.get("name", "")
            self._record_once("first_tool_call_ms", ms)
            self._m.total_tool_calls += 1
            if name in ("read_file", "grep", "glob", "search_code",
                        "semantic_search", "list_files"):
                self._record_once("first_read_ms", ms)
                self._m.files_read += 1
            elif name in ("write_file", "multi_edit"):
                self._record_once("first_write_ms", ms)
                self._m.files_written += 1
            elif name == "write_file_begin":
                self._record_once("first_stream_begin_ms", ms)
                self._m.streaming.streams_started += 1
            elif name == "write_file_chunk":
                self._record_once("first_chunk_ms", ms)
                self._m.streaming.total_chunks += 1
                lines = len((data.get("args") or {}).get("content", "").splitlines())
                self._m.streaming.total_lines_streamed += lines
            elif name == "write_file_finalize":
                self._m.streaming.finalized_streams += 1
            elif name in ("run_command", "shell_exec", "execute_command"):
                self._m.commands_run += 1
                cmd = str((data.get("args") or {}).get("command", "")).lower()
                if any(t in cmd for t in ("pytest", "jest", "npm test",
                                          "vitest", "cargo test")):
                    self._record_once("first_test_ms", ms)

        elif kind_str == "llm.usage":
            self._m.tokens_in += data.get("turn_in", 0)
            self._m.tokens_out += data.get("turn_out", 0)

        elif kind_str == "subagent.start":
            self._record_once("first_dashboard_update_ms", ms)
            self._m.agents.total_spawned += 1
            self._m.agents.dynamic_spawns += 1
            current = self._m.agents.peak_active + 1
            if current > self._m.agents.peak_active:
                self._m.agents.peak_active = current

        elif kind_str in ("stream_error", "tool.error"):
            self._m.streaming.syntax_errors_caught_mid_stream += 1

    def __enter__(self) -> "MetricsCollector":
        self._start = time.monotonic()
        self._m.timings.start_wall = time.time()
        try:
            from agent.ui.events import BUS
            unsub = BUS.subscribe(self._on_event)
            self._unsub.append(unsub)
        except Exception:
            pass
        # Capture session reference now (in agent thread context) for post-run reads
        try:
            from agent.session import get_session
            self._session = get_session()
        except Exception:
            self._session = None
        # Inject memory/worker-pool preload counts after a short delay
        self._probe_thread = threading.Thread(
            target=self._probe_preloads, daemon=True)
        self._probe_thread.start()
        return self

    def __exit__(self, *_) -> None:
        for unsub in self._unsub:
            try:
                unsub()
            except Exception:
                pass
        self._unsub.clear()
        self._m.timings.duration_ms = self._elapsed_ms()
        # Read parallel dispatch stats from session (written by tool_scheduler)
        if self._session is not None:
            pb = getattr(self._session, "_par_batches", 0)
            pc = getattr(self._session, "_par_calls", 0)
            pm = getattr(self._session, "_par_max", 0)
            if pb > 0:
                self._m.parallel.parallel_batches = pb
                self._m.parallel.total_parallel_calls = pc
                self._m.parallel.max_batch_size = pm
        # Finalize parallel efficiency
        tc = self._m.total_tool_calls
        pc = self._m.parallel.total_parallel_calls
        if tc > 0:
            self._m.parallel.parallel_efficiency_pct = pc / tc * 100

    def _probe_preloads(self) -> None:
        """Read worker-pool and speculative-results after ~1s warm-up."""
        time.sleep(1.0)
        try:
            from agent.orchestration.worker_pool import get_worker_pool
            pool = get_worker_pool()
            for w in pool._workers.values():
                self._m.memory.worker_pool_preload_files += len(w.preloaded_files)
        except Exception:
            pass
        try:
            s = self._session
            if s is not None:
                sr = getattr(s, "_speculative_results", {})
                self._m.memory.speculative_preload_files = len(sr.get("files", []))
                exp_hits = getattr(s, "_experience_hits", 0)
                self._m.memory.experience_hits = exp_hits
        except Exception:
            pass
