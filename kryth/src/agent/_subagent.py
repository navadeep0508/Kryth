"""Spawn focused subagents — single or in parallel — in isolated sessions.

Supports typed sub-agent roles for multi-agent coordination:
    - Manager agent: plans and coordinates
    - Browser agent: handles web tasks
    - Tool agent: handles files/APIs
    - Recovery agent: handles failures
    - QA agent: validates results

Usage:
    # Basic subagent
    result = spawn_agent("Write tests", "Create unit tests for auth module")

    # Typed subagent
    from _subagent import AgentType
    result = spawn_typed_agent(AgentType.BROWSER, "Login to GitHub")
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from agent import ui
from agent.tools._results import err


class AgentType(Enum):
    """Typed sub-agent roles for multi-agent coordination."""

    MANAGER = "manager"
    BROWSER = "browser"
    TOOL = "tool"
    RECOVERY = "recovery"
    QA = "qa"
    GENERAL = "general"


# System prompt suffixes for each agent type
AGENT_SYSTEM_PROMPTS = {
    AgentType.MANAGER: (
        "You are a MANAGER agent that plans and coordinates work. "
        "Your job is to break down complex tasks into smaller sub-tasks "
        "and assign them to specialized agents. Focus on architecture, "
        "dependencies, and ensuring sub-tasks fit together. "
        "Be concise and delegate execution to sub-agents."
    ),
    AgentType.BROWSER: (
        "You are a BROWSER agent specialized in web automation tasks. "
        "You can navigate websites, fill forms, click buttons, extract data, "
        "and interact with web pages. Use browser tools for everything. "
        "Focus on reliable selectors and waiting for page loads."
    ),
    AgentType.TOOL: (
        "You are a TOOL agent specialized in file system, shell, and API operations. "
        "You can read/write files, run commands, search code, and interact with "
        "APIs. Focus on precise file operations and correct command usage."
    ),
    AgentType.RECOVERY: (
        "You are a RECOVERY agent specialized in diagnosing and fixing failures. "
        "When a task fails, analyze the error, identify root causes, and propose "
        "or implement fixes. Focus on understanding what went wrong and how to "
        "get back on track."
    ),
    AgentType.QA: (
        "You are a QA agent specialized in validating results. "
        "Your job is to verify that outputs meet requirements, find bugs, "
        "check edge cases, and ensure quality. Be thorough and report issues clearly."
    ),
    AgentType.GENERAL: (
        "You are a subagent spawned by the main agent. "
        "Focus only on the task below. When finished, reply with a "
        "concise final summary and stop calling tools."
    ),
}

SUBAGENT_SYSTEM_SUFFIX = (
    "You are a subagent spawned by the main agent. "
    "Focus only on the task below. When finished, reply with a "
    "concise final summary and stop calling tools."
)


@dataclass
class SubagentTask:
    """A task to be executed by a subagent."""

    description: str
    prompt: str
    agent_type: AgentType = AgentType.GENERAL
    max_turns: int = 8
    expected_output: str = ""
    depends_on: list[str] = None  # IDs of tasks this depends on

    def __post_init__(self):
        if self.depends_on is None:
            self.depends_on = []


def _build_nested(
    description: str,
    prompt: str,
    parent_depth: int,
    agent_type: AgentType = AgentType.GENERAL,
):
    """Construct a fresh Session for a subagent at depth = parent+1."""
    from agent.session import Session
    from agent.prompts import SYSTEM_PROMPT

    suffix = AGENT_SYSTEM_PROMPTS.get(agent_type, SUBAGENT_SYSTEM_SUFFIX)

    nested = Session()
    nested.system_prompt = f"{SYSTEM_PROMPT}\n\n{suffix}"
    nested.ensure_system()
    nested.append({
        "role": "user",
        "content": f"[Task from parent agent: {description}]\n\n{prompt}",
    })
    nested.depth = parent_depth + 1
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


def spawn_agent(description, prompt, max_turns=8, agent_type=None):
    """Spawn a single subagent to complete a focused task.

    Args:
        description: Human-readable task description.
        prompt: Detailed instructions for the subagent.
        max_turns: Maximum tool-use iterations.
        agent_type: Optional AgentType for specialized behavior.

    Returns:
        The subagent's final output as a string.
    """
    from agent.session import push_session, pop_session, get_session
    from agent.agent_loop import run_inner_loop

    if agent_type is not None and isinstance(agent_type, str):
        try:
            agent_type = AgentType(agent_type)
        except ValueError:
            agent_type = AgentType.GENERAL
    elif agent_type is None:
        agent_type = AgentType.GENERAL

    parent = get_session()
    if parent.depth >= 3:
        return err(
            "INVALID_STATE",
            "subagent nesting depth limit (3) reached",
        )

    nested = _build_nested(description, prompt, parent.depth, agent_type)
    token = push_session(nested)
    ui.subagent_start(depth=nested.depth, description=description)

    try:
        result = run_inner_loop(nested, max_turns, verbose_usage=False)
        final_text = _summarize_result(result, max_turns)
        ui.subagent_end(depth=nested.depth)
        return final_text
    finally:
        pop_session(token)


def spawn_typed_agent(agent_type: AgentType, description: str, prompt: str = "",
                       max_turns: int = 8) -> str:
    """Spawn a typed subagent with behavior optimized for its role.

    Args:
        agent_type: The type of agent to spawn.
        description: Task description.
        prompt: Detailed instructions.
        max_turns: Maximum tool-use iterations.

    Returns:
        The subagent's output.
    """
    task = SubagentTask(
        description=description,
        prompt=prompt or description,
        agent_type=agent_type,
        max_turns=max_turns,
    )
    return spawn_agent(task.description, task.prompt, task.max_turns, task.agent_type)


# ---------------------------------------------------------------------------
# Parallel fan-out
# ---------------------------------------------------------------------------

MAX_PARALLEL_AGENTS = 4


def _run_one(idx: int, description: str, prompt: str, max_turns: int,
             parent_depth: int, agent_type: AgentType = AgentType.GENERAL) -> tuple[int, str]:
    """Worker body: build session, push into THIS thread's context, run."""
    from agent.session import push_session, pop_session
    from agent.agent_loop import run_inner_loop

    nested = _build_nested(description, prompt, parent_depth, agent_type)
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

    ``tasks`` is a list of ``SubagentTask`` dicts or dicts with
    ``{description, prompt, max_turns?, agent_type?}`` keys.

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
    if parent.depth >= 2:
        return err("INVALID_STATE", "subagent nesting depth limit (2) reached")
    parent_depth = parent.depth

    plan: list[tuple[int, str, str, int, AgentType]] = []
    for i, t in enumerate(tasks):
        if isinstance(t, SubagentTask):
            desc = t.description
            prompt = t.prompt
            mt = t.max_turns
            at = t.agent_type
        elif isinstance(t, dict):
            desc = t.get("description")
            prompt = t.get("prompt")
            raw_mt = t.get("max_turns", 6)
            raw_at = t.get("agent_type", AgentType.GENERAL)
            if isinstance(raw_at, str):
                try:
                    at = AgentType(raw_at)
                except ValueError:
                    at = AgentType.GENERAL
            else:
                at = raw_at or AgentType.GENERAL
            try:
                mt = max(1, min(int(raw_mt), 30))
            except (TypeError, ValueError):
                return err("BAD_ARGS", f"task {i} max_turns must be an integer")
        else:
            return err("BAD_ARGS", f"task {i} must be an object or SubagentTask")

        if not isinstance(desc, str) or not desc.strip():
            return err("BAD_ARGS", f"task {i} needs a non-empty 'description'")
        if not isinstance(prompt, str) or not prompt.strip():
            return err("BAD_ARGS", f"task {i} needs a non-empty 'prompt'")
        plan.append((i, desc.strip(), prompt.strip(), mt, at))

    workers = max(1, min(int(max_concurrency), MAX_PARALLEL_AGENTS, len(plan)))

    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kryth-sub") as ex:
        ctx = contextvars.copy_context()
        futures = [
            ex.submit(ctx.copy().run, _run_one, idx, desc, prompt, mt, parent_depth, at)
            for (idx, desc, prompt, mt, at) in plan
        ]
        for fut in as_completed(futures):
            idx, text = fut.result()
            results[idx] = text

    ordered = [
        f"=== [{i}] {desc} ===\n{results.get(i, '(missing result)')}"
        for (i, desc, _, _, _) in plan
    ]
    return "\n\n".join(ordered)
