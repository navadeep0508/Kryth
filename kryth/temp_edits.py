#!/usr/bin/env python3
"""Apply the required edits to agent_loop.py"""

with open('src/agent/agent_loop.py', 'r') as f:
    lines = f.readlines()

# --- Edit 1: Add imports after line 29 (index 28) ---
# We already have: from agent.skills import auto_select_skills, compose_skills
# We already have: from agent.dynamic_builder import run_dynamic_build_with_approval
# We already have: from agent.ecosystem.router import route
# We already have: from agent.task_classifier import classify_task
# So we need to add: from agent.task_analyzer import TaskAnalyzer
# And: from agent.execution_strategy import decide_execution_strategy

# Find the line with "from agent.task_classifier import classify_task"
for i, line in enumerate(lines):
    if 'from agent.task_classifier import classify_task' in line:
        # Insert after this line
        lines.insert(i+1, 'from agent.task_analyzer import TaskAnalyzer\n')
        lines.insert(i+2, 'from agent.execution_strategy import decide_execution_strategy\n')
        print(f'Inserted imports after line {i+1}')
        break

# Re-read lines after insertion (adjust indices)
# Actually, we'll work with the modified list

# --- Edit 2: Remove auto_select_skills fallback block (lines 827-835) ---
# This is the else block after "if ecosystem_context:"
# Find the line with "else:" that comes after "if ecosystem_context:"
for i in range(len(lines)):
    if lines[i].strip() == 'else:' and i > 800:
        # Check if next line has auto_select_skills import
        if i+1 < len(lines) and 'from agent.skills import auto_select_skills' in lines[i+1]:
            # Find the end of this block (the line with compose_skills(auto)
            end = i
            for j in range(i, min(i+20, len(lines))):
                if 'compose_skills(auto)' in lines[j]:
                    end = j
                    break
            # Remove lines from i to end (inclusive)
            del lines[i:end+1]
            print(f'Removed auto_select_skills fallback block: lines {i+1} to {end+1}')
            break

# --- Edit 3: Replace parallel_builder block with dynamic_builder ---
# Find "if _complexity == \"complex\":"
for i in range(len(lines)):
    if 'if _complexity == "complex":' in lines[i]:
        start = i
        # Find the end of this block (before "elif _complexity == "medium":")
        end = i
        for j in range(i, min(i+100, len(lines))):
            if 'elif _complexity == "medium":' in lines[j]:
                end = j - 1
                break
        # Replace this block
        new_block = '''    if _complexity == "complex":
        # --- Complex: use dynamic builder based on task analysis ---
        try:
            from agent.task_analyzer import TaskAnalyzer
            from agent.execution_strategy import decide_execution_strategy
            
            # Analyze the task to identify work components
            analyzer = TaskAnalyzer()
            analysis = analyzer.analyze(user_input)
            
            # Decide execution strategy (single/sequential/parallel)
            strategy = decide_execution_strategy(analysis, user_input)
            
            # Run dynamic build with approval if needed
            if session.mode != "plan":
                dynamic_result = run_dynamic_build_with_approval(
                    user_input=user_input,
                    analysis=analysis,
                    strategy=strategy,
                    project_context=getattr(session, "project_map", ""),
                    skill_context=ecosystem_context or "",
                    max_turns_per_agent=60,
                )
                if dynamic_result:
                    session.append({"role": "user", "content": user_input})
                    session.append({"role": "assistant", "content": dynamic_result})
                    _result = LoopResult(status="done", content=dynamic_result, turns_used=0)
                    try:
                        from agent.persistence import session_store
                        store = session_store()
                        store.update_meta(
                            cumulative_in_tokens=session.cumulative_in_tokens,
                            cumulative_out_tokens=session.cumulative_out_tokens,
                            mode=session.mode, profile=session.profile,
                        )
                        store.write_meta_marker()
                    except Exception:
                        pass
                    run_hooks("Stop", "", {})
                    ui.publish_turn_summary(status="done", turns_used=0)
                    ui.turn_end(
                        tokens_in=session.cumulative_in_tokens,
                        tokens_out=session.cumulative_out_tokens,
                    )
                    return _result
        except Exception as _de:
            ui.muted(f"(dynamic builder skipped: {_de})")
        # Dynamic builder returned None or failed — fall through to the pipeline/planner path below.
        if _should_plan(user_input):
            plan_dict, plan_prose = ask_planner(user_input)
            if plan_dict:
                ui.plan(plan_dict)
            elif plan_prose:
                ui.plan_prose(plan_prose)

'''
        # Replace lines[start:end+1] with new_block lines
        lines[start:end+1] = [new_block]
        print(f'Replaced parallel_builder block (lines {start+1} to {end+1}) with dynamic_builder')
        break

# Write the modified file
with open('src/agent/agent_loop.py', 'w') as f:
    f.writelines(lines)

print('Edits applied successfully!')