# Adaptive inline panel contract

## Identity and state

- Every panel is keyed by the agent's `call_id`; one call ID maps to one state for its entire lifecycle.
- States progress from `running` to exactly one of `completed`, `failed`, or `cancelled`.
- Duplicate start events do not create a second panel. Duplicate terminal events do not print a second result.
- Progress only updates the currently active call because the current agent/tool execution path is serial and its progress callback has no call ID.
- The panel store is bounded. Completed panels can be revisited through the existing Ctrl+O expansion behavior.

## Rendering boundary

- `PanelRendererRegistry` maps each registered core tool name to the existing specialized summary/detail renderer.
- Default subagents share a role-aware renderer. Runtime MCP/custom tools use the generic fallback.
- Renderers receive `ToolOutcome` and return summary and detail renderables; they never execute tools or read global TUI state.
- Compact mode renders one aligned, borderless activity row with a short target, status, and duration. Expanded mode adds bounded details (arguments, code, diffs, terminal output, diagnostics).
- The same `PanelState` is rendered while running and after completion. A single transient Rich Live region is used when the terminal supports it; non-terminal output prints only the completed activity row.
- Plan updates are rendered from one current snapshot in the prompt workspace rather than appended as separate tool rows. The footer reports shortcuts and runtime state without repeating the plan summary.

## Event adapter

- The adapter consumes existing `AgentEvent` instances and maps tool start/completion to the orchestrator.
- The progress callback updates the active panel's latest progress label.
- Approval remains owned by the existing confirmation callback and approval manager; it is not inferred from tool text.
- The adapter is a presentation layer only and cannot alter schemas, tool ordering, execution, cancellation, or approval decisions.

## Layout and resource limits

- Activity rows use semantic status labels and theme colors without rectangular borders. Approval prompts are the only persistent interactive selector.
- Existing output and diff token limits remain authoritative; terminal-width wrapping is delegated to Rich.
- The existing spinner controller remains the only animation task. No per-panel threads or animation tasks are created.
- Ctrl+O remains the keyboard interaction for expanding the most recent panel; prompt-toolkit remains the persistent input owner.
