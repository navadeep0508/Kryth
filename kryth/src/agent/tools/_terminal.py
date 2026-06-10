"""Terminal engine tools — LLM-callable interface to the terminal engine.

These tools replace or augment run_command with intelligent capabilities:
  - shell_exec: run via persistent shell with parsed output
  - shell_state: get terminal state (cwd, git, venv, ports, docker)
  - shell_plan: build and preview an execution plan
  - shell_run_plan: execute an execution plan with auto-recovery
  - shell_build_test_loop: run full build/test/fix loop
  - process_list: list tracked processes
  - process_kill: stop/kill a process
  - terminal_memory: recall past workflows
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from agent.tools._results import err
from agent.terminal.shell import get_shell
from agent.terminal.state import terminal_state
from agent.terminal.process_manager import process_manager
from agent.terminal.output_parser import output_parser
from agent.terminal.log_compressor import log_compressor
from agent.terminal.command_planner import command_planner
from agent.terminal.recovery import RecoveryEngine, BuildTestLoop
from agent.terminal.memory import shell_memory
from agent.terminal.coordinator import terminal_coordinator


_MAX_OUTPUT = 100000  # chars sent to LLM


def shell_exec(
    command: str,
    timeout: int = 30,
    worker: str = "misc",
    compress_output: bool = True,
) -> str:
    """Execute a command in the persistent terminal engine.

    Unlike run_command, this:
    - Uses a persistent shell (cwd/env survive across calls)
    - Automatically parses errors into structured output
    - Compresses verbose logs before returning
    - Tracks the command in terminal memory

    Args:
        command: Shell command to execute.
        timeout: Seconds to wait (default 30, max 600).
        worker: Named worker terminal to use (backend/frontend/tests/docker/misc).
        compress_output: If True, compress logs before returning to save context.
    """
    timeout = max(1, min(int(timeout), 600))

    result = terminal_coordinator.run(worker, command, timeout=timeout)

    # Record in memory
    shell_memory.record_command(
        command=command,
        exit_code=result.exit_code,
        cwd=terminal_coordinator.worker(worker).cwd,
    )

    if compress_output and len(result.output) > _MAX_OUTPUT:
        compressed = log_compressor.compress_for_llm(result.output, result.exit_code)
        return compressed

    if result.exit_code != 0:
        parsed = output_parser.parse(result.output, result.exit_code)
        return err(
            "NON_ZERO_EXIT",
            f"command exited with code {result.exit_code}",
            parsed.to_llm_context(),
        )

    return result.output[:_MAX_OUTPUT] if len(result.output) > _MAX_OUTPUT else result.output


def shell_state(force_refresh: bool = False) -> str:
    """Get the current terminal environment state.

    Returns: cwd, git branch, active venv, node version,
    open ports, running servers, docker containers.
    """
    return terminal_state.format_summary()


def shell_plan(goal: str, cwd: str = ".") -> str:
    """Build and preview an execution plan for a goal.

    Detects project type from marker files and generates the correct
    commands (e.g., uv run pytest for Python projects using uv).

    Args:
        goal: One of 'test', 'build', 'install', or a description.
        cwd: Working directory (default: current directory).
    """
    cwd = cwd or "."
    goal_lower = goal.lower().strip()

    if "test" in goal_lower:
        plan = command_planner.plan_tests(cwd)
    elif "build" in goal_lower or "compile" in goal_lower:
        plan = command_planner.plan_build(cwd)
    elif "install" in goal_lower or "dep" in goal_lower:
        plan = command_planner.plan_install(cwd)
    else:
        return err("BAD_ARGS", f"unknown goal '{goal}'", "use: test, build, install")

    return plan.format()


def shell_run_plan(
    goal: str,
    cwd: str = ".",
    max_retries: int = 2,
    worker: str = "misc",
) -> str:
    """Execute a smart execution plan with automatic error recovery.

    Detects the project type, builds the correct command sequence,
    runs each step, and automatically recovers from common errors
    (missing deps, etc.) before retrying.

    Args:
        goal: 'test', 'build', or 'install'.
        cwd: Working directory.
        max_retries: How many auto-recovery attempts per failing step.
        worker: Which terminal worker to use.
    """
    cwd = cwd or "."
    goal_lower = goal.lower().strip()
    w = terminal_coordinator.worker(worker)

    if cwd != "." and not w.cd(cwd):
        return err("EXEC_FAILED", f"could not change to directory: {cwd}")

    if "test" in goal_lower:
        plan = command_planner.plan_tests(cwd)
    elif "build" in goal_lower or "compile" in goal_lower:
        plan = command_planner.plan_build(cwd)
    elif "install" in goal_lower or "dep" in goal_lower:
        plan = command_planner.plan_install(cwd)
    else:
        return err("BAD_ARGS", f"unknown goal '{goal}'", "use: test, build, install")

    if not plan.steps:
        return err("INVALID_STATE", f"no steps detected for {plan.stack} project")

    log_lines: list[str] = []

    def _run(command: str, timeout: int) -> tuple[str, int]:
        result = w.execute(command, timeout=timeout)
        return result.output, result.exit_code

    loop = BuildTestLoop(run_fn=_run, recovery=RecoveryEngine())
    result = loop.run(
        steps=plan.steps,
        cwd=cwd,
        max_retries=max_retries,
        on_progress=log_lines.append,
    )

    # Record workflow if it succeeded
    if result.success:
        shell_memory.record_workflow(
            name=goal_lower,
            commands=plan.commands(),
            stack=plan.stack,
            cwd=cwd,
        )

    log_text = "\n".join(log_lines)
    if result.success:
        return f"SUCCESS\n{log_text}"
    else:
        final = log_compressor.compress_for_llm(
            result.final_output or "", result.final_parsed.exit_code if result.final_parsed else 1
        )
        return err(
            "EXEC_FAILED",
            f"plan failed after {result.iterations} retries",
            f"{log_text}\n\n{final}",
        )


def shell_build_test_loop(
    build_command: str,
    test_command: str,
    fix_hint: str = "",
    max_iterations: int = 5,
    worker: str = "misc",
) -> str:
    """Run a build → test → fix → retry loop.

    Keeps iterating until tests pass or max_iterations is reached.
    Uses structured output parsing to detect failures automatically.

    Args:
        build_command: Command to build/compile.
        test_command: Command to run tests.
        fix_hint: Optional hint for what fix to attempt on failure.
        max_iterations: Max build-test cycles.
        worker: Which terminal worker to use.
    """
    w = terminal_coordinator.worker(worker)
    log: list[str] = []

    for iteration in range(1, max_iterations + 1):
        log.append(f"=== Iteration {iteration}/{max_iterations} ===")

        # Build step
        b_result = w.execute(build_command, timeout=300)
        if b_result.exit_code != 0:
            compressed = log_compressor.compress_for_llm(b_result.output, b_result.exit_code)
            log.append(f"BUILD FAILED:\n{compressed}")
            if iteration >= max_iterations:
                break
            log.append("(retrying after build failure)")
            continue
        log.append("BUILD PASSED")

        # Test step
        t_result = w.execute(test_command, timeout=300)
        if t_result.exit_code == 0:
            log.append("TESTS PASSED")
            shell_memory.record_workflow(
                name="build_test",
                commands=[build_command, test_command],
            )
            return f"SUCCESS after {iteration} iteration(s)\n" + "\n".join(log)

        compressed = log_compressor.compress_for_llm(t_result.output, t_result.exit_code)
        log.append(f"TESTS FAILED:\n{compressed}")

        if iteration >= max_iterations:
            break

        log.append("(will retry)")

    return err(
        "EXEC_FAILED",
        f"build/test loop failed after {max_iterations} iterations",
        "\n".join(log),
    )


def process_list() -> str:
    """List all processes tracked by KRYTH's process manager.

    Returns a table with PID, status, CPU%, RAM, ports, and command.
    """
    return process_manager.format_table()


def process_kill(pid: int, force: bool = False) -> str:
    """Stop or force-kill a process by PID.

    Args:
        pid: Process ID to kill.
        force: If True, send SIGKILL immediately instead of SIGTERM.
    """
    if force:
        success = process_manager.kill(pid)
    else:
        success = process_manager.stop(pid)

    if success:
        return f"process {pid} terminated"
    return err("EXEC_FAILED", f"could not terminate process {pid}")


def terminal_memory_recall(query: str = "") -> str:
    """Recall terminal memory — past workflows and successful command sequences.

    Args:
        query: 'test', 'build', 'install', 'deploy', or '' for summary.
    """
    if not query:
        return shell_memory.format_summary()

    wf = shell_memory.recall_workflow(query)
    if wf:
        cmds = "\n  ".join(wf.commands)
        return (
            f"Workflow '{query}' [{wf.stack}] used {wf.success_count} times:\n"
            f"  {cmds}"
        )

    return f"No workflow remembered for '{query}'"
