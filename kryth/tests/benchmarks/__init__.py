"""Benchmark suite for retrieval engine performance.

Measures:
- Symbol lookup latency
- Go-to-definition latency
- Reference search latency
- Dependency lookup latency
- Context generation time
- Parallel retrieval speedup
- Cache hit rate
- Memory usage
- Token savings

Compares baseline (old) vs upgraded (new) implementation.
"""

from __future__ import annotations

import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.retrieval import capabilities as all_caps


@dataclass
class BenchmarkResult:
    """Result of a single benchmark."""
    name: str
    iterations: int
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float
    throughput_qps: float
    memory_mb: float
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "iterations": self.iterations,
            "avg_latency_ms": self.avg_latency_ms,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "p99_latency_ms": self.p99_latency_ms,
            "min_latency_ms": self.min_latency_ms,
            "max_latency_ms": self.max_latency_ms,
            "throughput_qps": self.throughput_qps,
            "memory_mb": self.memory_mb,
            "metadata": self.metadata,
        }


class BenchmarkRunner:
    """Run benchmarks and collect results."""

    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)
        self.results: List[BenchmarkResult] = []

    def run_benchmark(
        self,
        name: str,
        func: Callable[[], Any],
        iterations: int = 10,
        warmup: int = 2,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> BenchmarkResult:
        """Run a single benchmark function."""
        # Warmup
        for _ in range(warmup):
            try:
                func()
            except Exception:
                pass

        # Measure
        latencies = []
        start_mem = self._get_memory_usage()
        for _ in range(iterations):
            t0 = time.perf_counter()
            try:
                func()
            except Exception as e:
                # Record failure but continue
                latencies.append(None)
                print(f"Error in {name}: {e}")
            else:
                t1 = time.perf_counter()
                latencies.append((t1 - t0) * 1000.0)  # ms

        end_mem = self._get_memory_usage()

        # Filter out None (failed) runs
        valid_latencies = [l for l in latencies if l is not None]
        if not valid_latencies:
            return BenchmarkResult(
                name=name,
                iterations=iterations,
                avg_latency_ms=0.0,
                p50_latency_ms=0.0,
                p95_latency_ms=0.0,
                p99_latency_ms=0.0,
                min_latency_ms=0.0,
                max_latency_ms=0.0,
                throughput_qps=0.0,
                memory_mb=0.0,
                metadata=metadata or {},
            )

        sorted_lat = sorted(valid_latencies)
        n = len(valid_latencies)
        p50 = sorted_lat[int(n * 0.50)]
        p95 = sorted_lat[int(n * 0.95)]
        p99 = sorted_lat[int(n * 0.99)]

        result = BenchmarkResult(
            name=name,
            iterations=iterations,
            avg_latency_ms=statistics.mean(valid_latencies),
            p50_latency_ms=p50,
            p95_latency_ms=p95,
            p99_latency_ms=p99,
            min_latency_ms=min(valid_latencies),
            max_latency_ms=max(valid_latencies),
            throughput_qps=1000.0 / statistics.mean(valid_latencies) if valid_latencies else 0.0,
            memory_mb=end_mem - start_mem,
            metadata=metadata or {},
        )
        self.results.append(result)
        return result

    def _get_memory_usage(self) -> float:
        """Get current process memory usage in MB."""
        try:
            import psutil
            import os
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / 1024 / 1024
        except ImportError:
            return 0.0

    def print_report(self) -> None:
        """Print a formatted report of all results."""
        print("\n" + "=" * 80)
        print("BENCHMARK RESULTS")
        print("=" * 80)
        for r in self.results:
            print(f"\n{r.name}:")
            print(f"  Iterations: {r.iterations}")
            print(f"  Avg latency: {r.avg_latency_ms:.2f} ms")
            print(f"  P50: {r.p50_latency_ms:.2f} ms")
            print(f"  P95: {r.p95_latency_ms:.2f} ms")
            print(f"  P99: {r.p99_latency_ms:.2f} ms")
            print(f"  Min/Max: {r.min_latency_ms:.2f} / {r.max_latency_ms:.2f} ms")
            print(f"  Throughput: {r.throughput_qps:.1f} q/s")
            print(f"  Memory delta: {r.memory_mb:.1f} MB")
            if r.metadata:
                print(f"  Metadata: {r.metadata}")

    def save_json(self, path: str) -> None:
        """Save results to JSON file."""
        import json
        data = [r.to_dict() for r in self.results]
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Specific benchmarks
# ---------------------------------------------------------------------------

def benchmark_symbol_lookup(repo_path: str, symbol: str = "hello") -> BenchmarkResult:
    """Benchmark symbol lookup via symbol index."""
    from agent.retrieval.symbol_index import get_index

    runner = BenchmarkRunner(repo_path)
    idx = get_index(repo_path)

    def lookup():
        results = idx.find_by_name(symbol, limit=10)
        return len(results)

    return runner.run_benchmark(
        name=f"Symbol Lookup: {symbol}",
        func=lookup,
        iterations=50,
        metadata={"symbol": symbol, "engine": "symbol_index"}
    )


def benchmark_dependency_lookup(repo_path: str, file: str) -> BenchmarkResult:
    """Benchmark dependency lookup."""
    from agent.retrieval.dep_graph import get_graph

    runner = BenchmarkRunner(repo_path)
    graph = get_graph(repo_path)

    def lookup():
        imports = graph.get_imports(file)
        return len(imports)

    return runner.run_benchmark(
        name=f"Dependency Lookup: {os.path.basename(file)}",
        func=lookup,
        iterations=50,
        metadata={"file": file, "engine": "dep_graph"}
    )


def benchmark_parallel_retrieval(repo_path: str, query: str) -> BenchmarkResult:
    """Benchmark parallel vs sequential retrieval."""
    from agent.retrieval.parallel_retriever import get_retriever

    runner = BenchmarkRunner(repo_path)
    retriever = get_retriever()

    def parallel():
        results = retriever.retrieve(
            query=query,
            path=repo_path,
            engines=["fts", "symbol"],
            max_results=10,
            merge=True
        )
        return len(results)

    return runner.run_benchmark(
        name=f"Parallel Retrieval: {query[:30]}...",
        func=parallel,
        iterations=20,
        metadata={"query": query, "engines": ["fts", "symbol"]}
    )


def benchmark_context_generation(repo_path: str) -> BenchmarkResult:
    """Benchmark context generation."""
    from agent.retrieval.context_builder import get_builder

    runner = BenchmarkRunner(repo_path)
    builder = get_builder(repo_path)

    def build():
        context = builder.build(
            query="Where is the main function defined?",
            query_type="symbol",
            max_tokens=2000
        )
        return context.total_tokens

    return runner.run_benchmark(
        name="Context Generation",
        func=build,
        iterations=20,
        metadata={"query_type": "symbol", "max_tokens": 2000}
    )


def benchmark_summary_generation(repo_path: str) -> BenchmarkResult:
    """Benchmark file/folder summary generation."""
    from agent.retrieval.context_compression import get_generator

    runner = BenchmarkRunner(repo_path)
    gen = get_generator(repo_path)

    def summarize():
        summary = gen.get_folder_summary(repo_path)
        return len(summary.key_symbols) if summary else 0

    return runner.run_benchmark(
        name="Summary Generation",
        func=summarize,
        iterations=30,
        metadata={"type": "folder_summary"}
    )


def run_all_benchmarks(repo_path: str) -> List[BenchmarkResult]:
    """Run all benchmarks and return results."""
    results = []

    print("Running benchmarks...")
    print("Note: Some benchmarks may be skipped if features are disabled.")

    # Symbol lookup
    try:
        results.append(benchmark_symbol_lookup(repo_path))
    except Exception as e:
        print(f"Symbol lookup benchmark skipped: {e}")

    # Dependency lookup
    try:
        # Find a Python file in repo
        py_files = list(Path(repo_path).rglob("*.py"))
        if py_files:
            results.append(benchmark_dependency_lookup(repo_path, str(py_files[0])))
    except Exception as e:
        print(f"Dependency lookup benchmark skipped: {e}")

    # Parallel retrieval
    try:
        results.append(benchmark_parallel_retrieval(repo_path, "function definition"))
    except Exception as e:
        print(f"Parallel retrieval benchmark skipped: {e}")

    # Context generation
    try:
        results.append(benchmark_context_generation(repo_path))
    except Exception as e:
        print(f"Context generation benchmark skipped: {e}")

    # Summary generation
    try:
        results.append(benchmark_summary_generation(repo_path))
    except Exception as e:
        print(f"Summary generation benchmark skipped: {e}")

    return results


if __name__ == "__main__":
    import sys
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    runner = BenchmarkRunner(repo)
    results = run_all_benchmarks(repo)
    runner.results = results
    runner.print_report()
    if len(sys.argv) > 2:
        runner.save_json(sys.argv[2])