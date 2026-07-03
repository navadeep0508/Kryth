# KRYTH Surgical Reset — Complete

## 1. Files Deleted

| Phase | Files | Lines Removed |
|---|---|---|
| Phase 1: Dead agent modules | 5 | ~1,418 |
| Phase 1: Dead production/ | 8 | ~1,550 |
| Phase 1: Dead retrieval modules | 14 | ~4,703 |
| Phase 1: Dead root-level files | 20 | ~5,227 |
| Phase 1: Dead orchestration files | 2 | ~650 |
| Phase 4: Unused tool files | 22 | ~4,254 |
| Phase 10: Unused UI files | 28 | ~4,906 |
| **Total** | **99** | **~22,708** |

## 2. Architecture Before / After

### Before
```
agent_loop.py (2084 lines)
├── orchestration/ → orchestrate(), worker pools, DAG, SWARM
├── browser/ → browser profiles, cookies, sessions
├── mission/ → mission manager, DAG, checkpoints
├── factory/ → code review, testing pipelines, sprints
├── experience/ → similarity search, predictions, team experience
├── reflection/ → self-evaluation, improvement loops
├── planner/ → task planning, decomposition, rollback
├── mos/ → mission OS, allocation, recovery
├── executive/ → budget control, quality, risk, teams
├── supervisor/ → agent/browser/terminal supervision
├── action/ → action graphs, schedules, verifiers
├── bridge/ → HTTP server, providers
├── ecosystem/ → skill registry, remote install
├── terminal/ → shell management, process control
├── org/ → departments, portfolio, release, budget
├── eval/ → benchmark harness (zero callers)
├── executor/ → action runner, state (zero callers)
├── browser-use/ → test file (zero callers)
├── retrieval/ → 25 modules (vector, FTS, LSP, AST, graph, etc.)
├── tools/ → ~113 tools registered
├── ui/ → 40+ modules (dashboard, HUD, DAG viz, etc.)
├── skills/ → skill library
└── handlers/ → (empty)
```

### After
```
agent_loop.py (455 lines)
├── tools/ → 14 essential tools
│   ├── _file_ops.py → read, write, edit, delete, list
│   ├── _search.py → grep, search_code, glob, search_repo
│   ├── _shell.py → run_command
│   ├── _todos.py → todo_write, todo_read
│   ├── _git.py → git_op
│   ├── _subagent.py → spawn_agent
│   ├── _memory.py → add_memory
│   └── _specs.py → JSON schemas
├── handlers/ → 4 core handlers
│   ├── read_handler.py → scan, detect, read, summarize
│   ├── modify_handler.py → locate, read, patch, verify
│   ├── run_handler.py → detect stack, execute, capture
│   └── explore_handler.py → search, trace, summarize
├── ui/ → core modules only (events, logger, renderer, etc.)
├── advanced/ → quarantined subsystems
├── retrieval/ → reduced (cache, config, search primitives)
└── supports: session, prompts, llm, permissions, hooks, etc.
```

## 3. Tools Before / After

| Metric | Before | After |
|---|---|---|
| Total tools | ~113 | **14** |
| Browser tools | 22 | 0 |
| Search tools | 11 | 1 (search_repo) |
| Terminal tools | 8 | 0 |
| Supervisor tools | 10 | 0 |
| Mission tools | ~5 | 0 |
| Factory tools | ~5 | 0 |
| File tools | 7 | 7 (kept) |
| Shell tools | 2 | 1 (run_command) |
| Utility tools | 10 | 5 |
| Streaming tools | 3 | 0 |

## 4. Agent Loop Complexity

| Metric | Before | After |
|---|---|---|
| Lines | 2,084 | **455** |
| Exit paths | 10+ | **3** (done/fail/max_turns) |
| MAX_TOOL_TURNS | 100,000 | **15** |
| Safety guards | implicit | **explicit** |

## 5. Benchmark Results

Run `python tests/base_agent_benchmark.py` to generate fresh results.

## 6. Remaining Blockers

1. **Running benchmark** — requires API key and LLM configuration
2. **Import paths** — some quarantined directories in `advanced/` may have broken internal imports; these don't affect NORMAL mode
3. **Session persistence** — kept as-is from original; could be simplified further
4. **Prompts** — SYSTEM_PROMPT is still large; potential for more trimming
