"""Dependency-aware work stealing & non-idle agents.

Extends the existing work-stealing model (``work_queue.WorkQueue``) with a
dependency-aware task lifecycle so a worker is **never idle while useful work
exists**. When a worker's task blocks on an unfinished dependency, the task is
returned to the queue as RESUMABLE and the worker immediately steals another
READY task — exactly how a real engineering team works: nobody sits waiting for
the backend, they pick up the next unblocked thing.

This is additive. It does NOT modify ``WorkQueue``, the scheduler, the DAG, the
Planner, the UAL, the Executive, or Mission OS. The existing scheduler can opt
into this queue; until it does, nothing changes. The module is pure-Python and
fully deterministic (time is injected), so the before/after benchmark proves the
utilization win without a live model.

Lifecycle (Phase 1)::

    READY → RUNNING → COMPLETE
              │
              ├─(hits missing dep)→ WAITING_DEPENDENCY → (dep ready) → RESUMABLE → RUNNING
              └─(error)──────────→ FAILED

PAUSED is an explicit hold; BLOCKED is a hard block with no known producer.

Key ideas:
* **Dependency Registry** (Phase 2): dep → producer, consumers, status, ready_time.
* **Block-and-resteal** (Phase 3): blocked task saved + worker pulls new READY work.
* **Resume, not restart** (Phase 4): a resumed task keeps its ``progress`` payload.
* **Partial unlocking** (Phase 5): a task requires only the deps it lists.
* **Contract-first** (Phase 6): a declared interface satisfies a dep early.
* **Utilization** (Phase 7) + **critical-path** (Phase 9) reporting.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional


class TaskState(Enum):
    READY = "ready"
    RUNNING = "running"
    WAITING_DEPENDENCY = "waiting_dependency"
    BLOCKED = "blocked"
    PAUSED = "paused"
    RESUMABLE = "resumable"
    COMPLETE = "complete"
    FAILED = "failed"


class DepStatus(Enum):
    PENDING = "pending"        # no producer has finished
    CONTRACT = "contract"      # interface declared — consumers may start (Phase 6)
    READY = "ready"            # producer finished


# ── Dependency Registry (Phase 2) ────────────────────────────────────────────

@dataclass
class DepRecord:
    name: str
    producer: str = ""
    consumers: List[str] = field(default_factory=list)
    status: DepStatus = DepStatus.PENDING
    ready_time: float = -1.0
    declared_time: float = -1.0


class DependencyRegistry:
    """Central registry of named dependencies (files, APIs, contracts)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._deps: Dict[str, DepRecord] = {}

    def _rec(self, name: str) -> DepRecord:
        r = self._deps.get(name)
        if r is None:
            r = DepRecord(name=name)
            self._deps[name] = r
        return r

    def declare_producer(self, name: str, producer: str) -> None:
        with self._lock:
            self._rec(name).producer = producer

    def add_consumer(self, name: str, consumer: str) -> None:
        with self._lock:
            r = self._rec(name)
            if consumer not in r.consumers:
                r.consumers.append(consumer)

    def declare_contract(self, name: str, now: float = 0.0) -> None:
        """Phase 6 — an interface/schema/contract exists. Consumers may begin
        against it even though the real implementation isn't finished."""
        with self._lock:
            r = self._rec(name)
            if r.status is DepStatus.PENDING:
                r.status = DepStatus.CONTRACT
                r.declared_time = now

    def satisfy(self, name: str, now: float = 0.0) -> None:
        """Producer finished — the dependency is fully ready."""
        with self._lock:
            r = self._rec(name)
            r.status = DepStatus.READY
            r.ready_time = now

    def is_ready(self, name: str) -> bool:
        """Ready if fully produced OR a contract has been declared (Phase 6)."""
        with self._lock:
            r = self._deps.get(name)
            return bool(r and r.status in (DepStatus.READY, DepStatus.CONTRACT))

    def record(self, name: str) -> Optional[DepRecord]:
        with self._lock:
            return self._deps.get(name)

    def snapshot(self) -> List[dict]:
        with self._lock:
            return [
                {"dependency": r.name, "producer": r.producer,
                 "consumers": list(r.consumers), "status": r.status.value,
                 "ready_time": r.ready_time}
                for r in self._deps.values()
            ]


