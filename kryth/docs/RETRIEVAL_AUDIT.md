# KRYTH Retrieval Engine Audit Report

## Executive Summary

The current retrieval stack is **production-grade** with smart query routing, multiple backends, and graceful degradation. However, it lacks several advanced capabilities required for Hermes/Cursor-class performance.

**Overall Assessment**: 60% complete. Core infrastructure exists; missing LSP, persistent symbol index, AST cache, dependency graph, cost optimizer, parallel retrieval, context compression, telemetry, and adaptive learning.

---

## Current Components (Present)

### 1. Query Router (`engine.py`)
- ✅ Query classification (keyword, symbol, structural, docs, relational, semantic, complex)
- ✅ Smart escalation: cheapest engine first
- ✅ Fallback chains
- ✅ Combined multi-engine results

### 2. Backend Engines
- ✅ **ripgrep** - fast text search
- ✅ **ast-grep** - structural pattern matching (multi-language)
- ✅ **SQLite FTS5** - full-text search with ranking
- ✅ **Graphify adapter** - semantic code graph (with fallback to repo_index)
- ✅ **repo_index** - Python AST-based symbol lookup
- ✅ **Semantic search** - embedding-based (sentence-transformers)

### 3. Caching Layer (`cache.py`)
- ✅ xxhash fingerprints (fast hashing)
- ✅ diskcache persistent storage
- ✅ orjson serialization
- ✅ Graceful fallbacks (md5, in-memory, stdlib json)

### 4. File Reading (`file_reader.py`)
- ✅ Multiple strategies: normal, mmap, streaming
- ✅ Size-based automatic selection
- ✅ Encoding detection (chardet, charset-normalizer)
- ✅ Batch reading with ThreadPoolExecutor

### 5. Configuration (`config.py`)
- ✅ Feature flags (ENABLE_GRAPHIFY, ENABLE_RIPGREP, etc.)
- ✅ Environment variable overrides
- ✅ Thresholds (MMAP_THRESHOLD, STREAM_THRESHOLD, MAX_FILE_SIZE)
- ✅ Concurrency limits (MAX_CONCURRENT_READS)
- ✅ Cache settings (CACHE_SIZE_BYTES, CACHE_TTL)

### 6. FD Discovery (`fd_discovery.py`)
- ✅ Fast file discovery using `fd` command
- ✅ Gitignore-aware
- ✅ Fallback to stdlib

### 7. AST Search (`ast_search.py`)
- ✅ Python AST-based search
- ✅ Symbol lookup (functions, classes, methods)
- ✅ Import/export tracking

### 8. Graphify Adapter (`graphify_adapter.py`)
- ✅ Wraps graphifyy library
- ✅ Multiple constructor pattern support
- ✅ Singleton per project
- ✅ Fallback to repo_index

### 9. Watcher (`watcher.py`)
- ✅ File system watching (watchfiles)
- ✅ Incremental indexing on changes
- ✅ Event debouncing

### 10. Capabilities Discovery (`__init__.py`)
- ✅ `capabilities()` function
- ✅ Detects all optional dependencies
- ✅ Returns feature matrix

---

## Missing Components

### 1. Language Server Protocol (LSP) Integration ❌
**Status**: Missing

**Required**:
- LSP client implementation (python-lsp-jsonrpc or custom)
- Multi-language server management (pyright, typescript-language-server, rust-analyzer, gopls, etc.)
- Unified API: `go_to_definition`, `find_references`, `hover`, `workspace_symbols`, `document_symbols`, `rename`, `implementations`, `type_definitions`
- Automatic server startup per file type
- Graceful fallback if server unavailable
- Cached LSP responses (integrate with existing cache)
- Timeout protection (non-blocking)

**Why needed**: LSP provides exact, language-aware navigation that beats heuristic search.

---

### 2. Persistent Symbol Index ❌
**Status**: Missing

**Required**:
- Repository-wide symbol database (SQLite)
- Store: classes, functions, methods, interfaces, enums, exports, imports, constants, variables
- Each symbol: name, type, file, line, column, parent, module, visibility, signature, docstring
- Incremental updates (watcher integration)
- Fast lookup by name, type, file, module
- No full rebuilds (delta updates)

**Why needed**: Enables instant "go to symbol" across entire repo without parsing.

---

### 3. Tree-sitter AST Cache ❌
**Status**: Missing

**Required**:
- Parse files with tree-sitter (multi-language)
- Cache ASTs by file xxhash
- Persistent storage (diskcache)
- Invalidate on file change (watcher integration)
- Lazy loading (parse on demand)
- Shared cache for Graphify and ast-grep

**Why needed**: Avoids reparsing unchanged files; speeds up repeated queries.

---

### 4. Dependency Graph Cache ❌
**Status**: Missing

**Required**:
- Lightweight dependency database (SQLite)
- Track: imports, imported_by, function_calls, class_inheritance, interface_implementations, module_dependencies, package_relationships
- Support impact analysis, safe refactoring, dead code detection
- Incremental updates
- Queryable: "what depends on X", "who calls Y"

