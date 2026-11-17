# CLI reference

## Install

```bash
pip install kryth
```

To check for and install the latest release:

```bash
kryth upgrade
```

KRYTH needs Python 3.11 or newer. A `Dockerfile` and Compose file live in [`docker/`](../docker/) if you would rather run the agent sandboxed.

When a newer release is available, KRYTH displays a notice when the interactive TUI starts. It never upgrades automatically; run `kryth upgrade` yourself to install it.

## Log in

```bash
kryth login           # opens your browser to authorize with OpenRouter
kryth login --paste   # paste an API key instead
kryth logout          # remove the saved key
```

Browser login is OAuth 2.0 with PKCE against a loopback redirect; the key is written to your user config directory. `--base-url` saves a different API base URL alongside it.

The `API_KEY` environment variable always takes precedence over the saved key, which makes CI and one-off overrides easy:

```bash
API_KEY=sk-... kryth "run the test suite and fix what fails"
```

## Commands

| Command | What it does |
| --- | --- |
| `kryth` | Start the interactive TUI |
| `kryth "your prompt"` | Single-shot mode: run one prompt, print the answer, exit |
| `kryth run [prompt]` | The explicit form of the two above |
| `kryth login` | Authorize with OpenRouter |
| `kryth logout` | Remove the saved API key |
| `kryth upgrade` | Check PyPI and upgrade KRYTH if a newer release is available |
| `kryth sessions` | List saved sessions for this directory |
| `kryth sessions --all` | List sessions from every directory |
| `kryth sessions rm <id>` | Delete a saved session |
| `kryth --version` | Print the installed version |

## Global flags

These go before the command, or on their own with the bare `kryth`.

| Flag | Short | What it does |
| --- | --- | --- |
| `--cwd <path>` | `-c` | Run against a different working directory |
| `--resume <id>` | `-r` | Resume a saved session; an id prefix is enough |
| `--continue` | `-C` | Resume the most recent session for this directory |

## Examples

```bash
kryth                          # interactive TUI in the current repo
kryth "fix the failing test"   # one-shot, good for scripting
kryth --cwd ~/code/api         # work in another checkout
kryth --continue               # pick up where you left off
kryth --resume 3f2a1c          # resume one specific session
```

## Interactive keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Enter` | Send the prompt |
| `Ctrl+J` (often `Shift+Enter` in Windows Terminal) | Add a line to a multiline prompt |
| `Ctrl+P` | Open the command picker; use `/` to filter commands |
| `Tab` / `Up` / `Down` | Navigate slash-command suggestions |
| `Ctrl+O` | Expand or collapse the latest tool output |
| `Esc` / `Ctrl+C` | Interrupt the active agent turn |
| `Ctrl+L` | Clear the visible terminal screen |
| `Shift+Tab` | Cycle the approval policy |
| `Ctrl+D` on an empty prompt | Exit the interactive session |

At an empty prompt, KRYTH shows a short composer hint. Type `/` to filter commands; use `Up` and `Down` or `Tab` to move through matches. `Ctrl+P` opens the same handler-backed command picker.

The slash suggestions list only commands with registered handlers. Run `/help` for descriptions.

Single-shot mode runs the same agent loop as the TUI, including tools and approvals — so pair it with an approval policy that will not block on a prompt nobody is there to answer. See [Approvals](approvals.md).

### Activity stream and approvals

Tool calls appear as compact, borderless activity rows. A running row updates in place with its latest progress, then settles into one concise result. Press `Ctrl+O` to expand the most recent activity and inspect bounded output, source previews, diffs, terminal logs, or diagnostics. The TUI retains a bounded set of recent activities. Noninteractive runs print only the final activity, without a live animation.

Approval-required actions use one keyboard-first selector. Use `Up` and `Down` to select a decision, `Enter` to confirm, `Ctrl+O` to inspect command details, and `Y`, `A`, or `N` as shortcuts for allow once, allow for this session, or reject. `Esc` and `Ctrl+C` cancel without executing the action.

At the end of a turn, KRYTH shows a completion summary with only counters it could derive from tool results. Missing counts are omitted.

Sessions started from either mode land in the same place. See [Sessions](sessions.md).
