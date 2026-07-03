with open('kryth/src/agent/agent_loop.py', 'rb') as f:
    lines = f.read().split(b'\n')

# --- Hook 3: update scratchpad after tool execution ---
# Insert after line 1410 "_total_executing_s += ..."
for i, line in enumerate(lines):
    if b'_total_executing_s += _time.monotonic() - _exec_start' in line:
        insert3 = [
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
        ]
        lines = lines[:i+1] + insert3 + lines[i+1:]
        break

# --- Hook 4: inject scratchpad before LLM call ---
# Insert before line with "from agent.self_eval import evaluate_task"
for i, line in enumerate(lines):
    if b'from agent.self_eval import evaluate_task as _self_eval' in line:
        insert4 = [
            b'',
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
        lines = lines[:i] + insert4 + lines[i:]
        break

# --- Hook 5: should_finish() early exit ---
# Insert after "_consecutive_no_tool_turns += 1"
for i, line in enumerate(lines):
    if b'_consecutive_no_tool_turns += 1' in line and b'scratch' not in line:
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
        lines = lines[:i+1] + insert5 + lines[i+1:]
        break

with open('kryth/src/agent/agent_loop.py', 'wb') as f:
    f.write(b'\n'.join(lines))

import py_compile
py_compile.compile('kryth/src/agent/agent_loop.py', doraise=True)
print('Hooks 3+4+5 applied, compile OK')
