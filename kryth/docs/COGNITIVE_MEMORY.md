# KRYTH Cognitive Memory Stack

## Architecture

The 6-layer cognitive memory stack gives KRYTH human-like memory capabilities:

```
┌──────────────────────────────────────────────────────────┐
│                  CognitiveMemoryManager                   │
│                    (Unified Interface)                     │
├──────────────────────────────────────────────────────────┤
│                     Memory Router                         │
│  (Intent classification → layer routing → result merge)   │
├──────┬──────┬──────┬──────┬──────┬──────────────────────┤
│  L1  │  L2  │  L3  │  L4  │  L5  │         L6           │
│Graph │Graph │Neo4j │Qdrant│Redis │    Consolidator       │
│ ify  │ iti  │      │      │      │                       │
├──────┼──────┼──────┼──────┼──────┼──────────────────────┤
│Code  │Facts │Bug→  │Vector│Live  │  Compress + Learn     │
│AST   │over  │Fix   │embed │state │  Dedup + Decay        │
│graph │time  │cause │search│TTL   │  Summarize            │
└──────┴──────┴──────┴──────┴──────┴──────────────────────┘
```

## Layers

### Layer 1: Graphify (Structural Code Memory)
- **Purpose:** Repo-level code understanding
- **Stores:** Files, classes, functions, imports, call graph, dependencies
- **Queries:** "Who calls login()?" "What imports auth?" "Impact of changing X?"
- **Backend:** graphifyy + networkx (local, no server needed)

### Layer 2: Graphiti (Temporal Memory)
- **Purpose:** Track facts that change over time
- **Stores:** Decisions, config changes, architecture evolution, regressions
- **Queries:** "When was the DB changed?" "What was the previous config?"
- **Backend:** LITE=SQLite, FULL=Graphiti + Neo4j

### Layer 3: Neo4j (Causal Memory)
- **Purpose:** Bug→Fix relationship graph for autonomous repair
- **Stores:** Failures, fixes, patches, affected files
- **Queries:** "What fixed similar TypeError?" "Root cause of crash?"
- **Backend:** LITE=SQLite, FULL=Neo4j graph database

### Layer 4: Qdrant (Semantic Memory)
- **Purpose:** Meaning-based retrieval across docs/code/conversations
- **Stores:** Embeddings of code, docs, tasks, patches, conversations
- **Queries:** "Find similar patterns to..." "Related code to..."
- **Backend:** Qdrant (on-disk or remote), fallback to ChromaDB/BM25

### Layer 5: Redis (Hot Working Memory)
- **Purpose:** Live runtime state for multi-agent coordination
- **Stores:** Active shells, browser tabs, task state, CWD, agent status
- **Queries:** "Current state?" "What's active now?"
- **Backend:** Redis (with in-memory fallback)

### Layer 6: Consolidator (Memory Compression + Learning)
- **Purpose:** Autonomous memory maintenance
- **Actions:** Deduplicate, decay stale entries, merge duplicates, summarize patterns
- **Triggers:** Every 25 tool calls OR 30k context chars OR session end
- **Output:** Summaries stored in Temporal + Semantic layers

## Operational Modes

### LITE Mode (Default — 8 GB RAM)
Enabled layers: Graphify + Qdrant + Redis (with SQLite/in-memory fallbacks)

```env
KRYTH_MEMORY_MODE=lite
```

### FULL Mode (Cloud/Server)
All 6 layers with external services:

```env
KRYTH_MEMORY_MODE=full
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379/0
```

## Configuration

Feature flags (all overridable via env):

```env
ENABLE_GRAPHIFY=true       # Code structure graph
ENABLE_GRAPHITI=false      # Temporal memory (FULL mode only)
ENABLE_NEO4J=false         # Graph database (FULL mode only)
ENABLE_QDRANT=true         # Vector search
ENABLE_REDIS=true          # Hot memory
ENABLE_CONSOLIDATOR=true   # Autonomous maintenance
```

## Usage

```python
from agent.memory.cognitive import CognitiveMemoryManager, MemoryEntry, MemoryLayer

# Initialize
manager = CognitiveMemoryManager(project_hash="abc123", cwd="/project")

# Retrieve (auto-routed to best layer)
results = manager.retrieve("what functions call login()?")

# Write (specify target layer)
entry = MemoryEntry(
    key="api_change",
    value="REST API migrated from v1 to v2",
    layer=MemoryLayer.TEMPORAL,
    confidence=0.95,
)
manager.write(entry)

# Consolidate (manual trigger)
result = manager.consolidate()

# Stats
stats = manager.get_stats()
```

## Graceful Degradation

Every layer has a fallback:
- Graphify → disabled (no structural queries)
- Graphiti → SQLite temporal DB
- Neo4j → SQLite causal DB
- Qdrant → ChromaDB → NumPy TF-IDF → BM25
- Redis → In-memory dict with TTL
- Consolidator → always available (works with whatever layers exist)

The system never crashes due to a missing dependency. Each layer initializes independently and reports its status.

## Migration from Legacy

```bash
python -m agent.memory.cognitive.migration.migrate_legacy <project_hash> [cwd]
```

This migrates:
- `execution.db` → Causal layer (failed commands)
- `failure.db` → Causal layer (failures + fixes)
- `decision.db` → Temporal layer (decisions as facts)
- `workflow.db` → Temporal layer (patterns)

Legacy databases are preserved (read-only backup). No data loss.

## Performance Targets

| Metric | Target |
|--------|--------|
| Hot memory latency | <50 ms |
| Vector search | <250 ms |
| Structural query | <500 ms |
| Token reduction | 50-80% |
| Retrieval relevance | 2x baseline |
| Autonomous repair | 30% better |