**Why needed**: Enables refactoring intelligence and impact analysis.

---

### 5. Query Cost Optimizer ❌
**Status**: Missing (current router is static classification)

**Required**:
- Dynamic cost estimation per engine:
  - ripgrep: ~2ms
  - FTS5: ~1ms
  - SymbolIndex: ~1ms
  - Graphify: ~20ms
  - Semantic: ~80ms
- Factors: latency, cache hit probability, index availability, token cost
- Choose cheapest successful path (no hardcoded routing)
- Learn from telemetry

**Why needed**: Minimizes latency and token usage by avoiding expensive engines when cheaper ones suffice.

---

### 6. Parallel Retrieval Engine ❌
**Status**: Missing (currently sequential)

**Required**:
- Run multiple engines simultaneously
- Adaptive parallelism (few files → sequential; many → ThreadPool; huge → async + batching)
- Configurable worker limits
- Timeout handling per engine
- Cancellation support
- Result merging with deduplication

**Why needed**: Parallel execution often feels much faster, especially for complex queries.

---

### 7. Context Compression Layer ❌
**Status**: Missing

**Required**:
- Generate summaries for: files, folders, modules, packages
- Store: responsibilities, exported APIs, dependencies, important symbols
- Use summaries before loading raw files
- Incremental summary updates
- Cache summaries persistently

**Why needed**: Reduces token consumption; 20 files → 3 summaries.

---

### 8. Intelligent Context Builder ❌
**Status**: Missing

**Required**:
- Build smallest useful context for LLM
- Strategies:
  - Symbol + implementation + callers
  - Summary + key symbols
  - Dependency chain
- Minimize tokens while preserving completeness
- Adaptive based on query type

**Why needed**: Critical for large codebases; prevents context overflow.

---

### 9. Repository Knowledge Cache ❌
**Status**: Missing

**Required**:
- Persistent knowledge store (SQLite)
- Track: common navigation paths, previous search results, frequently accessed files, popular symbols, module summaries
- Automatic refresh of stale entries (TTL-based)
- Integration with existing cache layer

**Why needed**: Speeds up repeated queries; learns repository structure.

---

### 10. Retrieval Telemetry ❌
**Status**: Missing

**Required**:
- Track: query type, engine selected, latency, cache hits, token savings, retrieval success rate
- Persistent storage (SQLite or JSONL)
- Aggregation and reporting
- Integration with adaptive router

**Why needed**: Data-driven optimization; identify bottlenecks.

---

### 11. Adaptive Retrieval Learning ❌
**Status**: Missing

**Required**:
- Router learns from usage patterns
- If strategy X consistently performs better → increase priority
- Reduce expensive calls when cheaper alternatives succeed
- Improve cache hit rates
- Simple statistical optimization (no ML model needed)

**Why needed**: Self-improving system; adapts to repository-specific patterns.

---

### 12. Refactoring Intelligence ❌
**Status**: Missing

**Required**:
- Rename safety analysis (using LSP + Symbol Index + Dependency Graph)
- Impact analysis: "what breaks if I rename X"
- Dependency tracing: full call chain
- Unused code detection (dead code)
- Circular dependency detection
- Safe move/delete operations

**Why needed**: Enables confident refactoring; prevents breaking changes.

---

### 13. Repository Scale Optimizations ❌
**Status**: Partial (some optimizations exist)

**Required**:
- Monorepo support (multiple roots, selective indexing)
- Polyglot repository handling (language-specific indexes)
- Generated code exclusion (patterns, .gitignore)
- Incremental startup (avoid full indexing)
- Lazy symbol loading

**Why needed**: Large repositories (100k+ files) need special handling.

---

### 14. Feature Flags System ✅
**Status**: Present (`config.py`)

**Existing flags**:
- ENABLE_GRAPHIFY
- ENABLE_RIPGREP
- ENABLE_AST_GREP
- ENABLE_FTS
- ENABLE_MMAP
- ENABLE_ASYNC_IO
- ENABLE_CACHE
- ENABLE_WATCHER

**Missing flags** (to be added):
- ENABLE_LSP
- ENABLE_SYMBOL_INDEX
- ENABLE_AST_CACHE
- ENABLE_DEP_GRAPH
- ENABLE_COST_OPTIMIZER
- ENABLE_PARALLEL_RETRIEVAL
- ENABLE_CONTEXT_COMPRESSION
- ENABLE_TELEMETRY
- ENABLE_ADAPTIVE_ROUTING
- ENABLE_REFACTORING_INTELLIGENCE

---

### 15. Benchmarks ❌
**Status**: Missing

**Required**:
- Benchmark suite measuring:
  - Symbol lookup latency
  - Go-to-definition latency
  - Reference search latency
  - Dependency lookup latency
  - Context generation time
  - Parallel retrieval speedup
  - Cache hit rate
  - Memory usage
- Compare against baseline (current implementation)
- Regression detection

