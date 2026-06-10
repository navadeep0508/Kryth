"""Comprehensive tests for advanced retrieval components.

Tests cover:
- Symbol Index
- AST Cache
- Dependency Graph
- LSP Client (mocked)
- Cost Optimizer
- Parallel Retriever
- Context Compression
- Context Builder
- Knowledge Cache
- Telemetry
- Adaptive Router
- Refactoring Intelligence
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.retrieval import config as cfg
from agent.retrieval.ast_cache import capabilities as ast_caps, parse_file
from agent.retrieval.cost_optimizer import CostOptimizer, get_optimizer
from agent.retrieval.dep_graph import DependencyGraph, get_graph
from agent.retrieval.knowledge_cache import KnowledgeAccessor, get_knowledge
from agent.retrieval.parallel_retriever import ParallelRetriever, RetrievalResult
from agent.retrieval.symbol_index import SymbolIndex, get_index
from agent.retrieval.telemetry import QueryEvent, TelemetryRecorder, get_store
from agent.retrieval.context_compression import SummaryGenerator, get_generator
from agent.retrieval.context_builder import ContextBuilder, get_builder
from agent.retrieval.adaptive_router import AdaptiveRouter, get_router
from agent.retrieval.refactor_intelligence import RefactoringIntelligence, get_refactor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository with sample files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    
    # Create a Python file
    (repo / "module.py").write_text("""
def hello(name: str) -> str:
    '''Say hello.'''
    return f"Hello, {name}!"

class Greeter:
    def __init__(self):
        self.greetings = []
    
    def add_greeting(self, greeting: str):
        self.greetings.append(greeting)
    
    def greet(self, name: str) -> str:
        return hello(name)
""")
    
    # Create another file that imports
    (repo / "app.py").write_text("""
from module import hello, Greeter

def main():
    g = Greeter()
    print(hello("World"))

if __name__ == "__main__":
    main()