# ── Task ─────────────────────────────────────────────────────────────────────

@dataclass
class DepTask:
    task_id: str
    description: str = ""
    requires: List[str] = field(default_factory=list)   # dep names needed before/at runtime
    produces: List[str] = field(default_factory=list)   # dep names this task creates
    priority: int = 5                                    # lower = sooner
    state: TaskState = TaskState.READY
    # resume support (Phase 4) — opaque payload the worker saves/restores.
    progress: dict = field(default_factory=dict)
    # metrics (Phase 1 / 7)
    resume_count: int = 0
    blocked_duration: float = 0.0
    dependency_wait_time: float = 0.0
    started_at: float = -1.0
    _blocked_since: float = -1.0
    blocked_on: str = ""

    def __lt__(self, other: "DepTask") -> bool:
        return self.priority < other.priority


# ── Dependency-aware queue (Phases 1,3,4,5) ──────────────────────────────────

class DependencyAwareQueue:
    """Work-stealing queue where blocked tasks free their worker.

    Time is injected (``now`` args) so behavior is deterministic and testable.
    """

    def __init__(self, registry: Optional[DependencyRegistry] = None) -> None:
        self._lock = threading.RLock()
        self.registry = registry or DependencyRegistry()
        self._tasks: Dict[str, DepTask] = {}
        self._order: List[str] = []          # all task ids, priority-sorted
        self.work_steals = 0
        self.dependency_events = 0           # dep-satisfaction events observed

    # -- submission ---------------------------------------------------------

    def add_task(self, task_id: str, *, description: str = "", requires=None,
                 produces=None, priority: int = 5, progress: Optional[dict] = None) -> DepTask:
        with self._lock:
            if task_id in self._tasks:
                return self._tasks[task_id]
            t = DepTask(task_id=task_id, description=description,
                        requires=list(requires or []), produces=list(produces or []),
                        priority=priority, progress=dict(progress or {}))
            self._tasks[task_id] = t
            self._order.append(task_id)
            self._order.sort(key=lambda i: self._tasks[i].priority)
            for d in t.produces:
                self.registry.declare_producer(d, task_id)
            for d in t.requires:
                self.registry.add_consumer(d, task_id)
            return t

    # -- readiness ----------------------------------------------------------

    def _deps_ready(self, t: DepTask) -> bool:
        return all(self.registry.is_ready(d) for d in t.requires)

    def _unmet(self, t: DepTask) -> str:
        for d in t.requires:
            if not self.registry.is_ready(d):
                return d
        return ""

    def next_ready(self, now: float = 0.0) -> Optional[DepTask]:
        """Claim the highest-priority task that can run now: a READY or RESUMABLE
        task whose required deps are all satisfied. Marks it RUNNING. Returns
        None only when no runnable work exists (the *only* legitimate idle)."""
        with self._lock:
            for tid in self._order:
                t = self._tasks[tid]
                if t.state in (TaskState.READY, TaskState.RESUMABLE) and self._deps_ready(t):
                    if t.state is TaskState.RESUMABLE:
                        t.resume_count += 1
                    t.state = TaskState.RUNNING
                    if t.started_at < 0:
                        t.started_at = now
                    return t
            return None

    # -- block & re-steal (Phase 3) -----------------------------------------

    def block(self, task_id: str, missing_dep: str, now: float = 0.0) -> None:
        """A running task discovered an unmet dependency. Save it as
        WAITING_DEPENDENCY (resumable) so the worker is freed to steal new work.
        The task's ``progress`` payload is preserved for resume (Phase 4)."""
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            t.state = (TaskState.WAITING_DEPENDENCY if self.registry.record(missing_dep)
                       else TaskState.BLOCKED)
            t.blocked_on = missing_dep
            t._blocked_since = now

    def block_and_resteal(self, task_id: str, missing_dep: str, now: float = 0.0):
        """Convenience for the worker loop: block the current task and return the
        next runnable task (or None). This is what guarantees non-idle workers —
        a blocked worker leaves with new work in hand whenever any exists."""
        with self._lock:
            self.block(task_id, missing_dep, now)
            nxt = self.next_ready(now)
            if nxt is not None:
                self.work_steals += 1
            return nxt

    def save_progress(self, task_id: str, progress: dict) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t:
                t.progress.update(progress or {})

    # -- completion & dependency production (Phases 4,5) --------------------

    def produce(self, task_id: str, dep: str, now: float = 0.0) -> List[str]:
        """A task produced a dependency. Satisfy it in the registry and move any
        WAITING consumers whose deps are now all met back to RESUMABLE. Returns
        the list of unblocked consumer task ids (Phase 5: only the consumers that
        actually required THIS dep, nothing else)."""
        with self._lock:
            self.registry.satisfy(dep, now)
            self.dependency_events += 1
            unblocked = []
            for tid in self._order:
                t = self._tasks[tid]
                if (t.state in (TaskState.WAITING_DEPENDENCY, TaskState.BLOCKED)
                        and dep in t.requires and self._deps_ready(t)):
                    waited = now - t._blocked_since if t._blocked_since >= 0 else 0.0
                    t.blocked_duration += max(0.0, waited)
                    t.dependency_wait_time += max(0.0, waited)
                    t._blocked_since = -1.0
                    t.blocked_on = ""
                    t.state = TaskState.RESUMABLE
                    unblocked.append(tid)
            return unblocked

    def complete(self, task_id: str, now: float = 0.0, *, auto_produce: bool = True) -> List[str]:
        """Mark a task COMPLETE. By default it auto-produces every dep it
        declared (the common case), unblocking consumers. Returns unblocked ids."""
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return []
            t.state = TaskState.COMPLETE
            unblocked = []
            if auto_produce:
                for d in t.produces:
                    unblocked.extend(self.produce(task_id, d, now))
            return unblocked

    def fail(self, task_id: str) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t:
                t.state = TaskState.FAILED

    def pause(self, task_id: str) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t.state in (TaskState.READY, TaskState.RESUMABLE, TaskState.RUNNING):
                t.state = TaskState.PAUSED

    def resume_paused(self, task_id: str) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t and t.state is TaskState.PAUSED:
                t.state = TaskState.RESUMABLE

    # -- introspection (Phases 7,8) -----------------------------------------

    def all_done(self) -> bool:
        with self._lock:
            return all(t.state in (TaskState.COMPLETE, TaskState.FAILED)
                       for t in self._tasks.values())

    def runnable_exists(self, now: float = 0.0) -> bool:
        with self._lock:
            return any(t.state in (TaskState.READY, TaskState.RESUMABLE) and self._deps_ready(t)
                       for t in self._tasks.values())

    def state_counts(self) -> dict:
        with self._lock:
            counts = {s.value: 0 for s in TaskState}
            for t in self._tasks.values():
                counts[t.state.value] += 1
            return counts

    def snapshot(self) -> List[dict]:
        """Dashboard-ready rows (Phase 8)."""
        with self._lock:
            return [
                {"task_id": t.task_id, "description": t.description,
                 "state": t.state.value, "blocked_by": t.blocked_on,
                 "resume_count": t.resume_count,
                 "dependency_wait_time": round(t.dependency_wait_time, 2),
                 "requires": list(t.requires), "produces": list(t.produces)}
                for t in self._tasks.values()
            ]

    def queue_depth(self, now: float = 0.0) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values()
                       if t.state in (TaskState.READY, TaskState.RESUMABLE))