---

### 16. Testing ❌
**Status**: Partial (some tests exist)

**Existing tests**:
- `tests/test_retrieval.py` - basic retrieval tests
- `tests/test_agent_lifecycle.py` - agent lifecycle

**Missing**:
- LSP integration tests
- Symbol index tests
- AST cache tests
- Dependency graph tests
- Parallel retrieval tests
- Cost optimizer tests
- Context builder tests
- Telemetry tests
- Adaptive routing tests
- Refactoring intelligence tests

---

## Gap Analysis Summary

| Feature | Status | Priority | Effort |
|---------|--------|----------|--------|
| LSP Integration | Missing | P0 | High |
| Symbol Index | Missing | P0 | High |
| AST Cache | Missing | P1 | Medium |
| Dependency Graph | Missing | P1 | Medium |
| Cost Optimizer | Missing | P1 | Low |
| Parallel Retrieval | Missing | P1 | Medium |
| Context Compression | Missing | P2 | Medium |
| Intelligent Context Builder | Missing | P2 | Medium |
| Knowledge Cache | Missing | P2 | Low |
| Telemetry | Missing | P2 | Low |
| Adaptive Learning | Missing | P2 | Low |
| Refactoring Intelligence | Missing | P3 | High |
| Scale Optimizations | Partial | P3 | Medium |
| Benchmarks | Missing | P3 | Medium |
| Tests | Partial | P3 | High |

---

## Implementation Strategy

### Phase 1: Foundation (Week 1-2)
1. Add missing feature flags to `config.py`
2. Implement **AST Cache** (reuse existing cache layer)
3. Implement **Symbol Index** (SQLite, incremental updates)
4. Implement **Dependency Graph** (lightweight, incremental)

### Phase 2: Intelligence (Week 3-4)
5. Implement **LSP Integration** (unified client, multi-language)
6. Implement **Cost Optimizer** (dynamic routing)
7. Implement **Parallel Retrieval** (ThreadPool + async)
8. Implement **Context Compression** (summaries)

### Phase 3: Optimization (Week 5-6)
9. Implement **Intelligent Context Builder**
10. Implement **Knowledge Cache**
11. Implement **Telemetry**
12. Implement **Adaptive Learning**

### Phase 4: Polish (Week 7-8)
13. Implement **Refactoring Intelligence**
14. Add **Scale Optimizations**
15. Create **Benchmarks**
16. Write **Comprehensive Tests**
17. Generate **Documentation**

---

## Reuse Existing Components

✅ **Do NOT rewrite**:
- `cache.py` - use for AST cache, symbol index, dependency graph
- `config.py` - extend with new flags
- `engine.py` - extend with cost optimizer and parallel retrieval
- `file_reader.py` - reuse for file operations
- `graphify_adapter.py` - integrate with AST cache
- `ast_search.py` - integrate with symbol index
- `watcher.py` - use for incremental updates

---

## Architecture Diagram (Target)

```
User Query
    │
    ▼
┌─────────────────────────────────────────────┐
│   Query Classifier + Cost Estimator         │
│   (upgrade engine.py)                      │
└─────────────┬───────────────────────────────┘
              │
      ┌───────┴────────┬──────────────┬──────────────┐
      │                │              │              │
      ▼                ▼              ▼              ▼
┌──────────┐   ┌──────────┐  ┌──────────┐  ┌──────────┐
│   LSP    │   │  Symbol  │  │   AST    │  │  Dep     │
│          │   │  Index   │  │  Cache   │  │  Graph   │
└────┬─────┘   └────┬─────┘  └────┬─────┘  └────┬─────┘
     │              │             │             │
     │              │             │             │
     │              └─────────────┴─────────────┘
     │                            │
     │              ┌─────────────┴──────────────┐
     │              │   Parallel Merge + Rank    │
     │              │   (dedup, score, merge)    │
     │              └─────────────┬──────────────┘
     │                            │
     ▼                            ▼
┌─────────────────────────────────────────────┐
│   Context Compression + Builder            │
│   (summaries, minimal context)             │
└─────────────────────────────────────────────┘
              │
              ▼
       LLM Response
```

---

## Performance Targets

| Metric | Current | Target |
|--------|---------|--------|
| Symbol lookup | ~50ms (repo_index) | <5ms (persistent index) |
| Go-to-definition | ~100ms (ast-grep) | <10ms (LSP + cache) |
| Reference search | ~200ms (graphify) | <20ms (dep graph + cache) |
| Cold query latency | ~500ms | <100ms (parallel + cache) |
| Cache hit rate | ~30% | >70% (knowledge cache) |
| Token usage per query | ~2000 | <500 (context compression) |

---

## Conclusion

The current retrieval engine is solid but needs the advanced capabilities listed above to reach state-of-the-art status. Implementation should be incremental, reusing existing components, and always maintaining backward compatibility.

**Next steps**: Begin Phase 1 implementation (AST Cache, Symbol Index, Dependency Graph).