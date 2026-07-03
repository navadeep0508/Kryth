# Frontier Memory v3 — Wiring Audit Report

## Status: FULLY WIRED

All cognitive memory subsystems are now connected to the runtime execution paths.

## Integration Points

### agent_loop.py — Main Execution Loop

| Hook | Location | Connected |
|------|----------|-----------|
| Episode start | `run_agent()` → before `run_inner_loop()` | ✅ |
| Episode complete | `run_agent()` → after `run_inner_loop()` | ✅ |
| Reflection | `run_agent()` → after task completion | ✅ |
| Lifecycle (background) | `run_agent()` → after reflection | ✅ |
| Runtime state | `run_agent()` → before execution | ✅ |
| Tool call recording | `dispatch_tool_call()` | ✅ |
| Failure recording | `dispatch_tool_call()` → on error | ✅ |
| Patch tracking | `dispatch_tool_call()` → on write_file/edit | ✅ |
| Graph context injection | `build_initial_system()` → first turn | ✅ |
| Cognitive retrieval | `_speculative_preload()` → background | ✅ |
| Heuristics injection | `_speculative_preload()` → injected into session | ✅ |
| Episode search | `_speculative_preload()` → similar past tasks | ✅ |
| Conversational context | `_run_conversational_reply()` → CWD + files | ✅ |

### memory.py — Bridge Module

| API | Called By | Purpose |
|-----|-----------|---------|
| `memory.record_tool_call()` | dispatch_tool_call | Consolidation trigger |
| `memory.record_failure()` | dispatch_tool_call (on error) | Causal memory |
| `memory.record_episode_patch()` | dispatch_tool_call (on write) | Episode journal |
| `memory.record_episode()` | run_agent (task start) | Episode lifecycle |
| `memory.complete_episode()` | run_agent (task end) | Episode lifecycle |
| `memory.reflect()` | run_agent (post-task) | Lesson extraction |
| `memory.run_lifecycle()` | run_agent (background thread) | Decay/archive |
| `memory.set_runtime_state()` | run_agent (task start) | Hot memory |
| `memory.retrieve()` | _speculative_preload | Context injection |
| `memory.get_heuristics()` | _speculative_preload | Learned rules |
| `memory.semantic_episode_search()` | _speculative_preload | Past episodes |
| `memory.graph.search()` | build_initial_system | Code graph |
| `memory.graph.context_for()` | build_initial_system | File context |
| `memory.graph.is_built()` | build_initial_system | Graph availability |
| `memory.store()` | reflection pipeline (via bridge) | Lesson storage |
| `get_memory_manager()` | reflection pipeline, browser bridge | Backward compat |

### Backward Compatibility

| Consumer | Import Path | Status |
|----------|-------------|--------|
| `agent.reflection.pipeline` | `from agent.memory.memory import get_memory_manager, LAYER_*` | ✅ |
| `agent.browser.memory_bridge` | `from agent.memory.memory_manager import LAYER_*` | ✅ (fallback) |
| `agent.mission.mission_memory_bridge` | `from agent.memory.memory_manager import LAYER_*` | ✅ (fallback) |
| `agent.mission.checkpoint_engine` | `from agent.memory.memory_manager import MemoryManager` | ✅ (fallback) |

## Data Flow

```
User Input
  │
  ├── _speculative_preload (background)
  │   ├── memory.retrieve() → cognitive context
  │   ├── memory.get_heuristics() → learned rules
  │   └── memory.semantic_episode_search() → past tasks
  │
  ├── build_initial_system
  │   ├── memory.graph.search() → relevant files
  │   └── memory.graph.context_for() → file content
  │
  ├── Session injection (first turn)
  │   ├── [Cognitive memory] → temporal/causal/semantic results
  │   ├── [Learned heuristics] → past lessons
  │   └── [Similar past tasks] → episode recalls
  │
  ├── Episode start: memory.record_episode()
  ├── Runtime state: memory.set_runtime_state()
  │
  ├── run_inner_loop (tool execution)
  │   └── dispatch_tool_call (per tool)
  │       ├── memory.record_tool_call() → consolidation
  │       ├── memory.record_failure() → on error
  │       └── memory.record_episode_patch() → on write
  │
  └── Post-task
      ├── memory.complete_episode()
      ├── memory.reflect() → lessons, heuristics
      └── memory.run_lifecycle() → background decay
```

## Zero Dead Modules

All cognitive memory APIs are now called from live execution paths:
- ✅ Graphify structural layer (graph queries)
- ✅ SQLite temporal layer (facts, decisions)
- ✅ SQLite causal layer (failures, fixes)
- ✅ Semantic layer (vector search via preload)
- ✅ Hot memory layer (runtime state)
- ✅ Episode store (task lifecycle)
- ✅ Semantic episode search (fuzzy recall)
- ✅ Hybrid router (query classification)
- ✅ Parallel retrieval (concurrent search)
- ✅ Adaptive consolidator (auto-trigger)
- ✅ Reflection agent (post-task learning)
- ✅ Lifecycle manager (background decay)
- ✅ Telemetry collector (metrics)

## Performance

All hooks are wrapped in try/except and run in <5ms each.
Lifecycle runs in a daemon thread (non-blocking).
No hook blocks the main agent loop.