# ── Critical-path dependency (Phase 9) ───────────────────────────────────────

def most_blocking_dependency(queue: DependencyAwareQueue, now: float = 0.0) -> dict:
    """The dependency blocking the most tasks right now + impact.

    Returns ``{dependency, blocking, workers_waiting, delay, impact}``. Pairs
    with the existing critical-path panel (see agent.ui.dag_analysis)."""
    waiting: Dict[str, int] = {}
    delay: Dict[str, float] = {}
    with queue._lock:
        for t in queue._tasks.values():
            if t.state in (TaskState.WAITING_DEPENDENCY, TaskState.BLOCKED) and t.blocked_on:
                waiting[t.blocked_on] = waiting.get(t.blocked_on, 0) + 1
                wait = now - t._blocked_since if t._blocked_since >= 0 else 0.0
                delay[t.blocked_on] = max(delay.get(t.blocked_on, 0.0), wait)
    if not waiting:
        return {"dependency": "", "blocking": 0, "workers_waiting": 0,
                "delay": 0.0, "impact": "none"}
    dep = max(waiting, key=lambda d: waiting[d])
    n = waiting[dep]
    impact = "high" if n >= 3 else "medium" if n >= 1 else "low"
    return {"dependency": dep, "blocking": n, "workers_waiting": n,
            "delay": round(delay.get(dep, 0.0), 2), "impact": impact}


