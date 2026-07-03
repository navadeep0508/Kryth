# Phase 1 — Deleted Files Report

## Summary

| Category | Files | Lines Removed |
|---|---|---|
| Dead agent modules | 5 | ~1418 |
| Dead retrieval modules | 14 | ~4703 |
| Dead root-level files | 20 | ~5227 |
| Dead orchestration files | 2 | ~650 |
| **Total** | **41** | **~12,000** |

---

## Dead Agent Modules

| File | Lines | Reason |
|---|---|---|
| `agent/task_classifier.py` | ~547 | Zero imports; only a commented-out reference in agent_loop.py |
| `agent/self_eval.py` | ~171 | Zero imports; only self-docstring reference |
| `agent/_memory.py` | ~100 | Duplicate of tools/_memory.py; zero imports |
| `agent/tools/registry.py` | ~600 | Zero imports; unused dynamic registry |
| `agent/benchmark.py` | ~400 | Dead benchmark script; zero external callers |

## Dead Production Subsystems

| File | Lines | Reason |
|---|---|---|
| `agent/production/__init__.py` | ~18 | Zero imports from outside production/ |
| `agent/production/adaptive.py` | ~112 | Zero external callers |
| `agent/production/context_shard.py` | ~197 | Zero external callers |
| `agent/production/execution_profiles.py` | ~130 | Only called by orchestration (quarantined) |
| `agent/production/readiness.py` | ~199 | Zero external callers |
| `agent/production/reliability.py` | ~315 | Only called by orchestration (quarantined) |
| `agent/production/reputation.py` | ~211 | Zero external callers |
| `agent/production/telemetry.py` | ~168 | Zero external callers |

## Dead Retrieval Modules

All files below had zero external callers outside the retrieval directory itself:

| File | Lines | Reason |
|---|---|---|
| `retrieval/__init__.py` | ~200 | Capabilities loader; not imported externally |
| `retrieval/adaptive_router.py` | ~236 | Zero external callers |
| `retrieval/context_builder.py` | ~332 | Zero external callers |
| `retrieval/context_compression.py` | ~415 | Zero external callers |
| `retrieval/cost_optimizer.py` | ~246 | Zero external callers |
| `retrieval/dep_graph.py` | ~389 | Zero external callers |
| `retrieval/knowledge_cache.py` | ~387 | Zero external callers |
| `retrieval/lsp_client.py` | ~648 | Zero external callers |
| `retrieval/parallel_retriever.py` | ~325 | Zero external callers |
| `retrieval/refactor_intelligence.py` | ~429 | Zero external callers |
| `retrieval/scale_optimizer.py` | ~268 | Zero external callers |
| `retrieval/telemetry.py` | ~386 | Zero external callers |
| `retrieval/vector_store.py` | ~412 | Zero external callers |
| `retrieval/watcher.py` | ~230 | Zero external callers |

REMOVED from retrieval (kept): ast_cache, ast_search, cache, config, engine, fd_discovery, file_reader, fts_index, graphify_adapter, semantic_index, symbol_index

## Dead Root-Level Files

| File | Lines | Reason |
|---|---|---|
| `agent/_checkpoint.py` | 43 | Zero imports |
| `agent/_common.py` | 44 | Zero imports |
| `agent/_critique.py` | 114 | Zero imports |
| `agent/_debug.py` | 55 | Zero imports |
| `agent/_file_ops.py` | 563 | Zero imports (duplicate of tools/_file_ops.py) |
| `agent/_git.py` | 293 | Zero imports |
| `agent/_plan.py` | 22 | Zero imports |
| `agent/_project_runner.py` | 212 | Zero imports |
| `agent/_results.py` | 88 | Zero imports |
| `agent/_search.py` | 273 | Zero imports (duplicate of tools/_search.py) |
| `agent/_shell.py` | 231 | Zero imports |
| `agent/_specs.py` | 833 | Zero imports |
| `agent/_task_graph.py` | 212 | Zero imports |
| `agent/_todos.py` | 46 | Zero imports |
| `agent/_verify.py` | 26 | Zero imports |
| `agent/debug_cycle.py` | 268 | Zero external callers |
| `agent/workflow_templates.py` | 313 | Zero external callers |
| `agent/vision.py` | 328 | Zero imports |
| `agent/retriever.py` | 229 | Zero imports |
| `agent/parallel_builder.py` | 1034 | Zero imports |

## Dead Orchestration Files

| File | Lines | Reason |
|---|---|---|
| `orchestration/v6_validate.py` | 396 | Only docstring reference |
| `orchestration/work_partitioner.py` | 254 | Only imported by team_scaler.py (internal to orchestration/) |
