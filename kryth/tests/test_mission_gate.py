"""Pre-execution mission gate — Phase 0 (mode selection) + Phase 15 (approval).

DAG/SWARM-only, non-interactive-safe, user-choice-wins. Off by default
(KRYTH_MISSION_GATE) so existing behavior is unchanged.
"""

import io
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.mission_estimator import estimate
from agent.ui import mission_gate as mg


def _cap(fn, width=200):
    from agent.ui.console import console
    buf = io.StringIO()
    of, ow = console.file, console._width
    try:
        console.file, console._width = buf, width
        fn()
    finally:
        console.file, console._width = of, ow
    return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())


# ── Scope: when to offer (Phase 0) ───────────────────────────────────────────

def test_offers_for_dag_and_swarm():
    assert mg.should_offer_mode("dag") is True
    assert mg.should_offer_mode("swarm") is True


def test_never_offers_for_simple():
    assert mg.should_offer_mode("direct") is False
    assert mg.should_offer_mode("dag", is_conversational=True) is False
    # user already pinned a mode → don't re-ask
    assert mg.should_offer_mode("dag", exec_mode_override="dag") is False
    assert mg.should_offer_mode("swarm", exec_mode_override="direct") is False


# ── Non-interactive safety (must never block) ────────────────────────────────

def test_select_mode_non_interactive_returns_recommendation():
    est = estimate("create a SaaS landing page with hero, features, pricing, FAQ, testimonials")
    # interactive=False must NOT call input(); returns the recommendation.
    assert mg.select_mode(est, interactive=False) == est.recommendation


def test_request_approval_non_interactive_approves():
    est = estimate("build full SaaS platform")
    prev = mg.preview_from_estimate(est)
    assert mg.request_approval(prev, interactive=False) == "approve"


def test_gate_non_interactive_proceeds():
    est = estimate("build a full-stack web app with frontend backend database and tests")
    out = mg.run_pre_execution_gate(est, interactive=False)
    assert out.proceed is True
    # multi-domain build → dag → orchestrate
    assert out.orchestrate is True
    assert out.contract and out.contract.approved
    assert out.preapproved is True            # single source of truth — no 2nd prompt


# ── Interactive choices (injected reader) ────────────────────────────────────

def test_select_mode_user_picks_direct():
    est = estimate("create a SaaS landing page with hero features pricing faq testimonials")
    assert mg.select_mode(est, interactive=True, reader=lambda: "d") == "direct"
    assert mg.select_mode(est, interactive=True, reader=lambda: "s") == "swarm"
    # auto → follows recommendation
    assert mg.select_mode(est, interactive=True, reader=lambda: "a") == est.recommendation


def test_override_user_direct_beats_estimator_dag():
    est = estimate("build a full-stack web app with frontend backend database and tests")
    assert est.recommendation in ("dag", "swarm")
    out = mg.run_pre_execution_gate(est, interactive=True, reader=lambda: "d")
    assert out.mode == "direct" and out.orchestrate is False
    assert out.preapproved is False           # direct → nothing to pre-approve


def test_gate_cancel():
    est = estimate("build a full SaaS platform with auth payments docs tests")
    out = mg.run_pre_execution_gate(est, interactive=True, reader=lambda: "n")
    assert out.proceed is False


def test_gate_approve_creates_contract_and_preapproves():
    est = estimate("build a full SaaS platform with auth payments docs tests")
    out = mg.run_pre_execution_gate(est, interactive=True, reader=lambda: "y")
    assert out.proceed and out.orchestrate and out.contract.approved
    # The ONE decision — orchestration must not ask again.
    assert out.preapproved is True


def test_gate_swarm_override_preapproves():
    est = estimate("build a full-stack web app with frontend backend database")
    out = mg.run_pre_execution_gate(est, interactive=True, reader=lambda: "s")
    assert out.mode == "swarm" and out.orchestrate and out.preapproved


def test_gate_set_default_preference():
    est = estimate("build a full SaaS platform with auth payments docs tests")
    out = mg.run_pre_execution_gate(est, interactive=True, reader=lambda: "p")
    assert out.proceed and out.orchestrate and out.set_default is True
    assert out.preapproved is True


def test_gate_modify_falls_back_to_direct():
    est = estimate("build a full-stack web app with frontend backend database")
    out = mg.run_pre_execution_gate(est, interactive=True, reader=lambda: "m")
    assert out.proceed and out.orchestrate is False


# ── EOF / interrupt safety ───────────────────────────────────────────────────

def test_eof_during_select_returns_recommendation():
    est = estimate("build full SaaS platform")
    def _boom():
        raise EOFError
    assert mg.select_mode(est, interactive=True, reader=_boom) == est.recommendation


# ── Mission preview derivation ───────────────────────────────────────────────

def test_preview_from_estimate():
    est = estimate("create a SaaS landing page with hero, features, pricing, FAQ, testimonials")
    p = mg.preview_from_estimate(est)
    assert p.files_create >= 1
    assert "Frontend Team" in p.plan
    # sections show up as frontend tasks
    assert any("Hero" in t or "Pricing" in t for t in p.plan["Frontend Team"])


# ── Mission contract + critical actions (Level 3) ────────────────────────────

def test_contract_covers_routine_not_critical():
    c = mg.MissionContract(approved=True, mode="dag")
    assert c.covers("write", "src/hero.tsx") is True
    assert c.covers("command", "npm install") is True
    # critical actions always excluded
    assert c.covers("command", "kubectl apply -f prod.yaml") is False
    assert c.covers("command", "drop database users") is False


def test_unapproved_contract_covers_nothing():
    c = mg.MissionContract(approved=False)
    assert c.covers("write", "a.txt") is False


@pytest.mark.parametrize("action", [
    "deploy to production",
    "drop database analytics",
    "rm -rf /data",
    "rotate the api key",
    "terraform apply",
    "git force-push origin main",
])
def test_is_critical_detects(action):
    assert mg.is_critical(action) is True


@pytest.mark.parametrize("action", [
    "write src/hero.tsx", "create file pricing.tsx", "npm install", "run pytest",
])
def test_is_critical_allows_routine(action):
    assert mg.is_critical(action) is False


# ── Rendering ────────────────────────────────────────────────────────────────

def test_mode_panel_renders():
    est = estimate("create a SaaS landing page with hero, features, pricing, FAQ, testimonials")
    out = _cap(lambda: mg.mode_recommendation_panel(est))
    assert "EXECUTION MODE RECOMMENDATION" in out
    assert "Recommended" in out and "speedup" in out.lower()
    assert "[A] Auto" in out


def test_preview_panel_renders():
    est = estimate("build full SaaS platform")
    out = _cap(lambda: mg.mission_preview_panel(mg.preview_from_estimate(est)))
    assert "MISSION EXECUTION PREVIEW" in out
    assert "Execution plan" in out
    assert "[Y] Approve" in out


# ── Default-off + gate disabled ──────────────────────────────────────────────

def test_gate_enabled_by_default(monkeypatch):
    # The prompt is ON by default; non-interactive runs auto-proceed (never block).
    monkeypatch.delenv("KRYTH_MISSION_GATE", raising=False)
    assert mg.gate_enabled() is True
    monkeypatch.setenv("KRYTH_MISSION_GATE", "0")
    assert mg.gate_enabled() is False
    monkeypatch.setenv("KRYTH_MISSION_GATE", "1")
    assert mg.gate_enabled() is True
