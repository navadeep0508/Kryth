from pathlib import Path

p = Path('kryth/src/agent/agent_loop.py')
lines = p.read_text(encoding='utf-8').splitlines(keepends=True)

# Hook 1: import (already done via edit, but ensure it's present)
assert any('from agent.runtime.scratchpad import ScratchpadManager, scratchpad_reset' in l for l in lines), 'import missing'

# Hook 2: initialize scratchpad after session.ensure_system() in run_agent
anchor2 = '        session.ensure_system()\n'
idx2 = next(i for i, l in enumerate(lines) if l == anchor2 and i > 1500)
lines[idx2 + 1:idx2 + 1] = [
    '\n',
    '        # Initialize scratchpad\n',
    '        try:\n',
    '            from agent.runtime.scratchpad import scratch as _scratch\n',
    '            _scratch.initialize(user_input)\n',
    '        except Exception:\n',
    '            pass\n',
]

# Hook 3: update scratchpad after tool execution
anchor3 = '    _exec_start = _time.monotonic()\n'
idx3 = next(i for i, l in enumerate(lines) if l == anchor3)
lines[idx3 + 3:idx3 + 3] = [
    '\n',
    '    # Update scratchpad with tool results\n',
    '    try:\n',
    '        from agent.runtime.scratchpad import scratch as _scratch\n',
    '        for msg in session.messages:\n',
    '            if msg.get("role") == "tool":\n',
    '                _scratch.update_after_tool(\n',
    '                    msg.get("name", ""),\n',
    '                    msg.get("content", "")[:500],\n',
    '                )\n',
    '    except Exception:\n',
    '        pass\n',
]

# Hook 4: inject scratchpad before LLM call
anchor4 = '        from agent.self_eval import evaluate_task as _self_eval\n'
idx4 = next(i for i, l in enumerate(lines) if l == anchor4)
lines[idx4:idx4] = [
    '    # Inject scratchpad before LLM call\n',
    '    try:\n',
    '        from agent.runtime.scratchpad import scratch as _scratch\n',
    '        if getattr(_scratch.state, "intent", None) != "CHAT":\n',
    '            _block = _scratch.render_prompt_block()\n',
    '            if _block:\n',
    '                session.messages = [\n',
    '                    m\n',
    '                    for m in session.messages\n',
    '                    if not (m.get("role") == "system"\n',
    '                            and m.get("content", "").startswith("TASK SCRATCHPAD"))\n',
    '                ]\n',
    '                session.messages.insert(-1, {"role": "system", "content": _block})\n',
    '    except Exception:\n',
    '        pass\n',
    '\n',
]

# Hook 5: should_finish() check
anchor5 = '    _consecutive_no_tool_turns += 1\n'
idx5 = next(i for i, l in enumerate(lines) if l == anchor5)
lines[idx5 + 1:idx5 + 1] = [
    '\n',
    '    # Scratchpad completion intelligence\n',
    '    try:\n',
    '        from agent.runtime.scratchpad import scratch as _scratch\n',
    '        if _scratch.should_finish():\n',
    '            return LoopResult(\n',
    '                status="done",\n',
    '                content=content or "",\n',
    '                turns_used=turn_count,\n',
    '                finish_reason="completed",\n',
    '            )\n',
    '    except Exception:\n',
    '        pass\n',
]

p.write_text(''.join(lines), encoding='utf-8')

import py_compile
py_compile.compile(str(p), doraise=True)
print('All hooks applied, compile OK')
