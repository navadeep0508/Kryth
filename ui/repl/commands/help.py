from __future__ import annotations

from rich.markdown import Markdown

from agent.agent import Agent
from ui.repl.commands.base import CommandGroup

HELP_TEXT = """\
## Commands

- `/help` - Show this help
- `/exit` or `/quit` - Exit the agent
- `/clear` - Clear conversation history for a fresh start (old session is still saved)
- `/config` - Show current configuration options and their values
- `/model <name>` - Change the model mid-session (e.g. `/model gpt-4o` or `/model anthropic/claude-sonnet-4.5`)
- `Shift+Tab` - Cycle approval mode (ask → on fail → auto → auto-edit → read-only → yolo)
- `/thinking [on|off|low|medium|high]` - Show or configure model reasoning (e.g. `/thinking high`)
- `/stats` - Show session statistics (token counts, time elapsed, tool calls)
- `/tools` - List available tools the agent can use
- `/mcp` - Show connected MCP server status

## Sessions

- `/sessions [all]` - List saved sessions, newest first (e.g. `/sessions all` for every directory)
- `/sessions rm` - Delete a saved session interactively (scroll ↑↓, Enter to pick)
- `/sessions rm <n|id>` - Delete a saved session by number or id prefix
- `/resume` - Load a saved session interactively (scroll ↑↓, Enter to pick)
- `/resume <n|id>` - Load a saved session by number or id prefix
- `/checkpoint [label]` - Save a checkpoint now, with an optional name (e.g. `/checkpoint before refactor`)
- `/checkpoints` - List checkpoints in this session
- `/rewind` - Roll back to a checkpoint interactively (scroll ↑↓, Enter to pick)
- `/rewind <n|id>` - Roll back to a checkpoint by number or id prefix

## Tips

- Just type your message to chat with the agent
- Type `/` to see every command and keep typing to filter them; `↑`/`↓` to pick, `Enter` to run, `Tab` to fill in
- The agent can read, write, and execute code
- Some operations require approval (can be configured)
- Sessions are saved after every turn; `kryth --resume` picks the last one up
"""


class HelpCommands(CommandGroup):

    def show_help(self, agent: Agent, args: list[str]) -> None:
        self.console.print(Markdown(HELP_TEXT))
