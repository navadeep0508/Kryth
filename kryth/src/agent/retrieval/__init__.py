"""KRYTH retrieval engine — production-grade code intelligence layer.

Architecture (upgraded):
                    User Query
                         │
                         ▼
              Query Classifier + Cost Optimizer
                         │
      ┌──────────────────┼──────────────────┐
      │                  │                  │
      ▼                  ▼                  ▼
   LSP Layer      Symbol Index        AST Cache
      │                  │                  │
      └────────────┬─────┴────────────┬────┘
                   ▼                  ▼
              Parallel Retrieval (ripgrep, FTS, Graphify, Semantic)
                   │                  │
                   └──────────┬───────┘
                              ▼
                    Context Compression + Builder
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
     File Reader          Dependency Graph    Knowledge Cache
          │                   │                   │
          └───────────────────┼───────────────────┘
                              ▼
                       Cache Layer
                    (xxhash + diskcache)

All components degrade gracefully when optional dependencies are absent.
"""
from __future__ import annotations

# Lazy-safe imports — each submodule handles its own optional deps
from agent.retrieval import config  # noqa: F401 (re-exported)


def capabilities() -> dict:
    """Return a dict summarising all available retrieval capabilities."""
    import shutil

    result: dict = {
        # Core search engines
        "ripgrep": shutil.which("rg") is not None,
        "fd": False,
        "ast_grep": False,
        "graphify": False,
        "fts": config.ENABLE_FTS,
        "mmap": config.ENABLE_MMAP,
        "watcher_enabled": config.ENABLE_WATCHER,
        "watcher_available": False,
        "xxhash": False,
        "diskcache": False,
        "orjson": False,
        "chardet": False,
        "charset_normalizer": False,
        # Advanced features
        "lsp": config.ENABLE_LSP,
        "symbol_index": config.ENABLE_SYMBOL_INDEX,
        "ast_cache": config.ENABLE_AST_CACHE,
        "dep_graph": config.ENABLE_DEP_GRAPH,
        "cost_optimizer": config.ENABLE_COST_OPTIMIZER,
        "parallel_retrieval": config.ENABLE_PARALLEL_RETRIEVAL,
        "context_compression": config.ENABLE_CONTEXT_COMPRESSION,
        "telemetry": config.ENABLE_TELEMETRY,
        "adaptive_routing": config.ENABLE_ADAPTIVE_ROUTING,
        "refactoring_intelligence": config.ENABLE_REFACTORING_INTELLIGENCE,
        "knowledge_cache": config.ENABLE_KNOWLEDGE_CACHE,
    }

    try:
        from agent.retrieval.fd_discovery import fd_available
        result["fd"] = fd_available()
    except Exception:
        pass

    try:
        from agent.retrieval.ast_search import available as _aa
        result["ast_grep"] = _aa()
    except Exception:
        pass

    try:
        from agent.retrieval.graphify_adapter import available as _ga
        result["graphify"] = _ga()
    except Exception:
        pass

    try:
        from agent.retrieval.watcher import available as _wa
        result["watcher_available"] = _wa()
    except Exception:
        pass

    try:
        from agent.retrieval.cache import capabilities as _cc
        result.update(_cc())
    except Exception:
        pass

    try:
        from agent.retrieval.file_reader import capabilities as _fc
        result.update(_fc())
    except Exception:
        pass

    try:
        from agent.retrieval.ast_cache import capabilities as _ac
        result.update(_ac())
    except Exception:
        pass

    try:
        from agent.retrieval.symbol_index import capabilities as _si
        result.update(_si())
    except Exception:
        pass

    try:
        from agent.retrieval.dep_graph import capabilities as _dg
        result.update(_dg())
    except Exception:
        pass

    try:
        from agent.retrieval.lsp_client import capabilities as _lc
        result.update(_lc())
    except Exception:
        pass

    try:
        from agent.retrieval.cost_optimizer import capabilities as _co
        result.update(_co())
    except Exception:
        pass

    try:
        from agent.retrieval.parallel_retriever import capabilities as _pr
        result.update(_pr())
    except Exception:
        pass

    try:
        from agent.retrieval.context_compression import capabilities as _ccp
        result.update(_ccp())
    except Exception:
        pass

    try:
        from agent.retrieval.context_builder import capabilities as _cb
        result.update(_cb())
    except Exception:
        pass

    try:
        from agent.retrieval.knowledge_cache import capabilities as _kc
        result.update(_kc())
    except Exception:
        pass

    try:
        from agent.retrieval.telemetry import capabilities as _tc
        result.update(_tc())
    except Exception:
        pass

    try:
        from agent.retrieval.adaptive_router import capabilities as _ar
        result.update(_ar())
    except Exception:
        pass

    try:
        from agent.retrieval.refactor_intelligence import capabilities as _ri
        result.update(_ri())
    except Exception:
        pass

    return result


__all__ = [
    "config",
    "capabilities",
]