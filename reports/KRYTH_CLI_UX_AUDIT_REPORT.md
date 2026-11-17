# KRYTH CLI UX Audit Report

**Audit date:** 2026-11-15  
**Scope:** repository architecture, built-in tool registry, rendering, interactive prompt, commands, approvals, and available automated checks.

## 1. Executive Summary

KRYTH is a Python Click CLI with a Rich and prompt-toolkit interactive REPL backed by a streaming agent loop. The existing agent, schemas, integrations, permissions, and execution pathways are retained. The current interface includes a six-line KRYTH wordmark, startup model/provider/workspace information, streamed Markdown, a persistent multiline prompt, per-tool progress and completion rendering, compact output with expandable details, and approval prompts.

This audit verified 24 default registered tools (19 core tools and 5 subagents), 20 slash-command names (including aliases), and 7 top-level Click command names. Every registered core tool has a named renderer; subagents use a role-aware renderer; future/custom tools use a compact fallback. A concrete status bug was fixed: the elapsed time calculated on tool completion was not passed to the rendered header. Completion duration is now displayed, and status markers use their semantic success/failure styles. The startup now includes the active provider and approval requests explicitly announce that they are waiting.

The complete test suite passes: **201 passed, 1 skipped**. This supports the rendering and unit-test claims below; it does not mean that every external provider, MCP server, command execution mode, or terminal emulator was exercised end to end.

## 2. Repository Architecture

- `main.py` defines the Click entry point and top-level commands. Bare `kryth` launches the REPL; `kryth run <prompt>` performs a one-shot turn.
- `agent/agent.py` owns the agent loop. It emits typed events for starts, streamed text, tool calls, usage, completion, and errors.
- `agent/events.py` defines event types and event construction.
- `tools/registry.py` registers the 19 core tool classes and 5 default subagents. It validates parameters, invokes approval checks and hooks, executes tools, handles exceptions, and records successful file changes.
- `tools/core/` contains file, shell, search, plan, and persistent terminal tools. `tools/network/` contains web search and fetch. `tools/memory/` contains memory access. `tools/subagents/` defines the default delegated agents. `tools/mcp/` connects configured MCP servers and registers their discovered tools dynamically.
- `ui/stream.py` translates agent events into TUI updates and intentionally discards private reasoning events.
- `ui/tui.py` owns presentation of thinking, assistant streaming, tool lifecycle, approval, usage, and expandable tool output. It does not execute tools.
- `ui/components/` contains welcome, Markdown, tool output, diff, approval, progress, spinner, status, and related reusable renderers. `ui/components/tool_call.py` is the renderer registry and fallback boundary.
- `ui/repl/` contains the prompt, keybindings, completion, banner, and 20 slash-command names implemented through command groups.
- `ui/theme/` centralizes palette and Rich theme styles. `ui/console.py` respects `NO_COLOR`; animation can be disabled by config or reduced-motion environment flags.
- `config/` stores model/provider, workspace, approval, UI, tool-allowlist, and MCP configuration.

## 3. Complete Tool Inventory

The inventory below was derived from `create_default_registry(Config()).get_tools()` at audit time. The default registry has 24 tools. A configured MCP server may add tools at runtime; there are no MCP server entries in the default configuration used for this audit. Tool schemas were read from each registered tool's Pydantic schema and are summarized here by parameter names.