# ── Utilization tracking (Phase 7) ───────────────────────────────────────────

@dataclass
class UtilizationReport:
    running_pct: float = 0.0
    blocked_pct: float = 0.0
    idle_pct: float = 0.0
    resume_pct: float = 0.0
    work_steals: int = 0
    dependency_wait_time: float = 0.0
    samples: int = 0

    def to_dict(self) -> dict:
        return {
            "running_pct": round(self.running_pct, 1),
            "blocked_pct": round(self.blocked_pct, 1),
            "idle_pct": round(self.idle_pct, 1),
            "resume_pct": round(self.resume_pct, 1),
            "work_steals": self.work_steals,
            "dependency_wait_time": round(self.dependency_wait_time, 2),
        }


class UtilizationTracker:
    """Aggregates worker-state samples into running/blocked/idle/resume %."""

    def __init__(self) -> None:
        self._running = self._blocked = self._idle = self._resume = self._n = 0

    def sample(self, *, running: int, blocked: int, idle: int, resuming: int = 0) -> None:
        self._running += running
        self._blocked += blocked
        self._idle += idle
        self._resume += resuming
        self._n += max(1, running + blocked + idle + resuming)

    def report(self, *, work_steals: int = 0, dependency_wait_time: float = 0.0) -> UtilizationReport:
        n = self._n or 1
        return UtilizationReport(
            running_pct=100 * self._running / n,
            blocked_pct=100 * self._blocked / n,
            idle_pct=100 * self._idle / n,
            resume_pct=100 * self._resume / n,
            work_steals=work_steals,
            dependency_wait_time=dependency_wait_time,
        )


# ── Benchmark (Phase 10) — deterministic before/after simulation ─────────────

@dataclass
class SimTask:
    """A unit of simulated work. ``pre`` ticks run, then (if ``needs`` is set and
    not yet produced) the task blocks until the dep is ready, then ``post`` ticks
    run; on completion it produces ``produces``."""
    id: str
    pre: int = 1
    post: int = 0
    needs: str = ""
    produces: str = ""


