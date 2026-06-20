"""Tool Curation Layer — token-efficiency contract.

Curation only SHRINKS the offered tool set; behavior is preserved by an
always-core set + keyword-triggered domains + auto-expand escalation. These
tests lock in: the reduction target, always-core inclusion, domain triggers,
the safety guarantees (never empty, unknown tools kept), and the flag.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.tools import TOOL_SPECS
from agent import tool_curator as tc


def _msgs(text):
    return [{"role": "user", "content": text}]


def _names(specs):
    return {(s.get("function", {}) or {}).get("name", "") for s in specs}


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.delenv("KRYTH_TOOL_CURATION", raising=False)  # default ON
    yield


# ── Reduction target ─────────────────────────────────────────────────────────

def test_file_task_meets_70pct_reduction():
    s = tc.stats(_msgs("create hello.txt containing hello"), TOOL_SPECS)
    assert s["reduction_pct"] >= 70.0, s
    assert s["tools_sent"] < s["tools_available"]


@pytest.mark.parametrize("prompt", [
    "create hello.txt", "read main.py", "edit config.py",
    "write a factorial function", "run python --version", "build CRUD API",
])
def test_common_tasks_are_curated_down(prompt):
    curated = tc.curate(_msgs(prompt), TOOL_SPECS)
    assert len(curated) < len(TOOL_SPECS)


# ── Always-core ──────────────────────────────────────────────────────────────

def test_core_tools_always_present():
    for prompt in ("hi", "create x.txt", "scrape a website", "commit to git"):
        names = _names(tc.curate(_msgs(prompt), TOOL_SPECS))
        for core in ("read_file", "write_file", "edit_file", "run_command",
                     "grep", "todo_write", "verify_files"):
            assert core in names, f"{core} missing for {prompt!r}"


# ── Domain triggers ──────────────────────────────────────────────────────────

def test_browser_domain_only_on_web_tasks():
    file_names = _names(tc.curate(_msgs("create hello.txt"), TOOL_SPECS))
    assert not any(n.startswith("browser_") for n in file_names)
    web_names = _names(tc.curate(_msgs("scrape jobs from a website and save them"), TOOL_SPECS))
    assert any(n.startswith("browser_") for n in web_names)
    assert "open_url" in web_names


def test_git_domain_only_on_git_tasks():
    assert "git_op" not in _names(tc.curate(_msgs("create a.txt"), TOOL_SPECS))
    assert "git_op" in _names(tc.curate(_msgs("commit my changes to git"), TOOL_SPECS))


def test_mission_domain_only_on_mission_tasks():
    assert not any(n.startswith("mission_") for n in _names(tc.curate(_msgs("edit main.py"), TOOL_SPECS)))
    assert any(n.startswith("mission_") for n in _names(tc.curate(_msgs("queue a long-running mission and pause it"), TOOL_SPECS)))


def test_factory_and_supervisor_excluded_by_default():
    names = _names(tc.curate(_msgs("create hello.txt"), TOOL_SPECS))
    assert not any(n.startswith("factory_") for n in names)
    assert not any(n.startswith("supervisor_") for n in names)


# ── Safety ───────────────────────────────────────────────────────────────────

def test_never_returns_empty():
    assert tc.curate(_msgs(""), TOOL_SPECS)  # empty prompt → at least core
    assert tc.curate(_msgs("xyz"), [])  == []  # empty specs → empty (no crash)


def test_unknown_tool_is_kept():
    # A tool whose name matches no domain must be kept (never silently dropped).
    fake = [{"type": "function", "function": {"name": "some_brand_new_tool", "parameters": {}}}]
    out = tc.curate(_msgs("create x.txt"), fake)
    assert _names(out) == {"some_brand_new_tool"}


def test_force_full_returns_everything():
    assert len(tc.curate(_msgs("create x.txt"), TOOL_SPECS, force_full=True)) == len(TOOL_SPECS)


def test_flag_disabled_returns_full(monkeypatch):
    monkeypatch.setenv("KRYTH_TOOL_CURATION", "0")
    assert len(tc.curate(_msgs("create x.txt"), TOOL_SPECS)) == len(TOOL_SPECS)


def test_curation_subset_is_real_subset():
    curated = tc.curate(_msgs("create hello.txt"), TOOL_SPECS)
    assert _names(curated).issubset(_names(TOOL_SPECS))


# ── Behavior preservation: curated tools are unchanged specs ─────────────────

def test_curated_specs_are_identical_objects():
    curated = tc.curate(_msgs("create hello.txt"), TOOL_SPECS)
    by_name = {(s["function"]["name"]): s for s in TOOL_SPECS}
    for s in curated:
        # same object reference → schema is byte-identical, never rewritten
        assert s is by_name[s["function"]["name"]]


# ── Escalation latch ─────────────────────────────────────────────────────────

def test_escalation_counter():
    before = tc.curation_misses()
    tc._curation_miss()
    assert tc.curation_misses() == before + 1
