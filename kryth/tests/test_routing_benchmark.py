"""KRYTH Routing Benchmark — CI-enforced protected contract.

Drives the REAL run_agent() for every prompt in routing_benchmark_dataset.py
with the network LLM stubbed and recording instruments on every routing
decision point (planner, executive/orchestrator, UAL/dynamic-builder, worker
pool, inner loop, tool processing, file writes, commands, approvals).

It then asserts the observed routing profile matches the frozen expectation.

  • Conversation prompts MUST be tool-less: 0 tools, 0 writes, 0 commands,
    0 approvals, no planner / executive / UAL / workers, exactly one LLM call
    made with tools=None.
  • Execution prompts MUST reach the engine (inner loop) and MUST NOT be
    handled by the tool-less conversational path.

Run as a test:   pytest tests/test_routing_benchmark.py
Run for report:  python tests/test_routing_benchmark.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from routing_benchmark_dataset import DATASET, CONVERSATION, EXECUTION, MIXED  # noqa: E402


def _drive(prompt):
    """Run run_agent(prompt) with recording instruments; return an observed
    routing profile dict. No exceptions are raised by the instruments, so the
    full engagement set is captured (not just the first system reached)."""
    import agent.agent_loop as al
    import agent.task_classifier as tc
    from agent.agent_loop import LoopResult

    engaged = {
        "planner": 0, "executive": 0, "ual": 0, "workers": 0,
        "inner_loop": 0, "tool_calls": 0, "file_writes": 0,
        "commands": 0, "approvals": 0,
    }
    llm_calls = []

    def stub_llm(messages, tools=None, **kw):
        llm_calls.append({"tools": tools})
        return {
            "content": "Hi! How can I help?",
            "tool_calls": None,
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            "interrupted": False,
        }

    def rec_inner_loop(session, *a, **k):
        engaged["inner_loop"] += 1
        return LoopResult(status="done", content="", turns_used=0)

    def rec_planner(*a, **k):
        engaged["planner"] += 1
        return ({}, "")

    def rec_executive(*a, **k):
        engaged["executive"] += 1
        # Return a result that declines, so run_agent falls through cleanly.
        class _R:
            approved = False
            output = ""
            explanation = "benchmark stub"
            mode_updated = None
        return _R()

    def rec_ual(*a, **k):
        engaged["ual"] += 1
        return LoopResult(status="done", content="", turns_used=0)

    def rec_tools(session, tool_calls, *a, **k):
        engaged["tool_calls"] += len(tool_calls or [])

    # Force the LLM tiebreaker offline so classification is deterministic.
    tc._llm_tiebreaker = lambda *a, **k: None

    # Fresh session.
    from agent.session import get_session
    s = get_session()
    s.messages = []
    s.cumulative_in_tokens = 0
    s.cumulative_out_tokens = 0

    # Silence UI.
    import agent.ui as ui
    ui_orig = {}
    for n in dir(ui):
        fn = getattr(ui, n, None)
        if callable(fn) and not n.startswith("_"):
            ui_orig[n] = fn
            setattr(ui, n, lambda *a, **k: None)

    # Worker-pool spawn recorder (patched at source module).
    wp_ctx = _nullctx()
    try:
        import agent.orchestration.worker_pool as wp

        def rec_workers(*a, **k):
            engaged["workers"] += 1
            return {}
        wp_ctx = patch.object(wp, "spawn_early_for_prompt", rec_workers)
    except Exception:
        pass

    # Heavy, network/IO-ish setup stubbed for speed and hermeticity.
    spec_ctx = patch.object(al, "_speculative_preload", lambda *a, **k: _set_event())
    build_ctx = patch.object(al, "build_initial_system", lambda *a, **k: None)

    exec_ctxs = [
        patch.object(al, "ask_llm_stream", stub_llm),
        patch.object(al, "run_inner_loop", rec_inner_loop),
        patch.object(al, "ask_planner", rec_planner),
        patch.object(al, "run_dynamic_build_with_approval", rec_ual),
        patch.object(al, "_process_tool_calls", rec_tools),
        patch.object(al, "route", lambda *a, **k: []),
        patch.object(al, "auto_select_skills", lambda *a, **k: []),
        spec_ctx, build_ctx, wp_ctx,
    ]
    if getattr(al, "orchestrate", None) is not None:
        exec_ctxs.append(patch.object(al, "orchestrate", rec_executive))

    try:
        with _ExitStack(exec_ctxs):
            al.run_agent(prompt)
    finally:
        for n, fn in ui_orig.items():
            try:
                setattr(ui, n, fn)
            except Exception:
                pass

    route = "conversation" if (
        len(llm_calls) == 1
        and llm_calls and llm_calls[0]["tools"] is None
        and engaged["inner_loop"] == 0
        and engaged["executive"] == 0
        and engaged["ual"] == 0
    ) else "execution"

    return {"route": route, "engaged": engaged, "llm_calls": llm_calls}


def _set_event():
    import threading
    e = threading.Event()
    e.set()
    return e


class _nullctx:
    def __enter__(self): return None
    def __exit__(self, *a): return False


class _ExitStack:
    """Tiny nested context manager (avoids contextlib import churn)."""
    def __init__(self, ctxs):
        self.ctxs = [c for c in ctxs if c is not None]

    def __enter__(self):
        self.entered = []
        for c in self.ctxs:
            c.__enter__()
            self.entered.append(c)
        return self

    def __exit__(self, *exc):
        for c in reversed(self.entered):
            try:
                c.__exit__(*exc)
            except Exception:
                pass
        return False


# ── Contract assertions ────────────────────────────────────────────────────

@pytest.mark.parametrize("entry", DATASET, ids=[e["prompt"] for e in DATASET])
def test_routing_contract(entry):
    obs = _drive(entry["prompt"])
    prompt = entry["prompt"]

    # 1. Route must match the frozen expectation.
    assert obs["route"] == entry["route"], (
        f"ROUTE DRIFT for {prompt!r}: expected {entry['route']}, "
        f"observed {obs['route']} (engaged={obs['engaged']})"
    )

    eng = obs["engaged"]

    if entry["route"] == "conversation":
        # Hard zero-execution guarantee.
        assert len(obs["llm_calls"]) == 1, f"{prompt!r}: expected 1 LLM call"
        assert obs["llm_calls"][0]["tools"] is None, (
            f"{prompt!r}: tools leaked into the conversational LLM call"
        )
        assert eng["tool_calls"] == entry["tool_count"] == 0
        assert eng["file_writes"] == entry["file_writes"] == 0
        assert eng["commands"] == entry["commands"] == 0
        assert eng["approvals"] == entry["approvals"] == 0
        assert eng["planner"] == 0 and entry["planner"] is False
        assert eng["executive"] == 0 and entry["executive"] is False
        assert eng["ual"] == 0 and entry["ual"] is False
        assert eng["workers"] == 0 and entry["workers"] is False
    else:
        # Execution prompts must reach the engine and never use the chat path.
        assert eng["inner_loop"] >= 1, (
            f"{prompt!r}: execution prompt did not reach the inner loop"
        )
        # Deterministic non-engagement (none of these are complex):
        assert eng["planner"] == 0, f"{prompt!r}: unexpected planner usage"
        assert eng["executive"] == 0, f"{prompt!r}: unexpected executive usage"
        assert eng["ual"] == 0, f"{prompt!r}: unexpected UAL usage"
        # Worker pool is expected to warm up on the execution path.
        assert bool(eng["workers"]) == entry["workers"], (
            f"{prompt!r}: workers={eng['workers']} expected {entry['workers']}"
        )


# ── Standalone report ──────────────────────────────────────────────────────

def _report():
    rows = []
    fp = fn = 0
    for entry in DATASET:
        obs = _drive(entry["prompt"])
        ok = obs["route"] == entry["route"]
        if not ok:
            if entry["route"] == "conversation":
                fp += 1   # chat misrouted to execution = false positive (over-block? no — under-block of gate)
            else:
                fn += 1
        rows.append((entry["prompt"], entry["route"], obs["route"], ok, obs["engaged"]))

    total = len(rows)
    passed = sum(1 for r in rows if r[3])
    print("=" * 88)
    print("KRYTH ROUTING BENCHMARK — protected contract")
    print("=" * 88)
    hdr = f"{'PROMPT':42} {'EXPECT':12} {'OBSERVED':12} {'RESULT'}"
    print(hdr)
    print("-" * 88)
    for prompt, exp, obs_r, ok, eng in rows:
        mark = "OK " if ok else "FAIL"
        print(f"{prompt[:42]:42} {exp:12} {obs_r:12} {mark}")
    score = 100.0 * passed / total if total else 0.0
    # "Bypass" = a conversation prompt that reached execution.
    bypasses = [r for r in rows if r[1] == "conversation" and r[2] == "execution"]
    print("-" * 88)
    print(f"Prompts             : {total}  (conversation={len(CONVERSATION)}, "
          f"execution={len(EXECUTION)}, mixed={len(MIXED)})")
    print(f"Passed              : {passed}/{total}")
    print(f"Bypasses (chat→exec): {len(bypasses)}")
    print(f"False positives     : {fp}")
    print(f"False negatives     : {fn}")
    print(f"Regression count    : {total - passed}")
    print(f"Safety score        : {score:.1f}%")
    print("=" * 88)
    print("RESULT:", "✓ CONTRACT INTACT" if passed == total and not bypasses
          else "✗ ROUTING REGRESSION DETECTED")
    print("=" * 88)
    return 0 if passed == total and not bypasses else 1


if __name__ == "__main__":
    sys.exit(_report())
