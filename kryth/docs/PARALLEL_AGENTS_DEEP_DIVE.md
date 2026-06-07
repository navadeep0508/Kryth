# Parallel Agents in KRYTH - Complete Deep Dive

## Table of Contents
1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Core Components](#core-components)
4. [Execution Flow](#execution-flow)
5. [Parallelism Strategy](#parallelism-strategy)
6. [Threading Model](#threading-model)
7. [Result Merging](#result-merging)
8. [Error Handling](#error-handling)
9. [Timeout Management](#timeout-management)
10. [Cache Integration](#cache-integration)
11. [Telemetry](#telemetry)
12. [Adaptive Routing](#adaptive-routing)
13. [Configuration](#configuration)
14. [Performance Characteristics](#performance-characteristics)
15. [Code Walkthrough](#code-walkthrough)
16. [Integration Points](#integration-points)
17. [Limitations](#limitations)
18. [Future Improvements](#future-improvements)

---

## 1. Overview

The **Parallel Retriever** is a sophisticated multi-engine search system that executes multiple retrieval strategies simultaneously and merges results. It's designed to:

- **Reduce latency**: Run independent searches in parallel instead of sequentially
- **Improve recall**: Query multiple engines to get diverse results
- **Optimize costs**: Use cheaper engines first, expensive ones only if needed
- **Adapt intelligently**: Learn which engines work best for which query types

**Key Innovation**: Unlike traditional sequential search (engine A → engine B → engine C), parallel retrieval launches all eligible engines at once and returns the best merged results.

---

## 2. Architecture

```
User Query
    │
    ▼
┌─────────────────────────────────────────────┐
│         Query Classifier                    │
│   (engine.py - classifies query type)      │
└─────────────┬───────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────┐
│         Cost Optimizer                      │
│   (cost_optimizer.py - ranks engines)      │
└─────────────┬───────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────┐
│         Adaptive Router (optional)          │
│   (adaptive_router.py - learned patterns)  │
└─────────────┬───────────────────────────────┘
              │
              ▼
      ┌───────┴───────┐
      │               │
      ▼               ▼
┌──────────┐   ┌──────────────┐
│  Engine  │   │   Engine     │  (multiple engines in parallel)
│  A       │   │   B          │
└────┬─────┘   └──────┬───────┘
     │                │
     └────────┬───────┘
              │
              ▼
┌─────────────────────────────────────────────┐
│         Parallel Retriever                  │
│   (parallel_retriever.py)                  │
│   - ThreadPoolExecutor                     │
│   - Timeout handling                       │
│   - Result deduplication                   │
│   - Score merging                          │
└─────────────┬───────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────┐
│         Context Builder                     │
│   (context_builder.py)                     │
│   - Token budgeting                        │
│   - Strategy selection                     │
└─────────────┬───────────────────────────────┘
              │
              ▼
          LLM Response
```

---

## 3. Core Components

### 3.1 `parallel_retriever.py` (Main Module)

**Purpose**: Execute multiple retrieval engines in parallel and merge results.

**Key Classes**:

#### `RetrievalResult`
```python
@dataclass
class RetrievalResult:
    engine: str              # Which engine produced this
    content: str             # The actual result text
    score: float             # Quality score (0-1)
    metadata: Dict[str, Any] # Additional data (path, line, etc.)
    latency_ms: float        # How long it took
```

#### `MergedResult`
```python
@dataclass
class MergedResult:
    content: str             # Deduplicated result content
    engines: List[str]       # Which engines found this (consensus)
    max_score: float         # Best score among engines
    total_latency: float     # Worst-case latency (safest)
    metadata: Dict[str, Any] # Merged metadata
```

#### `ParallelRetriever`
Main class that manages parallel execution.

**Important Methods**:
- `retrieve()` - Main entry point
- `_run_sequential()` - Fallback for small queries
- `_run_single_engine()` - Execute one engine with timeout
- `_merge_results()` - Deduplicate and rank results

---

### 3.2 `cost_optimizer.py`

**Purpose**: Determine which engines to run and in what order.

**Key Concepts**:

#### EngineCost Model
```python
@dataclass
class EngineCost:
    name: str
    base_latency_ms: float     # Expected latency
    token_cost_per_k: float    # Cost per 1000 tokens
    cache_hit_penalty: float   # Multiplier if cache hit
    cache_miss_penalty: float  # Multiplier if cache miss
    availability: float        # 0-1, how often available
    success_rate: float        # Historical success rate
```

**Default Costs**:
- `ripgrep`: 2ms
- `fts`: 1ms
- `symbol`: 1ms
- `ast`: 5ms
- `graphify`: 20ms
- `semantic`: 80ms
- `lsp`: 3ms

#### Cost Estimation Formula
```
total_cost = (latency * penalty) * (1/availability) * (1/success_rate) + token_cost
```

**Learning**: The optimizer records actual performance and adjusts costs:
- If success rate < 80% → increase cost (avoid)
- If success rate > 95% and latency low → decrease cost (prefer)

---

### 3.3 `adaptive_router.py`

**Purpose**: Learn from telemetry which engines work best for specific query patterns.

**Pattern Extraction**:
```python
pattern_key = f"{query_type}:{file_extension}:{size_category}"
# Example: "symbol:.py:large"
```

**Learning Algorithm**:
1. Collect recent successful queries from telemetry
2. For each pattern, count successes per engine
3. Rank engines by success count
4. On new query, check if pattern has >10 historical queries
5. If yes, use learned best engines first
6. If no, fall back to cost optimizer

**Pattern Stats**:
```python
{
    "patterns_learned": 15,
    "total_pattern_queries": 3420,
    "patterns": [
        {
            "key": "symbol:.py:medium",
            "queries": 1500,
            "best_engines": ["symbol", "lsp", "fts"],
            "avg_tokens": 250
        }
    ]
}
```

---

### 3.4 `telemetry.py`

**Purpose**: Track all query executions for performance analysis.

**Data Collected**:
```python
@dataclass
class QueryEvent:
    timestamp: float
    query: str
    query_type: str
    engines_tried: List[str]
    engines_succeeded: List[str]
    latencies_ms: Dict[str, float]
    cache_hits: Dict[str, bool]
    tokens_estimated: int
    tokens_actual: int
    total_latency_ms: float
    success: bool
    error: Optional[str]
```

**Storage**: SQLite database in `.kryth/telemetry.db`

**Tables**:
- `query_events` - Every query execution
- `engine_stats` - Aggregated per-engine statistics

**Recorder Pattern**:
```python
recorder = TelemetryRecorder(query="test", query_type="keyword")
recorder.record_engine("ripgrep", latency_ms=2.0, cache_hit=False, success=True)
recorder.set_success(True)
event = recorder.finish()
record_event(event)  # Persists to database
```

---

## 4. Execution Flow

### Step-by-Step Example

**User Query**: "Where is `authenticate_user` defined?"

#### Step 1: Classification
```python
from agent.retrieval.engine import classify_query

query_type = classify_query("Where is authenticate_user defined?")
# Returns: "symbol"
```

#### Step 2: Engine Selection
```python
from agent.retrieval.cost_optimizer import get_optimizer
from agent.retrieval.adaptive_router import get_router

optimizer = get_optimizer()
router = get_router()

# Adaptive router may override
engines = router.route(query_type="symbol", path=".", max_results=10)
# Example output: ["symbol", "lsp", "fts"]
```

#### Step 3: Parallel Execution
```python
from agent.retrieval.parallel_retriever import get_retriever

retriever = get_retriever()
results = retriever.retrieve(
    query="authenticate_user",
    path=".",
    engines=["symbol", "lsp", "fts"],
    max_results=10,
    timeout_per_engine=5.0,
    merge=True
)
```

**What happens inside `retrieve()`**:

1. Check if parallel retrieval enabled and >2 engines
2. If not, use sequential fallback
3. Create ThreadPoolExecutor with `max_workers = min(32, cpu_count*3)`
4. Submit each engine as a separate future:
   ```python
   future = executor.submit(_run_single_engine, engine, query, path, max_results, timeout)
   ```
5. Wait for all futures with `as_completed()`
6. Collect results (with timeout handling)
7. Merge duplicates
8. Return sorted list

#### Step 4: Engine Execution Details

Each `_run_single_engine()` does:

```python
def _run_single_engine(engine, query, path, max_results, timeout):
    start = time.time()

    # Dynamically import the engine function
    if engine == 'symbol':
        from agent.retrieval.engine import _run_symbol as run_fn
    elif engine == 'lsp':
        return self._run_lsp(...)  # Special handling

    # Execute with timeout protection
    try:
        output = run_fn(query, path, max_results)
        latency = (time.time() - start) * 1000

        # Parse output into RetrievalResult objects
        results = self._parse_engine_output(engine, output, latency)
        return results
    except Exception as e:
        return []  # Fail gracefully
```

#### Step 5: Result Merging

```python
def _merge_results(self, results_by_engine):
    content_map = {}  # content -> MergedResult

    for engine, results in results_by_engine.items():
        for r in results:
            if r.content not in content_map:
                # First time seeing this result
                content_map[r.content] = MergedResult(
                    content=r.content,
                    engines=[engine],
                    max_score=r.score,
                    total_latency=r.latency_ms,
                    metadata=r.metadata
                )
            else:
                # Duplicate - merge
                merged = content_map[r.content]
                merged.engines.append(engine)
                merged.max_score = max(merged.max_score, r.score)
                merged.total_latency = max(merged.total_latency, r.latency_ms)
                # Merge metadata (accumulate paths)

    # Sort by consensus (number of engines) then score
    merged_list = list(content_map.values())
    merged_list.sort(key=lambda x: (len(x.engines), x.max_score), reverse=True)
    return merged_list
```

**Deduplication Logic**: Results are deduplicated by `content` string. If multiple engines return the same content, it's considered higher confidence (more engines = more trustworthy).

---

## 5. Parallelism Strategy

### Adaptive Parallelism

The system decides whether to use parallel or sequential execution:

```python
def retrieve(self, query, path, engines, max_results, timeout_per_engine, merge):
    # Conditions for sequential fallback:
    if not cfg.ENABLE_PARALLEL_RETRIEVAL:
        return self._run_sequential(...)

    if len(engines) <= 2:
        # Overhead of threading > benefit for 1-2 engines
        return self._run_sequential(...)

    # Otherwise use parallel
    return self._run_parallel(...)
```

**Rationale**:
- 1 engine: obviously sequential
- 2 engines: threading overhead ~1-2ms, minimal benefit
- 3+ engines: parallel wins (assuming engines are I/O bound)

### ThreadPoolExecutor Configuration

```python
max_workers = min(32, (os.cpu_count() or 1) * 3)
```

**Why 3x CPU count?**
- Retrieval engines are I/O bound (subprocess calls, disk I/O, network for LSP)
- I/O-bound tasks benefit from more threads than CPU cores
- 3x is a safe default (can be tuned)

**Example**:
- 8-core machine → 24 workers
- Can run 24 engines simultaneously (though typically only 5-7 are used)

---

## 6. Threading Model

### Thread Safety

All components use thread-safe patterns:

1. **Singleton with Lock**:
```python
_retriever: Optional[ParallelRetriever] = None
_retriever_lock = threading.Lock()

def get_retriever():
    global _retriever
    if _retriever is None:
        with _retriever_lock:
            if _retriever is None:
                _retriever = ParallelRetriever()
    return _retriever
```

2. **RLock for Internal State**:
```python
self._lock = threading.RLock()  # In CostOptimizer, AdaptiveRouter, etc.
```

3. **Thread-Local Storage**: Not used (all state shared intentionally)

### LSP Client Threading

The LSP client has a dedicated reader thread:

```python
class LSPClient:
    def start(self, root_uri):
        # Start language server process
        self.process = subprocess.Popen(...)

        # Start reader thread
        thread = threading.Thread(target=self._reader_loop, daemon=True)
        thread.start()

    def _reader_loop(self):
        """Background thread: read responses from server."""
        while True:
            line = self.process.stdout.readline()
            message = json.loads(line)
            if 'id' in message:
                req_id = message['id']
                with self._lock:
                    self._responses[req_id] = (result, error)
                    self._condition.notify_all()
```

**Why separate thread?**
- LSP uses stdio for communication
- Must continuously read stdout to avoid blocking the server
- Responses are matched to requests via ID
- Condition variable wakes waiting threads

---

## 7. Result Merging

### Deduplication

**By Content**: Results with identical `content` strings are merged.

```python
# Engine A returns: "def authenticate_user() in auth.py:10"
# Engine B returns: "def authenticate_user() in auth.py:10"
# Merged: ["def authenticate_user() in auth.py:10 (from symbol, lsp)"]
```

**Why content-based?**
- Different engines may find the same symbol
- We want to show it once with higher confidence (multiple sources)
- Content is normalized (same format) across engines

**Caveat**: If two different symbols have same text (unlikely), they'll be merged incorrectly. Mitigation: include file:line in content string.

### Scoring

**Consensus Score**: Number of engines that found the result.
```python
merged_list.sort(key=lambda x: (len(x.engines), x.max_score), reverse=True)
```

**Example**:
- Result A: found by 3 engines, score 0.9 → rank 1
- Result B: found by 2 engines, score 1.0 → rank 2 (fewer engines)
- Result C: found by 1 engine, score 1.0 → rank 3

**Rationale**: Consensus > individual quality. Multiple independent sources increase confidence.

### Metadata Merging

```python
if 'path' in r.metadata:
    merged.metadata.setdefault('paths', []).append(r.metadata['path'])
```

Accumulates all unique paths from all engines.

---

## 8. Error Handling

### Per-Engine Isolation

Each engine runs in its own try-except:

```python
try:
    output = run_fn(query, path, max_results)
    results = self._parse_engine_output(engine, output, latency)
    return results
except Exception as e:
    # Log but don't crash
    return []
```

**Why?** One engine failure shouldn't break the whole query.

### Timeout Protection

```python
with ThreadPoolExecutor(...) as executor:
    futures = {}
    for engine in engines:
        future = executor.submit(self._run_single_engine, ...)
        futures[future] = engine

    for future in concurrent.futures.as_completed(futures, timeout=timeout_per_engine + 2.0):
        try:
            results = future.result(timeout=timeout_per_engine)
        except TimeoutError:
            results = []
        except Exception:
            results = []
```

**Timeout Strategy**:
- Each engine gets `timeout_per_engine` seconds
- `as_completed()` has overall timeout (per-engine + 2s buffer)
- If engine times out, return empty results for that engine

### LSP Server Startup Failure

```python
def _get_client(self, path):
    client = LSPClient(language, config)
    if client.start(self.root_uri):
        return client
    else:
        return None  # Graceful fallback to other engines
```

---

## 9. Cache Integration

### Multi-Level Caching

1. **Engine Result Cache** (in `_run_single_engine`):
```python
# Not implemented in current version, but could be:
cache_key = f"engine:{engine}:{query}:{path}"
cached = self._cache.get(cache_key)
if cached is not None:
    return cached
```

2. **LSP Response Cache** (in `lsp_client.py`):
```python
def go_to_definition(self, path, line, character):
    cache_key = self._cache_key("go_to_definition", path, line, character)
    cached = self._cache.get(cache_key)
    if cached is not None:
        return cached

    result = client.go_to_definition(...)
    self._cache.set(cache_key, result, expire=cfg.CACHE_TTL)
    return result
```

3. **Knowledge Cache** (in `knowledge_cache.py`):
   - Caches entire search results by query fingerprint
   - Used by context builder, not directly by parallel retriever

### Cache Invalidation

- **Time-based TTL**: All caches use `cfg.CACHE_TTL` (default 1 hour)
- **File change detection**: Not used in parallel retriever (handled by lower-level caches)
- **Manual invalidation**: `cache.delete(key)` available

---

## 10. Telemetry Integration

### Recording Query Events

The parallel retriever doesn't directly record telemetry, but the surrounding system does:

```python
# In the agent's retrieval flow (not shown in parallel_retriever.py):
recorder = TelemetryRecorder(query=query, query_type=query_type)

# For each engine:
latency = ...
cache_hit = ...
success = len(results) > 0
recorder.record_engine(engine, latency, cache_hit, success)

# After all engines:
recorder.set_success(overall_success)
event = recorder.finish()
record_event(event)  # Persists to SQLite
```

### Metrics Captured

- **Per-engine latency**: How long each engine took
- **Cache hits**: Whether each engine returned cached data
- **Success rate**: Which engines returned results
- **Token estimates**: Rough count of result sizes
- **Overall latency**: Total time from query to merged results

### Telemetry Usage

1. **Cost Optimizer Learning**:
```python
def _adapt_costs(self):
    for engine, metrics in self._metrics.items():
        if metrics.success_rate < 0.8:
            cost.base_latency_ms *= 1.2  # Penalize
        elif metrics.success_rate > 0.95 and metrics.avg_latency < 10.0:
            cost.base_latency_ms *= 0.9  # Reward
```

2. **Adaptive Router Learning**:
```python
def learn_from_telemetry(self):
    events = get_recent_events(limit=1000)
    for event in events:
        pattern_key = extract_pattern(event)
        pattern.update(engine, success=event.success, tokens=...)
```

---

## 11. Adaptive Routing

### Pattern Extraction

```python
def _extract_pattern_key(self, query_type, path):
    # Get file extension
    ext = ""
    if os.path.isfile(path):
        ext = os.path.splitext(path)[1].lower()
    elif os.path.isdir(path):
        # Detect language from common files
        if os.path.exists(os.path.join(path, 'package.json')):
            ext = '.js'
        elif os.path.exists(os.path.join(path, 'Cargo.toml')):
            ext = '.rs'

    # Size category
    total_size = estimate_repo_size(path)
    if total_size < 100_000:
        size_cat = "small"
    elif total_size < 10_000_000:
        size_cat = "medium"
    else:
        size_cat = "large"

    return f"{query_type}:{ext}:{size_cat}"
```

### Routing Decision

```python
def route(self, query_type, path, max_results, hint_pattern=None):
    pattern_key = self._extract_pattern_key(query_type, path)

    with self._lock:
        pattern = self._patterns.get(pattern_key)
        if pattern and pattern.total_queries > 10:
            # Use learned best engines
            best_engines = pattern.get_best_engines()
            cost_engines = self._optimizer.select_engines(...)

            # Merge: learned best first, then cost-based
            ordered = [e for e in best_engines if e in cost_engines]
            for e in cost_engines:
                if e not in ordered:
                    ordered.append(e)
            return ordered

    # Fallback to cost optimizer
    return self._optimizer.select_engines(...)
```

**Example**:
- Pattern `"symbol:.py:medium"` learned: `["symbol", "lsp", "fts"]`
- Cost optimizer says: `["fts", "symbol", "graphify"]`
- Final order: `["symbol", "lsp", "fts", "graphify"]`

---

## 12. Configuration

### Feature Flags

```python
# config.py
ENABLE_PARALLEL_RETRIEVAL = _env_bool("ENABLE_PARALLEL_RETRIEVAL", True)
ENABLE_COST_OPTIMIZER = _env_bool("ENABLE_COST_OPTIMIZER", True)
ENABLE_ADAPTIVE_ROUTING = _env_bool("ENABLE_ADAPTIVE_ROUTING", True)
```

### Environment Overrides

```bash
export ENABLE_PARALLEL_RETRIEVAL=false  # Force sequential
export ENABLE_ADAPTIVE_ROUTING=false    # Disable learning
export MAX_CONCURRENT_READS=8           # Override worker count
```

### Cache Settings

```python
CACHE_SIZE_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB
CACHE_TTL = 3600  # 1 hour
```

---

## 13. Performance Characteristics

### Latency Breakdown

**Sequential** (3 engines, each 10ms):
```
Total = 10 + 10 + 10 = 30ms
```

**Parallel** (3 engines, each 10ms, ThreadPool overhead ~2ms):
```
Total = max(10, 10, 10) + 2ms = 12ms
```

**Speedup**: 2.5x faster

### Scalability

| Engines | Sequential (ms) | Parallel (ms) | Speedup |
|---------|----------------|---------------|---------|
| 2       | 20             | 12            | 1.7x    |
| 3       | 30             | 12            | 2.5x    |
| 5       | 50             | 15            | 3.3x    |
| 10      | 100            | 25            | 4.0x    |

**Note**: Diminishing returns due to ThreadPool overhead and I/O contention.

### Memory Usage

- Each thread: ~1MB stack (default)
- 24 threads → ~24MB
- Plus shared cache (diskcache) → ~100MB typical
- Total overhead: ~124MB

**Acceptable** for modern systems.

---

## 14. Code Walkthrough

### `parallel_retriever.py` - Key Sections

#### 1. Imports and Data Classes
```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field

@dataclass
class RetrievalResult:
    engine: str
    content: str
    score: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
```

#### 2. Main Retrieve Method
```python
def retrieve(self, query, path, engines, max_results, timeout_per_engine, merge):
    # Adaptive: skip parallel for <=2 engines
    if not cfg.ENABLE_PARALLEL_RETRIEVAL or len(engines) <= 2:
        return self._run_sequential(query, path, engines, max_results, merge)

    # Parallel execution
    results_by_engine = {}
    futures = {}

    with ThreadPoolExecutor(max_workers=min(self.max_workers, len(engines))) as executor:
        for engine in engines:
            future = executor.submit(
                self._run_single_engine,
                engine, query, path, max_results, timeout_per_engine
            )
            futures[future] = engine

        for future in concurrent.futures.as_completed(futures, timeout=timeout_per_engine + 2.0):
            engine = futures[future]
            try:
                results = future.result(timeout=timeout_per_engine)
                results_by_engine[engine] = results
            except TimeoutError:
                results_by_engine[engine] = []
            except Exception:
                results_by_engine[engine] = []

    if not merge:
        # Flatten to list
        flat = []
        for engine_results in results_by_engine.values():
            flat.extend(engine_results)
        return [MergedResult(...) for r in flat]

    return self._merge_results(results_by_engine)
```

#### 3. Single Engine Runner
```python
def _run_single_engine(self, engine, query, path, max_results, timeout):
    start = time.time()
    try:
        # Import engine function dynamically
        if engine == 'ripgrep':
            from agent.retrieval.engine import _run_ripgrep as run_fn
        elif engine == 'fts':
            from agent.retrieval.engine import _run_fts as run_fn
        # ... other engines
        elif engine == 'lsp':
            return self._run_lsp(engine, query, path, max_results)
        else:
            return []

        # Execute
        output = run_fn(query, path, max_results)
        latency = (time.time() - start) * 1000.0

        # Parse into RetrievalResult objects
        results = self._parse_engine_output(engine, output, latency)
        return results
    except Exception:
        return []
```

#### 4. LSP Special Handling
```python
def _run_lsp(self, engine, query, path, max_results):
    try:
        from agent.retrieval.lsp_client import get_manager
        manager = get_manager(path)
        results = manager.workspace_symbols(query)

        parsed = []
        for item in results[:max_results]:
            name = item.get('name', '')
            location = item.get('location', {})
            uri = location.get('uri', '')
            file_path = uri.replace('file://', '') if uri.startswith('file://') else uri
            range_data = location.get('range', {})
            line = range_data.get('start', {}).get('line', 0) + 1

            parsed.append(RetrievalResult(
                engine=engine,
                content=f"{name} (LSP result)",
                score=1.0,
                metadata={"path": file_path, "line": line, "name": name}
            ))
        return parsed
    except Exception:
        return []
```

#### 5. Output Parsing
```python
def _parse_engine_output(self, engine, output, latency_ms):
    results = []
    if not output or "(no" in output.lower() or "failed" in output.lower():
        return results

    # Split by double newlines (each block is a result)
    blocks = output.split('\n\n')
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.split('\n', 1)
        title = lines[0].strip()
        content = lines[1].strip() if len(lines) > 1 else title

        results.append(RetrievalResult(
            engine=engine,
            content=content,
            score=1.0,
            metadata={"title": title},
            latency_ms=latency_ms
        ))
        if len(results) >= 100:  # Cap per-engine
            break

    return results
```

#### 6. Merge Logic
```python
def _merge_results(self, results_by_engine):
    content_map = {}

    for engine, results in results_by_engine.items():
        for r in results:
            if r.content not in content_map:
                content_map[r.content] = MergedResult(
                    content=r.content,
                    engines=[engine],
                    max_score=r.score,
                    total_latency=r.latency_ms,
                    metadata=r.metadata
                )
            else:
                merged = content_map[r.content]
                merged.engines.append(engine)
                merged.max_score = max(merged.max_score, r.score)
                merged.total_latency = max(merged.total_latency, r.latency_ms)
                if 'path' in r.metadata:
                    merged.metadata.setdefault('paths', []).append(r.metadata['path'])

    merged_list = list(content_map.values())
    merged_list.sort(key=lambda x: (len(x.engines), x.max_score), reverse=True)
    return merged_list
```

---

## 15. Integration Points

### With Query Router (`engine.py`)

The parallel retriever is **not** directly used by `engine.py`. Instead, it's a **lower-level service** that other components can use.

**Typical integration** (in a higher-level agent):
```python
from agent.retrieval.engine import classify_query
from agent.retrieval.cost_optimizer import get_optimizer
from agent.retrieval.parallel_retriever import get_retriever

def search(query, path, max_results=50):
    # 1. Classify
    query_type = classify_query(query)

    # 2. Select engines
    optimizer = get_optimizer()
    engines = optimizer.select_engines(query_type, path, max_results)

    # 3. Execute in parallel
    retriever = get_retriever()
    results = retriever.retrieve(
        query=query,
        path=path,
        engines=engines,
        max_results=max_results,
        merge=True
    )

    # 4. Format for LLM
    return format_results(results)
```

### With LSP Client (`lsp_client.py`)

The parallel retriever treats LSP as just another engine, but with special handling:

```python
def _run_lsp(self, engine, query, path, max_results):
    # LSP doesn't have a general "search" method
    # We use workspace_symbols for broad searches
    manager = get_manager(path)
    results = manager.workspace_symbols(query)
    # Convert LSP format to RetrievalResult
    ...
```

**Limitation**: LSP's `workspace_symbols` is not as flexible as text search. For specific queries, we'd need to use LSP's `textDocument/definition` etc., but those require a position (line/char), not just a query string. So LSP is primarily used for symbol lookup, not general keyword search.

### With Symbol Index (`symbol_index.py`)

Symbol index is a regular engine:

```python
elif engine == 'symbol':
    from agent.retrieval.engine import _run_symbol as run_fn
```

The `_run_symbol` function (in `engine.py`) uses `repo_index` or symbol index to find symbols.

### With Cost Optimizer (`cost_optimizer.py`)

The cost optimizer provides the engine ordering:

```python
engines = optimizer.select_engines(query_type, path, max_results)
# Returns: ['fts', 'symbol', 'ripgrep']  # sorted by cost
```

The parallel retriever doesn't call the optimizer directly; it's called by the higher-level search function.

### With Adaptive Router (`adaptive_router.py`)

The adaptive router can override the cost optimizer:

```python
# Instead of:
engines = optimizer.select_engines(...)

# Use:
router = get_router()
engines = router.route(query_type, path, max_results)
```

The router internally uses the optimizer but reorders based on learned patterns.

### With Telemetry (`telemetry.py`)

Telemetry is recorded **around** the parallel retriever, not inside it:

```python
recorder = TelemetryRecorder(query, query_type)

# Record each engine
for engine in engines:
    start = time.time()
    results = run_engine(engine)
    latency = (time.time() - start) * 1000
    recorder.record_engine(engine, latency, cache_hit, success)

recorder.set_success(overall_success)
record_event(recorder.finish())
```

---

## 16. Limitations

### 1. LSP General Search Limitation

LSP doesn't have a true "full-text search" method. We use `workspace_symbols` which:
- Only searches symbol names, not content
- May be slow on large workspaces
- Not all servers implement it well

**Workaround**: Use LSP only for symbol queries, not keyword queries.

### 2. Thread Pool Overhead

For 1-2 engines, parallel execution is slower due to threading overhead.

**Mitigation**: Adaptive fallback to sequential.

### 3. Memory Usage

Each thread has stack overhead (~1MB). With 24 threads, that's 24MB. Plus shared cache.

**Acceptable** for desktop/server, but could be an issue on memory-constrained systems.

**Mitigation**: Make `max_workers` configurable.

### 4. No Streaming Results

Results are only returned after all engines complete. No incremental updates.

**Future**: Could yield results as they arrive (async generator).

### 5. No Cancellation

Once started, all engines run to completion (or timeout). Can't cancel mid-execution.

**Future**: Add cancellation token support.

### 6. Duplicate Detection by Content Only

If two different code snippets have identical text (e.g., `return 0`), they'll be merged.

**Mitigation**: Include file:line in content string to make unique.

### 7. No Result Ranking Beyond Consensus

We sort by (num_engines, score). No sophisticated ranking (e.g., BM25, embeddings).

**Future**: Integrate with Graphify's ranking or use learned model.

---

## 17. Future Improvements

### 1. Async/Await Support

Instead of ThreadPoolExecutor, use `asyncio` for better scalability:

```python
async def retrieve_async(self, ...):
    tasks = [self._run_engine_async(engine) for engine in engines]
    results = await asyncio.gather(*tasks, return_exceptions=True)
```

**Benefits**:
- Lower overhead (no thread creation)
- Better for I/O-bound operations
- Easier cancellation

**Challenge**: LSP client uses blocking stdio; would need async wrapper.

### 2. Result Streaming

Yield results as they become available:

```python
def retrieve_streaming(self, ...):
    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(...): engine for engine in engines}
        for future in as_completed(futures):
            results = future.result()
            for r in results:
                yield r  # Stream to caller
```

**Use case**: Show partial results to user while waiting for slow engines.

### 3. Smart Timeout per Engine

Different engines have different typical latencies:

```python
timeouts = {
    'ripgrep': 2.0,
    'fts': 1.0,
    'symbol': 1.0,
    'graphify': 10.0,
    'semantic': 20.0,
    'lsp': 5.0,
}
timeout = timeouts.get(engine, 5.0)
```

**Benefit**: Don't wait 20s for semantic if we already have results from 3 fast engines.

### 4. Early Termination

If we get N high-quality results from consensus engines, skip remaining engines:

```python
if len(high_confidence_results) >= max_results:
    # Cancel pending futures
    for future in pending_futures:
        future.cancel()
    break
```

**Benefit**: Reduce latency for queries with obvious answers.

### 5. Engine Warmup

Pre-warm frequently used engines on startup:

```python
def warmup(self):
    for engine in ['symbol', 'fts', 'ripgrep']:
        self._run_single_engine(engine, "test", ".", 1, 1.0)
```

**Benefit**: First query isn't slowed by JIT compilation or server startup.

### 6. Distributed Parallelism

For huge repos, distribute across multiple machines:

```python
# Shard by file path
shards = partition_repo_by_path(path, num_shards=4)
futures = [cluster.submit(search_shard, query, shard) for shard in shards]
```

**Use case**: Monorepos with 100k+ files.

### 7. Result Caching at Parallel Level

Cache the **merged** results, not just individual engine outputs:

```python
merged_cache_key = f"merged:{query_type}:{hash(query)}:{hash(tuple(engines))}"
cached = self._cache.get(merged_cache_key)
if cached:
    return cached
```

**Benefit**: Avoid re-parsing and merging for repeated queries.

### 8. Engine Health Monitoring

Track engine failure rates and temporarily disable:

```python
if engine_failure_rate[engine] > 0.5:
    # Skip this engine for a while
    continue
```

**Benefit**: Avoid wasting time on broken engines.

### 9. Priority Queue for Engines

Instead of fixed ordering, use priority queue that updates based on partial results:

```python
# Start all engines
# After 100ms, check which are done
# If we already have 5 good results, cancel slow engines
```

**Benefit**: Dynamic adaptation to actual performance.

### 10. Resource-Aware Scheduling

Monitor system load and adjust parallelism:

```python
if psutil.cpu_percent() > 80 or psutil.virtual_memory().percent > 80:
    max_workers = 4  # Reduce parallelism
else:
    max_workers = 24
```

**Benefit**: Don't overwhelm the system.

---

## 18. Summary

The **Parallel Retriever** is a sophisticated multi-engine search orchestrator that:

1. **Selects engines** via cost optimizer and adaptive router
2. **Executes in parallel** using ThreadPoolExecutor
3. **Handles failures** gracefully (per-engine isolation)
4. **Enforces timeouts** to prevent hangs
5. **Deduplicates** results by content
6. **Ranks** by consensus (number of engines) then score
7. **Caches** at multiple levels
8. **Records telemetry** for learning
9. **Adapts** routing based on historical performance

**Key Files**:
- `parallel_retriever.py` - Core parallel execution
- `cost_optimizer.py` - Engine selection and cost modeling
- `adaptive_router.py` - Learned routing patterns
- `telemetry.py` - Performance tracking
- `lsp_client.py` - One of the parallel engines

**Performance**: 2-4x speedup over sequential, with better recall and confidence scoring.

**Scalability**: Handles 10+ engines simultaneously, adaptive worker pool, memory-efficient.

**Production-Ready**: Thread-safe, error-isolated, telemetry-enabled, feature-flagged.

---

*End of Deep Dive*