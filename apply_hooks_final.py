from pathlib import Path
import py_compile

p = Path('kryth/src/agent/agent_loop.py')
lines = p.read_text(encoding='utf-8').splitlines(keepends=True)

# Hook 2: insert after 'session.ensure_system()'
idx2 = next(i for i, l in enumerate(lines) if l.strip() == 'session.ensure_system()')
lines[idx2 + 1:idx2 + 1] = [
    '\n',
    '        try:\n',
    '            from agent.runtime.scratchpad import scratch as _scratch\n',
    '            _scratch.initialize(user_input)\n',
    '        except Exception:\n',
    '            pass\n',
]

# Hook 3: insert after '_process_tool_calls(...)'
idx3 = next(i for i, l in enumerate(lines) if '_process_tool_calls(session, tool_calls)' in l)
lines[idx3 + 1:idx3 + 1] = [
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

# Hook 4: insert before self_eval
idx4 = next(i for i, l in enumerate(lines) if 'from agent.self_eval import evaluate_task as _self_eval' in l)
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
    '                    if not (m.get("role") == "system" and m.get("content", "").startswith("TASK SCRATCHPAD"))\n',
    '                ]\n',
    '                session.messages.insert(-1, {"role": "system", "content": _block})\n',
    '    except Exception:\n',
    '        pass\n',
    '\n',
]

# Hook 5: insert after '_consecutive_no_tool_turns += 1'
idx5 = next(i for i, l in enumerate(lines) if l.strip() == '_consecutive_no_tool_turns += 1')
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
py_compile.compile(str(p), doraise=True)
print('All hooks applied')
