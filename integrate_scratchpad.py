from pathlib import Path
import py_compile

p = Path('kryth/src/agent/agent_loop.py')
lines = p.read_text(encoding='utf-8').splitlines(keepends=True)

# Line 27 = 'from agent.session import get_session'
# Add scratchpad import before the skills comment
lines.insert(28, '# Runtime scratchpad - execution state tracking\n')
lines.insert(29, 'from agent.runtime.scratchpad import ScratchpadManager, scratchpad_reset\n')

# Line 1702 = '        session.ensure_system()' (now 1704 after +2 insert)
# Hook 2: initialize scratchpad on first turn
hook2 = [
    '\n',
    '        try:\n',
    '            from agent.runtime.scratchpad import scratch as _scratch\n',
    '            _scratch.initialize(user_input)\n',
    '        except Exception:\n',
    '            pass\n',
]
lines[1703:1703] = hook2  # after ensure_system line

# Line 1407 = '_process_tool_calls(session, tool_calls)' (now 1415 after +8)
# Hook 3: update scratchpad after tool calls
hook3 = [
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
lines[1415:1415] = hook3

# Line 1299 = 'from agent.self_eval import evaluate_task as _self_eval' (now 1307)
# Hook 4: inject scratchpad before LLM call
hook4 = [
    '    # Inject scratchpad before LLM call\n',
    '    try:\n',
    '        from agent.runtime.scratchpad import scratch as _scratch\n',
    '        if getattr(_scratch.state, "intent", None) != "CHAT":\n',
    '            _block = _scratch.render_prompt_block()\n',
    '            if _block:\n',
    '                session.messages = [\n',
    '                    m\n',
    '                    for m in session.messages\n',
    '                    if not (m.get("role") == "system" and m.get("content", "").startswith("TASK SCRATCHPAD"))\n',
    '                ]\n',
    '                session.messages.insert(-1, {"role": "system", "content": _block})\n',
    '    except Exception:\n',
    '        pass\n',
    '\n',
]
lines[1307:1307] = hook4

# Line 1255 = '_consecutive_no_tool_turns += 1' (now 1263)
# Hook 5: should_finish early exit
hook5 = [
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
lines[1263:1263] = hook5

p.write_text(''.join(lines), encoding='utf-8')
py_compile.compile(str(p), doraise=True)
print('All hooks applied, compile OK')
