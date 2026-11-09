# Slash commands

Anything typed after a `/` inside the TUI. `/help` prints this list in the session.

| Command | What it does |
| --- | --- |
| `/help` | Show all commands |
| `/model <name>` | Switch models mid-session |
| `/thinking [on\|off\|low\|medium\|high]` | Show, hide, or retune the model's reasoning |
| `/reasoning [on\|off\|low\|medium\|high]` | Alias for `/thinking` |
| `/clear` | Start a fresh conversation (the old one stays saved) |
| `/config` | Show the active configuration |
| `/stats` | Session statistics: tokens, elapsed time, tool calls |
| `/tools` | List available tools |
| `/mcp` | Show MCP server status |
| `/setup` | Configure a provider interactively |
| `/providers` | List known providers |
| `/models` | List models available from the current provider |
| `/doctor` | Run configuration and environment diagnostics |
| `/sessions [all]` | List saved sessions, newest first |
| `/sessions rm <n\|id>` | Delete a saved session |
| `/resume <n\|id>` | Load a saved session into the current one |
| `/checkpoint [label]` | Save a checkpoint now, with an optional name |
| `/checkpoints` | List the checkpoints in this session |
| `/rewind <n\|id>` | Roll the conversation back to a checkpoint |
| `/exit`, `/quit` | Leave the agent |

## Positions and ids

Commands that take `<n|id>` accept either. The number is the position from the last listing that command's family printed, so `/checkpoints` then `/rewind 2` does what it looks like. Ids work too, and a prefix is enough — `/resume 3f2a1c` finds `3f2a1c8b`.

## Settings that stick

`/model` writes the new model back to the config file, so it survives the session. `/thinking` applies to the running session only. Use `Shift+Tab` to cycle the approval policy; see [Configuration](configuration.md) to change its default permanently.

Related: [Approvals](approvals.md), [Sessions](sessions.md), [Tools](tools.md).
