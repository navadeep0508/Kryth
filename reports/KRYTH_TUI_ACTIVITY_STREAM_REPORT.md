# KRYTH TUI Activity Stream Redesign

**Date:** 2026-11-16

## Summary

Replaced default boxed tool presentations with one-line, call-ID keyed activity entries. Running entries update in the existing Rich Live region and settle into one compact result. `Ctrl+O` expands the latest result while preserving bounded output and specialized detail renderers. Plan rendering uses one current snapshot. Approval-required operations share a compact keyboard selector. Turn completion shows only metrics observed from successful tool results.

Execution, tool schemas, permissions, approvals, integrations, model configuration, and prompt input behavior remain owned by their existing components.

## Before and after

Before, routine operations each occupied bordered panels:

```text
╭─ read ─────────────────────────╮
│ src/main.py                    │
╰────────────────────────────────╯
╭─ bash ─────────────────────────╮
│ pytest -q                      │
╰────────────────────────────────╯
```

After, the same turn reads as a compact stream:

```text
read   src/main.py                         2ms
run    pytest -q                          842ms
done   Tests passed 4/4
```

The approval surface is a single compact selector; command details can be expanded with `Ctrl+O`:

```text
KRYTH SECURITY
Permission required  Bash | MEDIUM
Remove generated cache directories?
> Allow once      Execute this operation one time
  Allow session  Remember for this session
  Reject         Block this operation
Ctrl+O command details   Enter confirm   Esc cancel
```

## Components and files

- `ui/panels/render.py`: compact borderless activity summary and bounded expanded detail rendering.
- `ui/panels/state.py`, `registry.py`, `orchestrator.py`, `events.py`: preserved panel state contract, renderer dispatch, event adaptation, deduplication, and bounded history.
- `ui/tui.py`: activity lifecycle, persistent plan snapshot, completion counters, shared approval selector, and terminal-width-aware rendering.
- `ui/components/terminal_approval.py`, `confirmation.py`: consistent approval UI, selector actions, details expansion, and risk/context descriptions. Existing permission behavior remains unchanged.
- `ui/components/plan.py`: plain persistent plan summary without bordered table output.
- `ui/components/spinner.py`: shared, low-frequency animation controller; no per-tool animation threads.
- `ui/stream.py`, `ui/repl/app.py`: event lifecycle, per-turn summary, plan reset, and status hint integration.
- `tests/test_adaptive_panels.py`, `tests/test_approval_ui.py`: compact stream, deduplication, plan state, detail expansion, actual file read/write, approval rendering and key decision behavior.
- `docs/cli.md`, `docs/technologies.md`, `ui/panels/PANEL_CONTRACT.md`: updated interface and architecture documentation.

The worktree also contains pre-existing changes from earlier KRYTH requests. This report covers the activity-stream-related work and does not claim those earlier changes are part of this task.

## Renderer coverage

The registry inspection for the adaptive panel framework found 24 default routes: 19 core tools and five default subagents. Named renderers cover file operations (`read`, `write`, `edit`, `apply_patch`, `list_directories`), search (`grep`, `glob`), terminal lifecycle/output, research (`search`, `fetch`), planning/memory, and all default subagents. Dynamic MCP/custom tool names use the generic renderer fallback. Renderer tests cover success and failure-shaped outcomes. Live integrations and every subprocess implementation were not all executed.

## Tests and checks

- Focused UI suites: **65 passed** (`test_adaptive_panels`, `test_approval_ui`, tool rendering, REPL keys, and brand tests).
- Full suite after the final changes: **221 passed, 1 skipped** (7.78s).
- Windows CMD: `python main.py --version` passed.
- Windows PowerShell: `python main.py run --help` passed.
- Windows CMD with `NO_COLOR=1`: `python main.py --help` passed.
- Narrow rendering and no-color behavior are covered in approval/activity renderer tests.

`git diff --check` passed after the final changes.

## Performance and compatibility

- Completed history remains bounded by the existing orchestrator capacity (20 calls); output and diff limits remain in force.
- Tool execution is serial in the current agent event model; no concurrent panel grouping was introduced.
- One shared spinner/Live controller is used. Approval Live refresh is limited to 4 Hz while waiting so terminal resize is reflected without per-keypress redraw loops.
- Rich performs terminal-width wrapping and live-region resize handling; stream rows are truncated to fit narrow widths.
- CMD, PowerShell, and `NO_COLOR` command-line smoke checks passed. Windows Terminal interactive use, macOS/Linux terminal emulators, and an automated terminal resize/scrollback test were not run.

## Limitations

- `Ctrl+O` expands the latest activity; there is no separate per-entry scroll viewport. Expanded content remains bounded and uses terminal scrollback.
- The agent currently executes tools serially and does not stream bash stdout/stderr incrementally.
- MCP/custom fallback rendering was simulated; no live configured MCP service was available.
- Approval interactions were validated through unit tests; an interactive CMD/PowerShell terminal session was not automated end-to-end.
- No CPU/memory benchmark was run.
