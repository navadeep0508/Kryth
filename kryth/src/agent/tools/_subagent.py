"""Spawn focused subagents — single or in parallel — in isolated sessions."""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent import ui
from agent.tools._results import err


SUBAGENT_SYSTEM_SUFFIX = (
    "You are a subagent spawned by the main agent. "
    "Focus only on the task below. When finished, reply with a "
    "concise final summary and stop calling tools."
)


def _build_nested(description: str, prompt: str, parent_depth: int, parent_can_spawn: bool = True, parent_profile: str = "default"):
    """Construct a fresh Session for a subagent at depth = parent+1."""
    from agent.session import Session
    from agent.prompts import SYSTEM_PROMPT

    nested = Session()
    nested.system_prompt = f"{SYSTEM_PROMPT}\n\n{SUBAGENT_SYSTEM_SUFFIX}"
    nested.ensure_system()
    nested.append({
        "role": "user",
        "content": f"[Task from parent agent: {description}]\n\n{prompt}",
    })
    nested.depth = parent_depth + 1
    # All subagents (depth >= 1) are workers and cannot spawn further agents.
    # Only the root coordinator (depth=0) has can_spawn=True.
    nested.can_spawn = False
    # Inherit parent's permission profile so yolo/auto runs stay unattended.
    nested.profile = parent_profile
    return nested


def _summarize_result(result, max_turns: int) -> str:
    if result.status == "interrupted":
        return "(subagent interrupted)"
    if result.status == "api_error":
        return "(subagent failed due to an LLM API error)"
    if result.status == "max_turns":
        return (
            f"(subagent reached the {max_turns}-turn limit without finishing; "
            f"used {result.turns_used} turns)"
        )
    return result.content or ""


def spawn_agent(description, prompt, max_turns=8):
    from agent.session import push_session, pop_session, get_session
    from agent.agent_loop import run_inner_loop

    parent = get_session()
    if parent.depth >= 5:
        return err(
            "INVALID_STATE",
            "subagent nesting depth limit (5) reached",
        )

    nested = _build_nested(description, prompt, parent.depth, getattr(parent, 'can_spawn', True), getattr(parent, 'profile', 'default'))
    token = push_session(nested)
    ui.subagent_start(depth=nested.depth, description=description)

    try:
        result = run_inner_loop(nested, max_turns, verbose_usage=False)
        final_text = _summarize_result(result, max_turns)
        ui.subagent_end(depth=nested.depth)
        return final_text
    finally:
        pop_session(token)


# ---------------------------------------------------------------------------
# Parallel fan-out
# ---------------------------------------------------------------------------

MAX_PARALLEL_AGENTS = 16


def _run_one(idx: int, description: str, prompt: str, max_turns: int,
             parent_depth: int, parent_can_spawn: bool = True, parent_profile: str = "default") -> tuple[int, str]:
    """Worker body: build session, push into THIS thread's context, run."""
    from agent.session import push_session, pop_session
    from agent.agent_loop import run_inner_loop

    nested = _build_nested(description, prompt, parent_depth, parent_can_spawn, parent_profile)
    token = push_session(nested)
    ui.subagent_start(depth=nested.depth, description=f"[{idx}] {description}")
    try:
        result = run_inner_loop(nested, max_turns, verbose_usage=False)
        final_text = _summarize_result(result, max_turns)
    except Exception as e:
        final_text = f"(subagent {idx} crashed: {type(e).__name__}: {e})"
    finally:
        pop_session(token)
        ui.subagent_end(depth=nested.depth)
    return idx, final_text


def spawn_agents_parallel(tasks, max_concurrency: int = 3):
    """Run several subagents concurrently and aggregate their outputs.

    ``tasks`` is a list of ``{description, prompt, max_turns?}`` dicts.
    Each task runs in its own thread with an isolated Session pushed
    into that thread's ContextVar; no two subagents observe each other's
    messages. The parent depth is captured up-front, so depth-budget
    enforcement still works.

    Caveats:
      - File-system / git operations across threads share the same cwd
        and are NOT serialised — fan out READS, not concurrent writes to
        the same file.
      - The renderer accepts interleaved updates; output ordering across
        agents may interleave on stdout. The aggregated return value IS
        deterministic — sorted by the task's position in the input list.
    """
    from agent.session import get_session

    if not isinstance(tasks, list) or not tasks:
        return err("BAD_ARGS", "spawn_agents_parallel: tasks must be a non-empty list")

    parent = get_session()
    if parent.depth >= 5:
        return err("INVALID_STATE", "subagent nesting depth limit (5) reached")
    parent_depth = parent.depth
    parent_can_spawn = getattr(parent, 'can_spawn', True)
    parent_profile = getattr(parent, 'profile', 'default')

    plan: list[tuple[int, str, str, int]] = []
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            return err("BAD_ARGS", f"task {i} must be an object")
        desc = t.get("description")
        prompt = t.get("prompt")
        if not isinstance(desc, str) or not desc.strip():
            return err("BAD_ARGS", f"task {i} needs a non-empty 'description'")
        if not isinstance(prompt, str) or not prompt.strip():
            return err("BAD_ARGS", f"task {i} needs a non-empty 'prompt'")
        raw_mt = t.get("max_turns", 6)
        try:
            mt = max(1, min(int(raw_mt), 1000))
        except (TypeError, ValueError):
            return err("BAD_ARGS", f"task {i} max_turns must be an integer")
        plan.append((i, desc.strip(), prompt.strip(), mt))

    workers = max(1, min(int(max_concurrency), MAX_PARALLEL_AGENTS, len(plan)))

    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aicoder-sub") as ex:
        # contextvars.copy_context().run wraps each worker so the
        # parent's context (env vars, profile bindings) is inherited
        # but ContextVar.set inside the worker doesn't leak back out.
        ctx = contextvars.copy_context()
        futures = [
            ex.submit(ctx.copy().run, _run_one, idx, desc, prompt, mt, parent_depth, parent_can_spawn, parent_profile)
            for (idx, desc, prompt, mt) in plan
        ]
        for fut in as_completed(futures):
            idx, text = fut.result()
            results[idx] = text

    ordered = [
        f"=== [{i}] {desc} ===\n{results.get(i, '(missing result)')}"
        for (i, desc, _, _) in plan
    ]
    return "\n\n".join(ordered)