| Category | Tool | Current implementation / schema fields | UI status | Issues / limits |
|---|---|---|---|---|
| Files | `read` | Read file; `path, offset, limit` | Named renderer; line count/range and expandable syntax-highlighted content | No separate metadata-inspection tool |
| Files | `write` | Write file; `path, content, create_directories` | Named renderer; created path/line count and expandable diff | No dedicated rename/move/delete tool |
| Files | `edit` | Replace text; `path, old_string, new_string, replace_all` | Named renderer; path and added/removed counts with expandable diff | Output depends on diff metadata from execution |
| Files | `apply_patch` | Apply patch; `patch, dry_run` | Named renderer; operation/file counts and expandable patch | Multi-file status is summarized rather than shown inline per file |
| Files | `list_directories` | Directory listing; `path, include_hidden` | Named renderer; entry count and details | Large detail output remains subject to output handling |
| Search | `grep` | Search content; `pattern, path, case_insensitive` | Named renderer; compact match summary/details | No dedicated progress granularity per file |
| Search | `glob` | Match paths; `pattern, path` | Named renderer; compact result count/details | — |
| Search | `search` | Web search; `query, max_results` | Named renderer; query/result summary and expandable sources/output | Source rendering reflects returned payload; no source count is fabricated |
| Research | `fetch` | Fetch URL; `url, timeout` | Named renderer; URL/result summary and expandable output | No browser interaction or navigation state |
| Terminal | `bash` | Run command; `command, timeout, cwd` | Named renderer; command, exit status/output details, failure category and exit code | Output is not live stdout/stderr streaming during execution; progress callback is a concise status line |
| Terminal | `terminal_start` | Start persistent process; `command, name, cwd, auto_restart, max_restarts, startup_timeout` | Shared named persistent-terminal renderer | Not independently exercised against a real long-running process in this audit |
| Terminal | `terminal_status` | Get process status; `name` | Shared named persistent-terminal renderer | — |
| Terminal | `terminal_logs` | Read logs; `name, lines` | Shared named persistent-terminal renderer; session/status/line count | Large logs are placed in expandable details |
| Terminal | `terminal_input` | Send process input; `name, text` | Shared named persistent-terminal renderer | — |
| Terminal | `terminal_stop` | Stop process; `name, timeout` | Shared named persistent-terminal renderer | — |
| Terminal | `terminal_restart` | Restart process; `name, timeout` | Shared named persistent-terminal renderer | — |
| Terminal | `terminal_list` | List processes; `running_only` | Shared named persistent-terminal renderer | — |
| Agent | `plan` | Plan operations; `action, id, content` | Named renderer; structured plan output | — |
| Memory | `memory` | Memory operation; `action, key, value` | Named renderer; structured memory summary | This is the project memory tool, not a separate vector database integration |
| Delegation | `subagent_codebase_investigator` | Investigate code; `goal` | Role-aware subagent summary, termination reason and tool count; full report collapses | Nested progress is compact and not a separate transcript per agent |
| Delegation | `subagent_code_reviewer` | Review changes; `goal` | Role-aware subagent summary and expandable report | Same shared subagent limitation |
| Delegation | `subagent_software_architect` | Implement/architect; `goal` | Role-aware subagent summary and expandable report | Same shared subagent limitation |
| Delegation | `subagent_test_writer` | Write tests; `goal` | Role-aware subagent summary and expandable report | Same shared subagent limitation |
| Delegation | `subagent_debugger` | Debug; `goal` | Role-aware subagent summary and expandable report | Same shared subagent limitation |
| Runtime integrations | Configured MCP tools | Discovered from connected servers; schema varies by server | MCP names/categories and unrecognized tools route to compact fallback; failure path is shared | Zero servers configured in the default audit config; no live MCP connection tested |

### Tools not present in the registry

There is no separately registered browser automation, Python execution, Git operation, LSP, AST retrieval, vision/image, desktop automation, package manager, or test runner tool. Shell commands can invoke installed programs through `bash`; that does not make each program a first-class KRYTH tool. The generic fallback contains category-aware labels for several future/custom names, but it does not create those integrations.

## 4. Before and After Analysis

