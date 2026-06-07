"""KRYTH Terminal Engine — autonomous terminal execution layer.

Provides persistent shell sessions, process management, structured output
parsing, build/test loops, and autonomous recovery on top of the existing
Rich UI and tool infrastructure.

Public API:
    get_shell()           — acquire the per-process PersistentShell
    terminal_state        — TerminalStateManager singleton
    process_manager       — ProcessManager singleton
    output_parser         — OutputParser singleton
    shell_memory          — TerminalMemory singleton
"""

from __future__ import annotations

from agent.terminal.shell import PersistentShell, get_shell
from agent.terminal.state import TerminalStateManager, terminal_state
from agent.terminal.process_manager import ProcessManager, process_manager
from agent.terminal.output_parser import OutputParser, output_parser
from agent.terminal.log_compressor import LogCompressor, log_compressor
from agent.terminal.memory import TerminalMemory, shell_memory
from agent.terminal.recovery import RecoveryEngine, recovery_engine
from agent.terminal.command_planner import CommandPlanner, command_planner
from agent.terminal.build_loop import BuildTestLoop
from agent.terminal.interactive import InteractiveHandler, interactive_handler
from agent.terminal.coordinator import TerminalCoordinator, terminal_coordinator
from agent.terminal.worker import TerminalWorker

__all__ = [
    "PersistentShell",
    "get_shell",
    "TerminalStateManager",
    "terminal_state",
    "ProcessManager",
    "process_manager",
    "OutputParser",
    "output_parser",
    "LogCompressor",
    "log_compressor",
    "TerminalMemory",
    "shell_memory",
    "RecoveryEngine",
    "recovery_engine",
    "CommandPlanner",
    "command_planner",
    "BuildTestLoop",
    "InteractiveHandler",
    "interactive_handler",
    "TerminalCoordinator",
    "terminal_coordinator",
    "TerminalWorker",
]
