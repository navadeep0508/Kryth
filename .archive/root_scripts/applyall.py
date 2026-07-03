with open('kryth/src/agent/agent_loop.py', 'rb') as f:
    data = f.read()
lines = data.split(b'\n')

# helper: find line index containing bytes
def find_idx(needle):
    for i, line in enumerate(lines):
        if needle in line:
            return i
    raise SystemExit(f'not found: {needle}')

# hook 2 already applied via edit, skip build_initial_system anchor check

# hook 3 - insert after _process_tool_calls line
i3 = find_idx(b'_process_tool_calls(session, tool_calls)')
lines = lines[:i3+1] + [
    b'',
    b'    # Update scratchpad with tool results',
    b'    try:',
    b'        from agent.runtime.scratchpad import scratch as _scratch',
    b'        for msg in session.messages:',
    b'            if msg.get("role") == "tool":',
    b'                _scratch.update_after_tool(',
    b'                    msg.get("name", ""),',
    b'                    msg.get("content", "")[:500],',
    b'                )',
    b'    except Exception:',
    b'        pass',
] + lines[i3+1:]

# hook 4/5 - use offset-based insertion into known region
# After backup done += 1 (line 1270 in current)
i_cnv = find_idx(b'_consecutive_no_tool_turns += 1')
insert5 = [
    b'',
    b'    # Scratchpad completion intelligence',
    b'    try:',
    b'        from agent.runtime.scratchpad import scratch as _scratch',
    b'        if _scratch.should_finish():',
    b'            return LoopResult(',
    b'                status="done",',
    b'                content=content or "",',
    b'                turns_used=turn_count,',
    b'                finish_reason="completed",',
    b'            )',
    b'    except Exception:',
    b'        pass',
]
lines = lines[:i_cnv+1] + insert5 + lines[i_cnv+1:]

# hook 4 - inject before self_eval line
i_self = find_idx(b'from agent.self_eval import evaluate_task as _self_eval')
insert4 = [
    b'    # Inject scratchpad before LLM call',
    b'    try:',
    b'        from agent.runtime.scratchpad import scratch as _scratch',
    b'        if getattr(_scratch.state, "intent", None) != "CHAT":',
    b'            _block = _scratch.render_prompt_block()',
    b'            if _block:',
    b'                session.messages = [',
    b'                    m',
    b'                    for m in session.messages',
    b'                    if not (m.get("role") == "system"',
    b'                            and m.get("content", "").startswith("TASK SCRATCHPAD"))',
    b'                ]',
    b'                session.messages.insert(-1, {"role": "system", "content": _block})',
    b'    except Exception:',
    b'        pass',
    b'',
]
lines = lines[:i_self] + insert4 + lines[i_self:]

with open('kryth/src/agent/agent_loop.py', 'wb') as f:
    f.write(b'\n'.join(lines))

import py_compile
py_compile.compile('kryth/src/agent/agent_loop.py', doraise=True)
print('All hooks applied')