| Area | Observed before audit changes | Current result |
|---|---|---|
| Tool completion status | Duration was calculated and then discarded when constructing the header | Completion/failure status and elapsed duration are now passed to the shared header renderer |
| Tool status appearance | Header accepted a semantic icon style but rendered the marker with generic tool style | Marker now uses the supplied semantic style; status text carries success/failure styling |
| Startup context | Welcome composition did not include provider | Welcome includes configured model, active provider, workspace, and concise invitation |
| Approval state | Request did not state the waiting state in the shared TUI entry point | Approval flow displays “Waiting for approval” before request details |
| Tool output | Named renderers already existed for built-ins; output details are separated from summaries | Registry/fallback architecture retained; renderer success/failure coverage extended by existing tests |
| Agent privacy | Reasoning events are handled separately from visible user-facing activity | Private reasoning deltas/completions remain suppressed; visible progress is activity labeling |

## 5. Tool UI Audit

`ui/components/tool_call.py` has 20 explicit dictionary entries (including the compatibility alias `list_dir` for `list_directories`). All 19 core tool names map to an explicit renderer. The 5 subagents use a dedicated name-prefix renderer rather than dictionary entries. Other names use a bounded, compact generic fallback. Errors are routed through a shared failure renderer regardless of tool name and include first-line error, category and exit code when provided; additional diagnostics are expandable. Successful output is divided into summary and detail blocks, with expansion controlled by the TUI.

| Renderer path | Registered coverage | Summary / detail behavior | Automated verification |
|---|---:|---|---|
| Named core renderers | 19/19 core names | File counts/diffs, search counts, command/terminal summaries, plan/memory/fetch summaries; details are separate | Test asserts all core names have renderers and invokes each with success and failure-shaped outcomes |
| Subagent renderer | 5/5 defaults | Role, termination and nested tool count summary; report in details | Test checks each default role has a specialized summary; success/failure shapes tested |
| Generic / MCP fallback | Dynamic; 0 MCP tools in default config | Name/category, selected target, output line count; output in details | Test covers a custom browser-like name and MCP-shaped name |
| Failure path | Shared across registered and future names | Short headline always visible, long diagnostic details collapsed | Long-failure, error-category and exit-code tests |
| Running path | Shared lifecycle from agent events | Progress label, tool, target, elapsed seconds, optional latest progress string | Existing lifecycle/REPL tests plus suite; no real subprocess matrix for all tools |

The current agent executes tool calls serially. The UI does not claim parallel tool grouping. The normal bash result is rendered after execution and includes exit information, but stdout/stderr is not incrementally streamed by the current tool execution pathway. Terminal persistent-session APIs provide their own start/status/log/input/stop lifecycle.

## 6. TUI Component and Interaction Audit

| Surface | Implementation | Audit result |
|---|---|---|
| Startup | `ui/repl/banner.py`, `ui/components/welcome.py`, `ui/components/logo.py` | Wordmark and product identity, model, provider, workspace and short prompt; responsive narrow-terminal fallback |
| Main prompt | `ui/repl/prompt.py`, `ui/repl/app.py` | prompt-toolkit composer with multiline support, persistent history, command completion and bottom status/approval readout |
| Keyboard | `ui/repl/keys.py`, REPL handlers | Ctrl+J newline, Ctrl+C interrupt/cancel, Ctrl+O output expansion, slash completion/picker, Shift+Tab approval policy; key regression tests pass |
| Assistant stream | `ui/stream.py`, `ui/components/markdown.py` | Markdown is fed incrementally and emitted in blocks; private reasoning events do not print |
| Tool progress | `ui/components/tool_progress.py`, `ui/components/spinner.py`, `ui/tui.py` | Single shared lifecycle spinner, compact progress, duration and state; animations obey config/reduced-motion flags |
| Tool result | `ui/components/tool_call.py`, diff/args/plan/memory components | Named renderer registry, compact summaries, expandable details, bounded output/diffs |
| Approval | confirmation, permission and terminal approval components | Shows target/action context and explicit choices; existing approval manager remains in execution path; waiting state is explicit |
| Usage/status | usage/status bar components and `ui/tui.py` | Token/context usage and session/model/approval readouts are represented where available |
| Slash commands | `ui/repl/commands/` | 20 names including aliases; dispatcher gives unknown-command guidance and `/help` lists usage |
| Click CLI | `main.py` | 7 top-level registered command names including `/help`: `/help`, `login`, `logout`, `run`, `sessions`, `setup`, `upgrade`; `sessions rm` is a nested command |
| Color/accessibility | `ui/theme/`, `ui/console.py`, prompt style | Semantic palette uses Acid Gold primary, green success, red error, amber warning, subtle blue info, muted neutrals; `NO_COLOR` disables Rich and prompt-toolkit styling |
| Terminal fallback | Rich Console and logo encoding check | Console lets Rich detect terminal support; startup wordmark uses ASCII fallback if encoding is not UTF; exact behavior was not exercised in CMD/PowerShell/Windows Terminal/macOS/Linux separately |