def _run_sim(tasks: List[SimTask], n_workers: int, *, steal: bool) -> dict:
    """Discrete tick simulator. ``steal=False`` is the baseline (a blocked worker
    idles holding its task); ``steal=True`` is dependency-aware (a blocked worker
    returns the task and pulls other runnable work). Deterministic: workers act
    in index order each tick."""
    produced: set = set()
    rem_pre = {t.id: t.pre for t in tasks}
    rem_post = {t.id: (t.post if t.needs else 0) for t in tasks}
    phase = {t.id: "pre" for t in tasks}     # pre | blocked | post | done
    by_id = {t.id: t for t in tasks}
    order = [t.id for t in tasks]
    started: set = set()
    worker_task: List[Optional[str]] = [None] * n_workers

    idle_ticks = 0
    busy_ticks = 0
    steals = 0
    max_ticks = (sum(t.pre + t.post for t in tasks) + len(tasks)) * (n_workers + 2) + 10
    tick = 0

    def _pick(exclude: set) -> Optional[str]:
        # A task is runnable if it hasn't finished and either is unstarted (can
        # run pre) or is resumable (blocked but its dep is now produced).
        for tid in order:
            if tid in exclude:
                continue
            t = by_id[tid]
            if phase[tid] == "done":
                continue
            if tid not in started:
                return tid
            if steal and phase[tid] == "blocked" and t.needs in produced:
                return tid
        return None

    while tick < max_ticks and any(phase[t.id] != "done" for t in tasks):
        held = {wt for wt in worker_task if wt}
        for w in range(n_workers):
            tid = worker_task[w]
            # Acquire work if free.
            if tid is None:
                tid = _pick(held)
                if tid is None:
                    idle_ticks += 1
                    continue
                if tid not in started:
                    started.add(tid)
                else:
                    steals += 1  # resuming previously-blocked work
                worker_task[w] = tid
                held.add(tid)
                if phase[tid] == "blocked" and by_id[tid].needs in produced:
                    phase[tid] = "post"

            t = by_id[tid]
            ph = phase[tid]
            if ph == "pre":
                rem_pre[tid] -= 1
                busy_ticks += 1
                if rem_pre[tid] <= 0:
                    if t.needs and t.needs not in produced:
                        phase[tid] = "blocked"
                        if steal:
                            worker_task[w] = None      # free the worker (Phase 3)
                    elif rem_post[tid] > 0:
                        phase[tid] = "post"
                    else:
                        phase[tid] = "done"
                        if t.produces:
                            produced.add(t.produces)
                        worker_task[w] = None
            elif ph == "blocked":
                if t.needs in produced:
                    phase[tid] = "post"
                    rem_post[tid] -= 1
                    busy_ticks += 1
                    if rem_post[tid] <= 0:
                        phase[tid] = "done"
                        if t.produces:
                            produced.add(t.produces)
                        worker_task[w] = None
                else:
                    idle_ticks += 1                    # baseline: worker stuck
            elif ph == "post":
                rem_post[tid] -= 1
                busy_ticks += 1
                if rem_post[tid] <= 0:
                    phase[tid] = "done"
                    if t.produces:
                        produced.add(t.produces)
                    worker_task[w] = None
        tick += 1

    total = busy_ticks + idle_ticks
    return {
        "makespan": tick,
        "idle_ticks": idle_ticks,
        "busy_ticks": busy_ticks,
        "work_steals": steals,
        "utilization_pct": round(100 * busy_ticks / total, 1) if total else 0.0,
    }


def benchmark_dependency_scheduling(tasks: List[SimTask], n_workers: int) -> dict:
    """Compare baseline (idle-while-blocked) vs dependency-aware (block-and-resteal)
    on the SAME task set + worker count. Returns both metric sets + deltas."""
    base = _run_sim(tasks, n_workers, steal=False)
    aware = _run_sim(tasks, n_workers, steal=True)
    def _imp(key, lower_better=True):
        b, a = base[key], aware[key]
        if b == 0:
            return 0.0
        d = (b - a) / b * 100
        return round(d if lower_better else -d, 1)
    return {
        "baseline": base,
        "dependency_aware": aware,
        "makespan_reduction_pct": _imp("makespan"),
        "idle_reduction_pct": _imp("idle_ticks"),
        "utilization_gain_pct": round(aware["utilization_pct"] - base["utilization_pct"], 1),
    }


def default_benchmark_mission() -> List[SimTask]:
    """A representative mission ordered the way a planner emits it: UI tasks
    (consumers) come first, their backing APIs (producers) later. A worker that
    grabs a consumer scaffolds it (pre) then needs the API — the moment of truth.

    Baseline: the blocked worker idles holding the task, starving the producers.
    Dependency-aware: the worker returns the task and steals a producer or
    independent task, so the producers get built and the consumers resume.
    """
    return [
        # consumers first (each scaffolds, then needs its API)
        SimTask("login_ui", pre=1, post=4, needs="auth"),
        SimTask("profile_ui", pre=1, post=4, needs="users"),
        SimTask("billing_ui", pre=1, post=4, needs="billing"),
        # producers later in planner order
        SimTask("auth_api", pre=4, produces="auth"),
        SimTask("users_api", pre=4, produces="users"),
        SimTask("billing_api", pre=4, produces="billing"),
        # a little independent work
        SimTask("navbar", pre=3),
        SimTask("theme", pre=3),
    ]

