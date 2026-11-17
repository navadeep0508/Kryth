# KRYTH Execution Reliability v2 Report

## Root-cause findings

- Request preflight estimated messages and tools before all provider options were added. That could undercount the serialized request and did not explicitly retain a reserve for output tokens.
- The estimator used a byte heuristic even when a configured provider tokenizer was available. Context budget checks existed, but the model/provider estimate and budget telemetry were incomplete.
- A rejected request carried budget details on the low-level error event but the agent did not retain them in session history. Resetting the session also left stale context-budget telemetry.
- Browser tool success was not consistently distinct from verified page state. Clicks could return success without observing navigation, popup creation, or a meaningful state change. Navigation and recovery paths needed evidence and duplicate-action protection.
- Multi-step plans were held in the tool instance and could be lost across turns. Completion could be reported without verified evidence for each planned step.
- Workspace detection is bounded to the configured workspace and identifies KRYTH source markers there; it does not assume that an arbitrary active project is the KRYTH checkout.

## Architecture changes

- Added `ExecutionStatus` and verification evidence to `ToolResult`, preserving existing tool schemas and execution flow. Verified states require explicit successful evidence.
- Added provider-aware serialized request estimation. The estimator counts the fully assembled API payload, including tool schemas, request options, and `max_tokens`; it reserves output capacity and blocks preflight overflow before constructing the API client.
- Added bounded context-budget telemetry for admitted and rejected requests, including estimator, serialized bytes, input budget, context window, and output reserve. Rejections now reach session history through the agent error path.
- Kept full tool output persisted while sending bounded summaries/references into conversation context.
- Added browser action verification for navigation URL/title/content/readiness, popup tabs, tab state, input values, and observed DOM changes. Failure handling observes current state and reconnects on connection errors but does not blindly replay the action.
- Added bounded call-ID deduplication for browser invocations.
- Persisted task IDs, phases, steps, completion/failure state, verification evidence, and recovery notes through `DevelopmentSupervisor`. Plan state restores across tool resets; completion requires evidence for all steps.
- Fixed three undefined-name lint findings encountered during validation (`Any`, `Console`, and `LongRunningInfo`).

## Files changed for this work

- `agent/agent.py`, `agent/events.py`, `agent/session.py`
- `client/llm_client.py`, `client/response.py`
- `config/config.py`, `config/providers.py`, `config/setup_wizard.py`
- `context/manager.py`, `context/project_context.py`
- `tools/base.py`, `tools/registry.py`, `tools/core/plan.py`, `tools/core/bash.py`
- `tools/browser_plugin.py`, `tools/kuba_browser_engine.py`
- `utils/development_supervisor.py`, `utils/text.py`
- `ui/stream.py`
- `tests/test_context_budget.py`, `tests/test_development_supervisor.py`, `tests/test_plan_checkpoint.py`, `tests/test_browser_plugin.py`, `tests/test_kuba_browser_engine.py`

The workspace contains other pre-existing/unrelated changes; they were not reverted or included in this reliability scope.

## Tests added or expanded

- Context preflight: provider tokenizer selection, conservative fallback, output reserve, full serialized payload, overflow rejection, and telemetry.
- Tool-output persistence and truncation references; context compression during long tool loops.
- Active workspace/KRYTH-source detection boundaries.
- Browser navigation failure and redirect verification, click with no observed state change, popup tab verification, reconnect handling, duplicate call IDs, and delayed DOM update behavior.
- Task checkpoint persistence, incomplete-task recovery, plan restoration, and verified-only completion.

## Actual validation results

- Focused reliability tests: **56 passed**.
- Full KRYTH source-checkout suite (`PYTHONPATH` set to this checkout, `pytest tests`): **291 passed, 1 skipped**.
- Critical-rule Ruff check (`E9,F63,F7,F82`): **passed**.
- Python compile check (`compileall` on agent/client/config/context/tools/ui/utils): **passed**.
- A repository-root `pytest -q` attempt also collected the nested browser package tests and failed collection because its standalone `mcp_server` import could not resolve under the root test path. The canonical KRYTH suite was then run from `tests/` and passed.
- The browser subproject venv has no pytest installed. Running its standalone MCP test under the global interpreter also fails collection because the installed MCP package does not export the `MCPServer` symbol required by that subproject. Those environment-specific browser-subproject tests are not reported as passing.

## Remaining reliability risks and limitations

- Browser verification is bounded to what the engine exposes (URL, title, visible text, tab list, selector inspection, and page signature). It cannot prove semantic success for every site-specific workflow without task-specific expected-state assertions.
- Reconnection is attempted for observation after connection failures; action replay is deliberately avoided to prevent duplicate side effects. Recovery from an ambiguous action remains a decision for the agent after inspecting state.
- Provider tokenizer accuracy depends on a configured/recognized tokenizer. Unknown model names use a conservative serialized UTF-8 byte upper bound, which can reject some requests early.
- The standalone browser package suite remains unverified in its own dependency environment for the reasons listed above.
- No live browser task was executed during this engineering pass; browser behavior was verified with focused fake-engine and lifecycle tests.
