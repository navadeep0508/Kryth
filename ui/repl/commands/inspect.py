from __future__ import annotations

from rich.table import Table
from rich.text import Text

from agent.agent import Agent
from tools.mcp.client import MCPServerStatus
from ui.format import format_duration
from ui.repl.commands.base import CommandGroup

MCP_STATUS_STYLES = {
    MCPServerStatus.CONNECTED: "success",
    MCPServerStatus.CONNECTING: "warning",
    MCPServerStatus.DISCONNECTED: "muted",
    MCPServerStatus.ERROR: "error",
}


class InspectCommands(CommandGroup):

    def show_stats(self, agent: Agent, args: list[str]) -> None:
        session = agent.session
        usage = session.context_manager.total_usage

        table = Table.grid(padding=(0, 1))
        table.add_column(style="muted")
        table.add_column(style="subtitle")
        rows = [
            ("Session", session.session_id),
            ("Turns", str(session.turns)),
            ("Messages", str(session.context_manager.message_count)),
            ("Checkpoints", str(len(session.checkpoints()))),
            ("Duration", format_duration(session.created_at)),
            ("Prompt tokens", f"{usage.prompt_tokens:,}"),
            ("Completion tokens", f"{usage.completion_tokens:,}"),
            ("Cached tokens", f"{usage.cached_tokens:,}"),
            ("Total tokens", f"{usage.total_tokens:,}"),
        ]
        for label, value in rows:
            table.add_row(f"{label}:", value)

        self.console.print(Text("Session statistics", style="highlight"))
        self.console.print(table)

    def show_tools(self, agent: Agent, args: list[str]) -> None:
        tools = agent.session.tool_registry.get_tools()
        if not tools:
            self.console.print(Text("No tools available.", style="muted"))
            return

        table = Table.grid(padding=(0, 2))
        table.add_column(style="subtitle")
        table.add_column(style="muted")
        for tool in sorted(tools, key=lambda t: t.name):
            description = tool.description.strip().splitlines()[0] if tool.description else ""
            table.add_row(tool.name, description)

        self.console.print(Text(f"Available tools ({len(tools)})", style="highlight"))
        self.console.print(table)

    def show_mcp(self, agent: Agent, args: list[str]) -> None:
        clients = agent.session.mcp_manager.clients
        if not clients:
            self.console.print(Text("No MCP servers configured.", style="muted"))
            return

        table = Table.grid(padding=(0, 2))
        table.add_column(style="subtitle")
        table.add_column()
        table.add_column(style="muted")
        for client in clients:
            status_style = MCP_STATUS_STYLES.get(client.status, "muted")
            table.add_row(
                client.name,
                Text(client.status.value, style=status_style),
                f"{len(client.tools)} tools",
            )

        self.console.print(Text(f"MCP servers ({len(clients)})", style="highlight"))
        self.console.print(table)
