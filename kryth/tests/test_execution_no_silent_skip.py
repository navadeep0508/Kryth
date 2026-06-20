"""Execution must never be silently skipped — protected regression contract.

Historical bug: a SIMPLE task whose model reply was text-only (no tool call)
hit a shortcut that returned ``done`` immediately — showing "Mission Complete"
while creating nothing. Example: ``create txt file write helo sunny`` produced
no file, no approval, no tool call.

Root cause: run_inner_loop accepted a text-only first reply as the final
answer for simple tasks. That shortcut was meant for conversational simple
tasks, but those are now handled by the conversation hard gate — so any task
reaching the inner loop with explicit execution intent MUST dispatch a tool.

Contract:
  • has_execution_intent() is True for every create/edit/rename/delete/run
    style prompt (and False for pure chat).
  • Driving run_agent() for such a prompt, with a model that stalls with text
    before calling the tool, MUST nudge and dispatch the tool — never return
    "done" with zero tool calls.
"""

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.task_classifier import has_execution_intent, classify_task  # noqa: E402


EXECUTION_PROMPTS = [
    "create txt file write helo sunny",
    "create hello.txt containing hello",
    "edit main.py",
    "rename hello.txt to notes.txt",
    "delete notes.txt",
    "write a hello world script",
    "build jwt authentication",
    "fix this function",
]

CHAT_PROMPTS = [
    "hi", "hello", "thanks", "ok", "what is python", "who are you",
    "hello what is python",
]


@pytest.mark.parametrize("text", EXECUTION_PROMPTS)
def test_execution_intent_detected(text):
    assert has_execution_intent(text) is True
    assert classify_task(text).is_conversational is False


@pytest.mark.parametrize("text", CHAT_PROMPTS)
def test_chat_has_no_execution_intent(text):
    assert has_execution_intent(text) is False


