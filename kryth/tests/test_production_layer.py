"""KRYTH production layer — context sharding, reputation routing, adaptive
parallelism, execution profiles, telemetry, readiness report, self-optimization.

All modules are additive and pure/deterministic. None modify scheduler or
execution behavior.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


# ── Phase 1/2 — context sharding ─────────────────────────────────────────────

def _files():
    return [
        "src/components/Navbar.jsx", "src/pages/Landing.jsx", "src/styles/app.css",
        "api/routes/auth.py", "api/services/user.py", "server/main.py",
        "migrations/001_init.sql", "db/schema.sql",
        "tests/test_auth.py", "tests/test_user.py",
        "docs/README.md", ".github/workflows/ci.yml", "package.json",
    ]


def test_shard_partitions_by_domain():
    from agent.production.context_shard import shard_mission
    r = shard_mission(_files(), mission_goal="build the app")
    assert "frontend" in r.shards and "backend" in r.shards
    assert "database" in r.shards and "testing" in r.shards
    # frontend shard has the jsx/css, not the sql
    fe = r.shards["frontend"].files
    assert any("Navbar" in f for f in fe)
    assert not any(f.endswith(".sql") for f in fe)


def test_shard_reduces_context():
    from agent.production.context_shard import shard_mission
    r = shard_mission(_files(), mission_goal="build the app")
    # Each agent sees a slice, not everything → measurable reduction.
    assert r.context_reduction_pct > 0
    assert r.sharded_tokens < r.full_tokens


def test_shard_for_agent_view_is_compressed():
    from agent.production.context_shard import shard_mission
    r = shard_mission(_files(), mission_goal="build the app")
    view = r.for_agent("backend")
    assert view["domain"] == "backend"
    assert view["mission_summary"]               # cascade summary present
    assert "package.json" in view["files"]       # shared file included
    assert all(not f.endswith(".jsx") for f in view["files"])  # no frontend files


def test_shared_files_seen_by_all():
    from agent.production.context_shard import shard_mission
    r = shard_mission(_files())
    assert any("package.json" in f for f in r.shared_files)


# ── Phase 3/4 — reputation routing ───────────────────────────────────────────

def test_reputation_records_and_ranks(tmp_path):
    from agent.production.reputation import ReputationStore
    s = ReputationStore(tmp_path / "rep.json", autoload=False)
    for _ in range(9):
        s.record_tool("write_file", success=True, runtime_ms=10)
    s.record_tool("write_file", success=False, runtime_ms=10)
    for _ in range(2):
        s.record_tool("flaky_tool", success=True)
        s.record_tool("flaky_tool", success=False)
    # write_file (9/10) should outrank flaky_tool (2/4)
    assert s.best_of(["flaky_tool", "write_file"]) == "write_file"
    board = s.leaderboard("tool")
    assert board[0]["name"] == "write_file"
    assert board[0]["confidence"] > board[1]["confidence"]


def test_reputation_wilson_rewards_evidence(tmp_path):
    from agent.production.reputation import ReputationStore
    s = ReputationStore(tmp_path / "r.json", autoload=False)
    # 9/10 vs 95/100 — more evidence should win despite slightly lower raw rate.
    for i in range(10):
        s.record_tool("small", success=(i < 9))
    for i in range(100):
        s.record_tool("big", success=(i < 95))
    assert s.confidence("big") > s.confidence("small")


def test_reputation_persists(tmp_path):
    from agent.production.reputation import ReputationStore
    p = tmp_path / "rep.json"
    s = ReputationStore(p, autoload=False)
    s.record_executor("browser", success=True)
    s.save()
    s2 = ReputationStore(p)
    assert s2.confidence("browser", "executor") > 0


def test_executor_routing(tmp_path):
    from agent.production.reputation import ReputationStore
    s = ReputationStore(tmp_path / "r.json", autoload=False)
    for _ in range(5):
        s.record_executor("terminal", success=True)
    s.record_executor("api", success=False)
    assert s.best_of(["api", "terminal"], kind="executor") == "terminal"


# ── Phase 8 — adaptive parallelism ───────────────────────────────────────────

def test_adaptive_scales_up_under_backlog():
    from agent.production.adaptive import recommend, ParallelismSignal
    d = recommend(ParallelismSignal(workers=4, running=4, queue_depth=8, idle=0, max_workers=16))
    assert d.action == "scale_up"
    assert d.target_workers > 4


def test_adaptive_scales_down_when_idle():
    from agent.production.adaptive import recommend, ParallelismSignal
    d = recommend(ParallelismSignal(workers=8, running=2, queue_depth=0, idle=6, max_workers=16))
    assert d.action == "scale_down"
    assert d.target_workers < 8


def test_adaptive_holds_in_band():
    from agent.production.adaptive import recommend, ParallelismSignal
    d = recommend(ParallelismSignal(workers=4, running=4, queue_depth=0, idle=0))
    assert d.action == "hold"


def test_adaptive_respects_cap():
    from agent.production.adaptive import recommend, ParallelismSignal
    d = recommend(ParallelismSignal(workers=15, running=15, queue_depth=50, idle=0, max_workers=16))
    assert d.target_workers <= 16


def test_adaptive_controller_tracks_peak():
    from agent.production.adaptive import AdaptiveController, ParallelismSignal
    c = AdaptiveController(max_workers=16)
    c.observe(ParallelismSignal(workers=4, running=3, queue_depth=2, idle=1))
    c.observe(ParallelismSignal(workers=6, running=6, queue_depth=0, idle=0))
    rep = c.report()
    assert rep["peak_parallelism"] == 6
    assert rep["peak_efficiency"] == 1.0


# ── Phase 11 — execution profiles ────────────────────────────────────────────

def test_profiles_resolve():
    from agent.production.execution_profiles import get_profile, FAST, BALANCED, MAXIMUM_QUALITY
    assert get_profile("fast") is FAST
    assert get_profile("balanced") is BALANCED
    assert get_profile("max") is MAXIMUM_QUALITY
    assert get_profile("nonsense") is BALANCED   # safe fallback


def test_profile_tradeoffs():
    from agent.production.execution_profiles import FAST, BALANCED, MAXIMUM_QUALITY
    # FAST is cheaper/looser; MAX is stricter; BALANCED in between.
    assert FAST.token_budget_mult < BALANCED.token_budget_mult < MAXIMUM_QUALITY.token_budget_mult
    assert FAST.verification == "minimal" and MAXIMUM_QUALITY.verification == "maximum"
    assert MAXIMUM_QUALITY.code_review and not FAST.code_review


def test_profile_env_selection(monkeypatch):
    from agent.production import execution_profiles as ep
    monkeypatch.setenv("KRYTH_EXEC_PROFILE", "fast")
    assert ep.active_profile().name == "fast"
    monkeypatch.delenv("KRYTH_EXEC_PROFILE", raising=False)
    assert ep.active_profile().name == "balanced"   # default unchanged


def test_balanced_is_default_unchanged():
    from agent.production.execution_profiles import normalize
    assert normalize("BALANCED") == "balanced"
    assert normalize("garbage") is None


# ── Phase 14 — telemetry ─────────────────────────────────────────────────────

def test_telemetry_aggregates_and_exports(tmp_path):
    from agent.production.telemetry import Telemetry
    t = Telemetry()
    t.mission_done(success=True, duration_ms=1200)
    t.tool_call("write_file", success=True, runtime_ms=10)
    t.tool_call("run_command", success=False, runtime_ms=50)
    t.recovery(succeeded=True)
    snap = t.snapshot()
    assert snap["domains"]["mission"]["counters"]["completed"] == 1
    assert snap["domains"]["tool"]["counters"]["calls"] == 2
    dash = t.dashboard()
    assert dash["tool_calls"] == 2
    assert 0 < dash["tool_success_rate"] < 1
    # exports
    jp = tmp_path / "t.json"; mp = tmp_path / "t.md"
    t.export(jp); t.export(mp)
    assert jp.exists() and mp.exists()
    assert "Telemetry" in mp.read_text(encoding="utf-8")


def test_telemetry_markdown_has_domains():
    from agent.production.telemetry import Telemetry
    t = Telemetry()
    t.incr("verification", "syntax_ok", 5)
    md = t.to_markdown()
    assert "Verification" in md and "syntax_ok" in md


# ── Phase 15 — readiness report + Phase 12 self-optimization ─────────────────

def test_readiness_score_high_when_all_green():
    from agent.production.readiness import ReadinessInputs, compute_score
    s = compute_score(ReadinessInputs(
        token_reduction_pct=72, context_reduction_pct=60,
        worker_utilization_pct=90, parallel_efficiency_pct=85,
        recovery_success_rate=0.95, checkpoint_success_rate=0.95, tool_success_rate=0.97,
        contracts_green=True, adversarial_pass=True, suite_pass=True))
    assert s.score >= 85
    assert s.grade.startswith(("A", "B"))


def test_readiness_flags_failing_gates():
    from agent.production.readiness import ReadinessInputs, compute_score
    s = compute_score(ReadinessInputs(contracts_green=False, adversarial_pass=False))
    assert s.score < 70
    assert any("contract" in r.lower() for r in s.risks)


def test_readiness_report_markdown():
    from agent.production.readiness import ReadinessInputs, readiness_report
    md = readiness_report(ReadinessInputs(token_reduction_pct=72, worker_utilization_pct=90))
    assert "Production Readiness Score" in md
    assert "Token reduction" in md


def test_top_improvements_never_auto_merge():
    from agent.production.readiness import ReadinessInputs, top_improvements
    recs = top_improvements(ReadinessInputs(
        worker_utilization_pct=50, token_reduction_pct=40,
        recovery_success_rate=0.7, tool_success_rate=0.8, parallel_efficiency_pct=60), n=5)
    assert 1 <= len(recs) <= 5
    assert all(r.auto_merge is False for r in recs)
    # sorted by gain × confidence ÷ risk → first is the strongest bet
    assert recs[0].expected_gain_pct >= recs[-1].expected_gain_pct * 0.5