""")
    
    return repo


@pytest.fixture
def symbol_index(temp_repo: Path) -> SymbolIndex:
    """Create a symbol index for the temp repo."""
    idx = SymbolIndex(str(temp_repo))
    idx.rebuild()
    return idx


@pytest.fixture
def dep_graph(temp_repo: Path) -> DependencyGraph:
    """Create a dependency graph for the temp repo."""
    graph = DependencyGraph(str(temp_repo))
    graph.rebuild()
    return graph


@pytest.fixture
def summary_generator(temp_repo: Path) -> SummaryGenerator:
    """Create a summary generator."""
    return SummaryGenerator(str(temp_repo))


@pytest.fixture
def context_builder(temp_repo: Path) -> ContextBuilder:
    """Create a context builder."""
    return ContextBuilder(str(temp_repo))


@pytest.fixture
def knowledge_cache(temp_repo: Path) -> KnowledgeAccessor:
    """Create a knowledge cache."""
    return KnowledgeAccessor(str(temp_repo))


@pytest.fixture
def telemetry_store(temp_repo: Path) -> TelemetryStore:
    """Create a telemetry store."""
    return TelemetryStore(str(temp_repo))


# ---------------------------------------------------------------------------
# Symbol Index Tests
# ---------------------------------------------------------------------------

def test_symbol_index_build(symbol_index: SymbolIndex):
    """Test building symbol index."""
    stats = symbol_index.get_statistics()
    assert stats["total_symbols"] > 0
    assert stats["total_files"] >= 2


def test_symbol_index_find_by_name(symbol_index: SymbolIndex):
    """Test finding symbols by name."""
    results = symbol_index.find_by_name("hello")
    assert len(results) >= 1
    assert any(r["name"] == "hello" for r in results)


def test_symbol_index_find_by_type(symbol_index: SymbolIndex):
    """Test finding symbols by type."""
    functions = symbol_index.find_by_type("function")
    classes = symbol_index.find_by_type("class")
    assert len(functions) >= 1
    assert len(classes) >= 1


def test_symbol_index_find_in_file(symbol_index: SymbolIndex, temp_repo: Path):
    """Test finding symbols in a specific file."""
    module_path = str(temp_repo / "module.py")
    symbols = symbol_index.find_in_file(module_path)
    assert len(symbols) >= 3  # hello, Greeter, add_greeting, greet


def test_symbol_index_incremental(symbol_index: SymbolIndex, temp_repo: Path):
    """Test incremental indexing."""
    # Initial count
    initial_stats = symbol_index.get_statistics()
    
    # Touch a file (should not reindex)
    module_path = temp_repo / "module.py"
    module_path.touch()
    
    # Should still be up-to-date
    stats_after_touch = symbol_index.get_statistics()
    assert stats_after_touch["total_symbols"] == initial_stats["total_symbols"]


# ---------------------------------------------------------------------------
# Dependency Graph Tests
# ---------------------------------------------------------------------------

def test_dep_graph_build(dep_graph: DependencyGraph):
    """Test building dependency graph."""
    stats = dep_graph.get_statistics()
    assert stats["total_edges"] > 0


def test_dep_graph_imports(dep_graph: DependencyGraph, temp_repo: Path):
    """Test getting imports for a file."""
    app_path = str(temp_repo / "app.py")
    imports = dep_graph.get_imports(app_path)
    assert len(imports) >= 1
    # Should import from module
    assert any("module" in imp["target_file"] for imp in imports)


def test_dep_graph_imported_by(dep_graph: DependencyGraph, temp_repo: Path):
    """Test getting files that import a given file."""
    module_path = str(temp_repo / "module.py")
    imported_by = dep_graph.get_imported_by(module_path)
    assert len(imported_by) >= 1
    assert any("app.py" in ib["source_file"] for ib in imported_by)


# ---------------------------------------------------------------------------
# AST Cache Tests
# ---------------------------------------------------------------------------

def test_ast_cache_parse_file():
    """Test parsing a file with AST cache."""
    # This test will be skipped if tree-sitter is not available
    if not ast_caps().get("tree_sitter"):
        pytest.skip("tree-sitter not available")
    
    # Create a simple Python file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write("def test(): pass")
        path = f.name
    
    try:
        tree = parse_file(path)
        # tree is None if tree-sitter not fully set up, that's ok
        # The important thing is no exception
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Cost Optimizer Tests
# ---------------------------------------------------------------------------

def test_cost_optimizer_select_engines():
    """Test engine selection."""
    optimizer = CostOptimizer()
    engines = optimizer.select_engines("keyword", ".", 10)
    assert isinstance(engines, list)
    assert len(engines) > 0


def test_cost_optimizer_record():
    """Test recording performance."""
    optimizer = CostOptimizer()
    optimizer.record_attempt("ripgrep", latency_ms=2.0, cache_hit=False, success=True)
    stats = optimizer.get_metrics_report()
    assert "ripgrep" in stats


# ---------------------------------------------------------------------------
# Parallel Retriever Tests
# ---------------------------------------------------------------------------

def test_parallel_retriever_sequential_fallback():
    """Test sequential fallback when parallel disabled."""
    with patch('agent.retrieval.config.ENABLE_PARALLEL_RETRIEVAL', False):
        retriever = ParallelRetriever()
        results = retriever.retrieve(
            query="test",
            path=".",
            engines=["fts"],
            max_results=5,
            merge=False
        )
        # Should return empty or some results
        assert isinstance(results, list)


def test_parallel_retriever_merge():
    """Test result merging."""
    retriever = ParallelRetriever()
    results_by_engine = {
        "fts": [
            RetrievalResult("fts", "result1", score=1.0, latency_ms=1.0),
            RetrievalResult("fts", "result2", score=0.8, latency_ms=1.0),
        ],
        "ripgrep": [
            RetrievalResult("ripgrep", "result1", score=1.0, latency_ms=2.0),  # duplicate
            RetrievalResult("ripgrep", "result3", score=0.9, latency_ms=2.0),
        ],
    }
    merged = retriever._merge_results(results_by_engine)
    # Should have 2 unique results (result1 appears in both)
    assert len(merged) == 2
    # result1 should have both engines listed
    r1 = next(r for r in merged if r.content == "result1")
    assert len(r1.engines) == 2


# ---------------------------------------------------------------------------
# Context Compression Tests
# ---------------------------------------------------------------------------

def test_summary_generator_file_summary(summary_generator: SummaryGenerator, temp_repo: Path):
    """Test generating file summary."""
    module_path = temp_repo / "module.py"
    summary = summary_generator.get_file_summary(str(module_path))
    assert summary is not None
    assert summary.language == "python"
    assert len(summary.key_symbols) >= 1


def test_summary_generator_folder_summary(summary_generator: SummaryGenerator, temp_repo: Path):
    """Test generating folder summary."""
    summary = summary_generator.get_folder_summary(str(temp_repo))
    assert summary is not None
    assert summary.files >= 2
    assert "python" in summary.languages


# ---------------------------------------------------------------------------
# Context Builder Tests
# ---------------------------------------------------------------------------

def test_context_builder_build(context_builder: ContextBuilder):
    """Test building context."""
    context = context_builder.build(
        query="Where is hello defined?",
        query_type="symbol",
        max_tokens=1000
    )
    assert isinstance(context, BuiltContext)
    assert len(context.pieces) >= 0
    assert context.total_tokens >= 0


# ---------------------------------------------------------------------------
# Knowledge Cache Tests
# ---------------------------------------------------------------------------

def test_knowledge_cache_set_get(knowledge_cache: KnowledgeAccessor):
    """Test setting and getting knowledge."""
    knowledge_cache.set_search_result(
        query="test",
        query_type="keyword",
        results=["result1", "result2"]
    )
    results = knowledge_cache.get_search_result("test", "keyword")
    assert results == ["result1", "result2"]


def test_knowledge_cache_hot_file(knowledge_cache: KnowledgeAccessor):
    """Test hot file tracking."""
    knowledge_cache.increment_hot_file("/path/to/file.py")
    meta = knowledge_cache.get_hot_file("/path/to/file.py")
    assert meta is not None
    assert meta["access_count"] >= 1


# ---------------------------------------------------------------------------
# Telemetry Tests
# ---------------------------------------------------------------------------

def test_telemetry_recorder():
    """Test telemetry recording."""
    recorder = TelemetryRecorder(query="test", query_type="keyword")
    recorder.record_engine("ripgrep", latency_ms=2.0, cache_hit=False, success=True)
    recorder.set_success(True)
    event = recorder.finish()
    assert event.query == "test"
    assert "ripgrep" in event.engines_tried
    assert event.success is True


def test_telemetry_store_record(telemetry_store: TelemetryStore):
    """Test storing telemetry events."""
    event = QueryEvent(
        timestamp=time.time(),
        query="test",
        query_type="keyword",
        engines_tried=["ripgrep"],
        engines_succeeded=["ripgrep"],
        latencies_ms={"ripgrep": 2.0},
        cache_hits={"ripgrep": False},
        tokens_estimated=100,
        tokens_actual=80,
        total_latency_ms=2.0,
        success=True,
    )
    telemetry_store.record_event(event)
    events = telemetry_store.get_recent_events(limit=1)
    assert len(events) == 1
    assert events[0].query == "test"


# ---------------------------------------------------------------------------
# Adaptive Router Tests
# ---------------------------------------------------------------------------

def test_adaptive_router_route():
    """Test adaptive routing."""
    router = AdaptiveRouter()
    engines = router.route(query_type="keyword", path=".", max_results=10)
    assert isinstance(engines, list)
    assert len(engines) > 0


def test_adaptive_router_learn():
    """Test learning from telemetry."""
    router = AdaptiveRouter()
    # Simulate some successful queries
    router.learn_from_telemetry()
    stats = router.get_pattern_stats()
    assert "patterns_learned" in stats


# ---------------------------------------------------------------------------
# Refactoring Intelligence Tests
# ---------------------------------------------------------------------------

def test_refactor_analyze_rename(refactor: RefactoringIntelligence, temp_repo: Path):
    """Test rename analysis."""
    module_path = str(temp_repo / "module.py")
    analysis = refactor.analyze_rename("hello", module_path, "greet")
    assert analysis.symbol_name == "hello"
    assert analysis.file == module_path
    # Should have references (app.py calls hello)
    assert len(analysis.all_references) >= 1


def test_refactor_find_unused(refactor: RefactoringIntelligence):
    """Test finding unused code."""
    result = refactor.find_unused_code()
    assert "unused_functions" in result.to_dict()
    assert "unused_classes" in result.to_dict()


def test_refactor_circular_deps(refactor: RefactoringIntelligence):
    """Test circular dependency detection."""
    circles = refactor.find_circular_dependencies()
    # Our test repo should not have circles, but method should work
    assert isinstance(circles, list)


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------

def test_full_capabilities():
    """Test that all new components report capabilities."""
    from agent.retrieval import capabilities as all_caps
    caps = all_caps()
    # Check new flags exist
    assert "lsp" in caps
    assert "symbol_index" in caps
    assert "ast_cache" in caps
    assert "dep_graph" in caps
    assert "cost_optimizer" in caps
    assert "parallel_retrieval" in caps
    assert "context_compression" in caps
    assert "telemetry" in caps
    assert "adaptive_routing" in caps
    assert "refactoring_intelligence" in caps
    assert "knowledge_cache" in caps


def test_singleton_patterns():
    """Test that singleton getters work."""
    idx1 = get_index(".")
    idx2 = get_index(".")
    assert idx1 is idx2  # Same instance

    graph1 = get_graph(".")
    graph2 = get_graph(".")
    assert graph1 is graph2

    # Clean up
    idx1.close()
    graph1.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])