Slash command names at audit: `/checkpoint`, `/checkpoints`, `/clear`, `/config`, `/doctor`, `/exit`, `/help`, `/mcp`, `/model`, `/providers`, `/quit`, `/reasoning`, `/resume`, `/rewind`, `/save`, `/sessions`, `/setup`, `/stats`, `/thinking`, `/tools`. Aliases include `/exit`, `/quit`, `/reasoning`, and `/save`.

Current prompt shortcuts and status were reviewed. `/history`, `/status`, and `/compact` are not implemented; the brief listed these as examples, and they were not added because `/stats`, persistent history, and current usage/status readouts already cover nearby concepts without introducing duplicate commands.

## 7. Visual Design Changes

The interface keeps the KRYTH wordmark as the visual identity and uses a restrained semantic palette: Acid Gold `#E8FF3A` for brand/highlight, terminal-owned background, grayscale secondary text, muted blue for information, green for success, amber for warnings, and red for errors. The renderer system uses one common tool header/status structure, a renderer registry for explicit behavior, a fallback for future tools, and separate summary/detail blocks. It avoids dumping full tool payloads by default and makes file diffs expandable.

The startup contains the model, active provider and workspace. No mascot or pixel-art state system is included. No additional package was installed for this work.

## 8. Performance and Responsiveness

- Tool progress updates replace one line of state; animation is owned by the existing spinner controller rather than one thread per tool.
- Spinner is stopped at completion and in `stream_turn` cleanup; interactive approvals use transient Rich Live display.
- Animation respects UI config and `KRYTH_REDUCED_MOTION` / `KRYTH_NO_ANIMATION`.
- Tool result output and diff previews are truncated by model-token-oriented limits; expanded display is capped at 24 rendered lines.
- Live output is confined to the interactive flow; noninteractive CLI still uses the Click/Rich output pathway.
- TTY resize behavior is delegated to Rich and prompt-toolkit; resizing was not driven in an automated terminal emulator here.
- No benchmark or profiling run was performed, so CPU, memory, and redraw overhead are not presented as measured values.

## 9. Security and Reliability

The registry continues to validate schemas, run before/after hooks, and consult `ApprovalManager` before execution. Tool exceptions are logged, classified, and returned with recovery metadata. Successful mutations record file changes with the development supervisor. Subagent approvals are routed through the parent's confirmation callback. The UI improvements do not alter schemas, permissions, or tool execution behavior.

Approval renderers expose confirmation choices and terminal command context. Existing approval policies and session approvals remain in effect. This audit verified code paths and tests, but did not execute destructive commands or independently penetration-test approval handling. Error/cancellation cleanup paths were reviewed; exhaustive interruption at each tool's internal await point was not performed.

## 10. Test Results

Commands run from the repository root:

| Command | Result |
|---|---|
| `rtk pytest -q` | **201 passed, 0 failed, 1 skipped** |
| `rtk proxy python -m compileall -q agent tools ui config` | Passed |
| `rtk git diff --check` | Passed |
| `rtk proxy powershell -NoProfile -Command "python main.py --help"` | Passed; Click usage and commands printed |
| `rtk proxy powershell -NoProfile -Command "python main.py --version"` | Passed; `kryth, version 0.0.38` |
| `rtk proxy powershell -NoProfile -Command "python main.py run --help"` | Passed; one-shot/interactive usage printed |
| Dynamic registry inspection with `create_default_registry(Config())` | Passed; exactly 24 default tools listed |

The tool-render tests assert explicit renderer coverage for all core tools and provide success/failure-shaped outcomes for all core names, all 5 subagent names and an MCP-shaped custom name. They also cover compact output, file diff counts, expandable details, error categories/exit codes, and future-tool fallback. This is renderer-level coverage, not full execution testing of every tool against real files, processes, network services, credentials, or MCP servers.

### Coverage summary

| Area | Discovered | Audited | Improved | Tested |
|---|---:|---:|---:|---:|
| Default registered tools | 24 | 24 | 24 renderer routes retained/verified | 24 success/failure-shaped renderer cases; no claim of 24 end-to-end executions |
| Core tool named renderers | 19 | 19 | 19 | 19 mapping and render-shape checks |
| Default subagents | 5 | 5 | 5 | 5 role summaries and success/failure-shaped cases |
| Configured MCP servers | 0 | 0 | 0 | 0 live servers; generic MCP fallback shape tested |
| Slash command names | 20 | 20 | 0 new commands | Existing suite; help path inspected; not 20 isolated handler integrations |
| Click top-level command names | 7 | 7 | 0 | Help/version/run-help smoke checks; not every interactive/external command invoked |
| TUI/REPL components | 9 listed above | 9 | 3 direct improvements in this audit (provider welcome, approval waiting label, elapsed status/style) | Full unit suite and key/render tests; not all terminal emulators |
| Unsupported example integrations | 0 registered | 0 | 0 | 0; recorded as absent |

## 11. Remaining Issues and Untested Areas

1. No first-class browser automation, Python, Git, LSP, AST, vector-store, vision, desktop, package-manager, or test-runner tools are registered.
2. MCP behavior is dynamic, but there were no configured servers to exercise live discovery, invocation, and errors end to end.
3. Tool calls are serial. Parallel tool presentation is not implemented because the current agent execution loop is serial.
4. Bash output is summarized after completion; incremental stdout/stderr streaming is not implemented by the current bash execution pathway.
5. Persistent terminal lifecycle renderers have unit coverage, but were not each run against a live subprocess during this audit.
6. No structured machine-readable output mode was found. `NO_COLOR` is supported, but piped output and JSON mode were not validated as a separate CLI contract.
7. No actual Windows CMD, PowerShell, Windows Terminal, Linux, or macOS pseudo-terminal matrix was run. CLI smoke checks ran in PowerShell on Windows; other terminals remain unverified.
8. No automated terminal resize, paste stress, long-running network timeout, output flood, or resource benchmark was run.
9. The tool UI exposes running/completed/failed states, with cancellation handled at REPL/turn level. Dedicated partial-success, skipped, and retry status treatments are not currently part of the normal tool event contract.
10. Completion summaries are limited to verifiable streamed response, usage, tool output, and session information; there is no separate automatic final summary panel enumerating all files, commands, tests, and warnings.

## 12. Final Assessment

KRYTH has a coherent conversational CLI architecture with clear separation between agent execution, event streaming, reusable rendering, and prompt input. Its default tools are all accounted for in the UI, errors and long output have a compact path, approval checks stay in the execution layer, and the audited test suite passes. The corrected tool duration now reaches the user-facing header.

The result is **production-shaped for interactive use but not fully verified as production-ready across environments or integrations**. The evidence does not include end-to-end testing of all tool implementations, live MCP servers, other operating systems/terminal emulators, high-volume terminal workloads, or machine-readable output. Those are concrete validation gaps rather than presumed successes.
