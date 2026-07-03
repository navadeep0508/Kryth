with open('kryth/src/agent/agent_loop.py', 'r') as f:
    src = f.read()

old = (
    "    if _is_first_turn:\n"
    "        ui.llm_waiting(\"--- Scanning project---\")\n"
    "        build_initial_system(session, user_input)\n"
    "        session.ensure_system()\n"
    "\n"
    "        # Wait briefly for preloaded data\n"
)

new = (
    "    if _is_first_turn:\n"
    "        ui.llm_waiting(\"--- Scanning project---\")\n"
    "        build_initial_system(session, user_input)\n"
    "        session.ensure_system()\n"
    "\n"
    "        try:\n"
    "            from agent.runtime.scratchpad import scratch as _scratch\n"
    "            _scratch.initialize(user_input)\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "        # Wait briefly for preloaded data\n"
)

if old in src:
    src = src.replace(old, new, 1)
    with open('kryth/src/agent/agent_loop.py', 'w') as f:
        f.write(src)
    print('Hook 2 applied')
else:
    print('Anchor not found')
    idx = src.find('build_initial_system')
    print(repr(src[idx:idx+200]))
