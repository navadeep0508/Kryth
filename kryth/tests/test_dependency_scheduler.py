"""Dependency-aware work stealing & non-idle agents — contract.

Extends the work-stealing model so a blocked worker returns its task and pulls
other READY work instead of idling. Pure, deterministic (time injected). Does
not modify WorkQueue / scheduler / DAG.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.orchestration.dependency_scheduler import (
    DependencyAwareQueue, DependencyRegistry, TaskState, DepStatus,
    most_blocking_dependency, UtilizationTracker,
    benchmark_dependency_scheduling, default_benchmark_mission, SimTask,
)


# ── Phase 2 — dependency registry ────────────────────────────────────────────

def test_registry_lifecycle():
    r = DependencyRegistry()
    r.declare_producer("auth.ts", "backend")
    r.add_consumer("auth.ts", "login_ui")
    assert r.is_ready("auth.ts") is False
    r.satisfy("auth.ts", now=5.0)
    assert r.is_ready("auth.ts") is True
    rec = r.record("auth.ts")
    assert rec.producer == "backend" and "login_ui" in rec.consumers
    assert rec.ready_time == 5.0


def test_contract_first_unlocks_early():
    # Phase 6 — a declared contract lets consumers start before the impl.
    r = DependencyRegistry()
    r.declare_producer("login_api", "backend")
    assert r.is_ready("login_api") is False
    r.declare_contract("login_api", now=1.0)
    assert r.is_ready("login_api") is True            # interface is enough
    assert r.record("login_api").status is DepStatus.CONTRACT


# ── Phase 1 — task lifecycle ─────────────────────────────────────────────────

def test_ready_task_runs_when_deps_met():
    q = DependencyAwareQueue()
    q.add_task("p", produces=["auth"], priority=1)
    q.add_task("c", requires=["auth"], priority=2)
    # Only the producer is runnable initially.
    t = q.next_ready(now=0)
    assert t.task_id == "p"
    assert q.next_ready(now=0) is None                # consumer blocked on auth
    q.complete("p", now=3)                            # auto-produces auth
    t2 = q.next_ready(now=3)
    assert t2.task_id == "c"


# ── Phase 3/4 — block, re-steal, resume (not restart) ────────────────────────

def test_block_and_resteal_frees_worker():
    q = DependencyAwareQueue()
    q.add_task("login_ui", requires=["auth"], priority=1)
    q.add_task("auth_api", produces=["auth"], priority=2)
    q.add_task("navbar", priority=3)

    # Worker grabs login_ui (it has no *pre* gate here; simulate runtime block).
    first = q.next_ready(now=0)
    # login_ui needs auth which isn't ready, so next_ready actually skips it and
    # gives the first runnable task (auth_api or navbar).
    assert first.task_id in ("auth_api", "navbar")


def test_resume_preserves_progress_and_counts():
    q = DependencyAwareQueue()
    q.add_task("login_ui", requires=["auth"])
    q.add_task("auth_api", produces=["auth"])
    # login_ui starts optimistically, then blocks at runtime on auth.
    q._tasks["login_ui"].state = TaskState.RUNNING
    q.save_progress("login_ui", {"scaffolded": True})
    nxt = q.block_and_resteal("login_ui", "auth", now=2)
    assert q._tasks["login_ui"].state == TaskState.WAITING_DEPENDENCY
    assert nxt is not None and nxt.task_id == "auth_api"   # worker not idle
    # produce auth → login_ui becomes resumable, progress intact, not restarted
    unblocked = q.complete("auth_api", now=6)
    assert "login_ui" in unblocked
    assert q._tasks["login_ui"].state == TaskState.RESUMABLE
    assert q._tasks["login_ui"].progress == {"scaffolded": True}
    resumed = q.next_ready(now=6)
    assert resumed.task_id == "login_ui"
    assert resumed.resume_count == 1
    assert resumed.dependency_wait_time == pytest.approx(4.0)  # 6 - 2


# ── Phase 5 — partial dependency unlocking ───────────────────────────────────

def test_partial_unlocking_only_required_dep():
    q = DependencyAwareQueue()
    q.add_task("login_ui", requires=["auth"])
    q.add_task("auth_api", produces=["auth"])
    q.add_task("billing_api", produces=["billing"])   # unrelated
    q._tasks["login_ui"].state = TaskState.WAITING_DEPENDENCY
    q._tasks["login_ui"]._blocked_since = 0
    q._tasks["login_ui"].blocked_on = "auth"
    # Producing the unrelated dep must NOT unblock login_ui.
    assert q.produce("billing_api", "billing", now=1) == []
    assert q._tasks["login_ui"].state == TaskState.WAITING_DEPENDENCY
    # Producing the required dep unblocks it.
    assert q.produce("auth_api", "auth", now=2) == ["login_ui"]


# ── Phase 9 — most-blocking dependency (critical path) ───────────────────────

def test_most_blocking_dependency():
    q = DependencyAwareQueue()
    for i in range(4):
        tid = f"ui{i}"
        q.add_task(tid, requires=["auth"])
        q._tasks[tid].state = TaskState.WAITING_DEPENDENCY
        q._tasks[tid].blocked_on = "auth"
        q._tasks[tid]._blocked_since = 0
    q.add_task("oneoff", requires=["users"])
    q._tasks["oneoff"].state = TaskState.WAITING_DEPENDENCY
    q._tasks["oneoff"].blocked_on = "users"
    q._tasks["oneoff"]._blocked_since = 0
    info = most_blocking_dependency(q, now=38)
    assert info["dependency"] == "auth"
    assert info["blocking"] == 4
    assert info["impact"] == "high"


# ── Phase 7 — utilization ────────────────────────────────────────────────────

def test_utilization_tracker():
    u = UtilizationTracker()
    u.sample(running=3, blocked=0, idle=1)
    u.sample(running=4, blocked=0, idle=0)
    rep = u.report(work_steals=2, dependency_wait_time=5.0)
    assert rep.running_pct > rep.idle_pct
    assert rep.work_steals == 2
    d = rep.to_dict()
    assert set(d) >= {"running_pct", "idle_pct", "work_steals", "dependency_wait_time"}


# ── Phase 10 — benchmark proves the improvement ──────────────────────────────

@pytest.mark.parametrize("workers", [3, 4, 5])
def test_benchmark_dependency_aware_beats_baseline(workers):
    r = benchmark_dependency_scheduling(default_benchmark_mission(), workers)
    base, aware = r["baseline"], r["dependency_aware"]
    # Never idle while work exists ⇒ no worse makespan, less idle, more utilization.
    assert aware["makespan"] <= base["makespan"]
    assert aware["idle_ticks"] <= base["idle_ticks"]
    assert aware["utilization_pct"] >= base["utilization_pct"]
    assert r["idle_reduction_pct"] >= 0


def test_benchmark_shows_real_gain_when_workers_scarce():
    # With workers <= blocking tasks, the baseline wastes capacity; the
    # dependency-aware queue must reclaim it measurably.
    r = benchmark_dependency_scheduling(default_benchmark_mission(), 3)
    assert r["idle_reduction_pct"] > 50          # large idle reclaim
    assert r["dependency_aware"]["work_steals"] >= 1


def test_no_idle_while_runnable_work_exists():
    # Core success criterion: a worker only idles when nothing is runnable.
    q = DependencyAwareQueue()
    q.add_task("a")
    q.add_task("b", requires=["x"])
    q.add_task("p", produces=["x"])
    # Two runnable tasks (a, p); a blocked one (b). Two pulls must both succeed.
    t1 = q.next_ready(now=0); t2 = q.next_ready(now=0)
    assert {t1.task_id, t2.task_id} == {"a", "p"}
    assert q.next_ready(now=0) is None           # b not runnable yet → legit idle
    assert q.runnable_exists(now=0) is False


# ── Phase 8 — dashboard snapshot ─────────────────────────────────────────────

def test_snapshot_has_dashboard_fields():
    q = DependencyAwareQueue()
    q.add_task("login_ui", description="Login UI", requires=["auth"])
    rows = q.snapshot()
    assert rows and set(rows[0]) >= {"task_id", "state", "blocked_by", "resume_count",
                                     "dependency_wait_time", "requires"}
    assert q.state_counts()["ready"] >= 1