def _drive_stalling_model(prompt, max_tool_turns=50):
    """Drive run_agent with a model that replies text-only first (stall), then
    dispatches write_file once nudged, then finishes. Returns the list of tool
    names dispatched and the final status."""
    import agent.agent_loop as al
    import agent.task_classifier as tc

    tc._llm_tiebreaker = lambda *a, **k: None  # offline determinism

    state = {"n": 0}

    def stub_llm(messages, tools=None, **kw):
        state["n"] += 1
        if state["n"] == 1:
            # The stall: text only, no tool call.
            return {"content": "Sure, I'll do that.", "tool_calls": None,
                    "finish_reason": "stop",
                    "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                    "interrupted": False}
        if state["n"] == 2:
            return {"content": "", "tool_calls": [{
                        "id": "t1", "type": "function",
                        "function": {"name": "write_file",
                                     "arguments": '{"path":"notes.txt","content":"helo sunny"}'}}],
                    "finish_reason": "tool_calls",
                    "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                    "interrupted": False}
        return {"content": "Done.", "tool_calls": None, "finish_reason": "stop",
                "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                "interrupted": False}

    dispatched = []

    def rec_tools(session, tcs, *a, **k):
        for t in (tcs or []):
            dispatched.append(t["function"]["name"])

    # Patch the network + heavy setup.
    orig = {}
    def setattr_save(mod, name, val):
        orig[(mod, name)] = getattr(mod, name)
        setattr(mod, name, val)

    setattr_save(al, "ask_llm_stream", stub_llm)
    setattr_save(al, "_process_tool_calls", rec_tools)
    setattr_save(al, "build_initial_system", lambda *a, **k: None)
    setattr_save(al, "_speculative_preload", lambda *a, **k: _set_event())
    setattr_save(al, "MAX_TOOL_TURNS", max_tool_turns)

    import agent.ui as ui
    ui_orig = {}
    for n in dir(ui):
        f = getattr(ui, n, None)
        if callable(f) and not n.startswith("_"):
            ui_orig[n] = f
            setattr(ui, n, lambda *a, **k: None)

    wp_orig = None
    try:
        import agent.orchestration.worker_pool as wp
        wp_orig = (wp, "spawn_early_for_prompt", wp.spawn_early_for_prompt)
        wp.spawn_early_for_prompt = lambda *a, **k: {}
    except Exception:
        pass

    from agent.session import get_session
    s = get_session()
    s.messages = []
    s.cumulative_in_tokens = 0
    s.cumulative_out_tokens = 0

    try:
        res = al.run_agent(prompt)
    finally:
        for (mod, name), val in orig.items():
            setattr(mod, name, val)
        for n, f in ui_orig.items():
            setattr(ui, n, f)
        if wp_orig:
            setattr(wp_orig[0], wp_orig[1], wp_orig[2])

    return dispatched, res


def _set_event():
    e = threading.Event()
    e.set()
    return e


@pytest.mark.parametrize("prompt", [
    "create txt file write helo sunny",
    "create hello.txt containing hello",
])
def test_execution_prompt_is_not_silently_skipped(prompt):
    dispatched, res = _drive_stalling_model(prompt)
    assert "write_file" in dispatched, (
        f"SILENT SKIP REGRESSION: {prompt!r} returned status={res.status} "
        f"with no tool dispatch ({dispatched})"
    )


def test_pure_chat_still_short_circuits_without_tools():
    # Conversational input must NOT be forced into tool dispatch — it is
    # handled by the gate (tool-less). Driving "hi" dispatches no tools.
    dispatched, res = _drive_stalling_model("hi")
    assert dispatched == [], f"chat dispatched tools unexpectedly: {dispatched}"
    assert res.status == "done"


# ── Execution budget: simple tasks STOP after the requested work ───────────
#
# Regression: after a simple file op (write/delete/rename) succeeded, the loop
# nudged the model into npm install / test generation / build pipelines —
# autonomous continuation the user never asked for. A simple task must perform
# exactly the requested operation(s), then mark complete and STOP.

def _drive_completing_model(prompt, tool_name="write_file",
                            tool_args='{"path":"hello.txt","content":"helo sunny"}',
                            max_tool_turns=50):
    """Drive run_agent with a model that dispatches ONE tool, then signals
    completion with text on every subsequent turn. Returns (dispatched, res,
    nudged) where `nudged` is True iff the loop injected an npm/test/build
    continuation directive."""
    import agent.agent_loop as al
    import agent.task_classifier as tc

    tc._llm_tiebreaker = lambda *a, **k: None
    state = {"n": 0}

    def stub_llm(messages, tools=None, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"content": "", "tool_calls": [{
                        "id": "t1", "type": "function",
                        "function": {"name": tool_name, "arguments": tool_args}}],
                    "finish_reason": "tool_calls",
                    "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                    "interrupted": False}
        return {"content": "Done. Task complete.", "tool_calls": None,
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                "interrupted": False}

    dispatched = []

    def rec_tools(session, tcs, *a, **k):
        for t in (tcs or []):
            dispatched.append(t["function"]["name"])
            session.append({"role": "tool", "name": t["function"]["name"],
                            "content": "ok", "tool_call_id": t.get("id", "x")})

    orig = {}
    def save(mod, name, val):
        orig[(mod, name)] = getattr(mod, name)
        setattr(mod, name, val)

    save(al, "ask_llm_stream", stub_llm)
    save(al, "_process_tool_calls", rec_tools)
    save(al, "build_initial_system", lambda *a, **k: None)
    save(al, "_speculative_preload", lambda *a, **k: _set_event())
    save(al, "MAX_TOOL_TURNS", max_tool_turns)

    import agent.ui as ui
    ui_orig = {}
    for n in dir(ui):
        f = getattr(ui, n, None)
        if callable(f) and not n.startswith("_"):
            ui_orig[n] = f
            setattr(ui, n, lambda *a, **k: None)
    wp_orig = None
    try:
        import agent.orchestration.worker_pool as wp
        wp_orig = (wp, "spawn_early_for_prompt", wp.spawn_early_for_prompt)
        wp.spawn_early_for_prompt = lambda *a, **k: {}
    except Exception:
        pass

    from agent.session import get_session
    s = get_session()
    s.messages = []
    s.cumulative_in_tokens = 0
    s.cumulative_out_tokens = 0
    try:
        res = al.run_agent(prompt)
    finally:
        for (mod, name), val in orig.items():
            setattr(mod, name, val)
        for n, f in ui_orig.items():
            setattr(ui, n, f)
        if wp_orig:
            setattr(wp_orig[0], wp_orig[1], wp_orig[2])

    nudged = any(
        ("npm install" in (m.get("content") or ""))
        or ("test files now" in (m.get("content") or ""))
        or ("Continue --- dispatch" in (m.get("content") or ""))
        or ("No files written yet" in (m.get("content") or ""))
        for m in s.messages if m.get("role") == "user"
    )
    return dispatched, res, nudged


@pytest.mark.parametrize("prompt,tool,args", [
    ("create a txt file write helo sunny", "write_file", '{"path":"hello.txt","content":"helo sunny"}'),
    ("create hello.txt containing hello", "write_file", '{"path":"hello.txt","content":"hello"}'),
    ("delete hello.txt", "delete_file", '{"path":"hello.txt"}'),
    ("rename hello.txt to notes.txt", "write_file", '{"path":"notes.txt","content":"x"}'),
])
def test_simple_task_stops_after_one_operation(prompt, tool, args):
    dispatched, res, nudged = _drive_completing_model(prompt, tool_name=tool, tool_args=args)
    # Exactly the requested operation, nothing more.
    assert dispatched == [tool], (
        f"BUDGET BREACH: {prompt!r} dispatched {dispatched} (expected exactly [{tool!r}])"
    )
    assert "run_command" not in dispatched, f"{prompt!r}: ran a command — forbidden for simple ops"
    # No autonomous continuation directive was injected.
    assert not nudged, f"BUDGET BREACH: {prompt!r} was nudged into npm/tests/build continuation"
    assert res.status == "done"


def test_medium_task_still_continues_to_build_and_test():
    # Guard against over-restricting: medium/complex builds must still be
    # nudged to install/verify after writing files.
    import agent.task_classifier as tc
    tc._llm_tiebreaker = lambda *a, **k: None
    prompt = "build jwt authentication system with login and signup"
    assert tc.classify_task(prompt).complexity in ("medium", "complex")
    _, _, nudged = _drive_completing_model(prompt, tool_name="write_file",
                                           tool_args='{"path":"auth.js","content":"x"}')
    assert nudged, "medium build task should still be nudged to install/verify"
