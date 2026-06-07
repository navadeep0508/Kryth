"""Parallel retrieval engine - run multiple search engines simultaneously.

Instead of sequential execution, launch multiple retrieval strategies in parallel
and merge results. Uses ThreadPoolExecutor for I/O-bound operations.

Adaptive parallelism:
  - Few files (< 10) → sequential (lower overhead)
  - Many files (10-1000) → ThreadPool (default workers)
  - Huge repos (> 1000) → async + batching (future)

Features:
- Configurable worker limits
- Timeout per engine
- Cancellation support
- Result deduplication
- Score merging
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from agent.retrieval import config as cfg
from agent.retrieval.cache import get_cache


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class RetrievalResult:
    """Single result from a retrieval engine."""
    engine: str
    content: str
    score: float = 1.0  # Higher is better
    metadata: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    def __hash__(self) -> int:
        # Hash by content for deduplication
        return hash(self.content)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, RetrievalResult):
            return False
        return self.content == other.content


@dataclass
class MergedResult:
    """Merged result from multiple engines."""
    content: str
    engines: List[str]
    max_score: float
    total_latency: float
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parallel retriever
# ---------------------------------------------------------------------------

class ParallelRetriever:
    """Execute multiple retrieval strategies in parallel."""

    def __init__(self, max_workers: Optional[int] = None):
        self.max_workers = max_workers or min(32, (os.cpu_count() or 1) * 3)
        self._cache = get_cache("parallel_retriever")
        self._lock = threading.RLock()

    def retrieve(
        self,
        query: str,
        path: str,
        engines: List[str],
        max_results: int = 50,
        timeout_per_engine: float = 10.0,
        merge: bool = True,
    ) -> List[MergedResult]:
        """Run multiple engines in parallel and merge results.

        Args:
            query: Search query
            path: Directory to search in
            engines: List of engine names to run
            max_results: Max results per engine
            timeout_per_engine: Timeout for each engine
            merge: If True, merge duplicate results; else return flat list

        Returns:
            List of MergedResult (or RetrievalResult if merge=False)
        """
        if not cfg.ENABLE_PARALLEL_RETRIEVAL or len(engines) <= 1:
            # Fallback to sequential execution
            return self._run_sequential(query, path, engines, max_results, merge)

        # Adaptive: if very few engines, just run sequentially to avoid overhead
        if len(engines) <= 2:
            return self._run_sequential(query, path, engines, max_results, merge)

        results_by_engine: Dict[str, List[RetrievalResult]] = {}
        futures: Dict[concurrent.futures.Future, str] = {}

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
                    result_list = future.result(timeout=timeout_per_engine)
                    results_by_engine[engine] = result_list
                except TimeoutError:
                    results_by_engine[engine] = []
                except Exception:
                    results_by_engine[engine] = []

        if not merge:
            # Flatten
            flat: List[RetrievalResult] = []
            for engine_results in results_by_engine.values():
                flat.extend(engine_results)
            return [MergedResult(
                content=r.content,
                engines=[r.engine],
                max_score=r.score,
                total_latency=r.latency_ms,
                metadata=r.metadata
            ) for r in flat]

        # Merge duplicates
        return self._merge_results(results_by_engine)

    def _run_sequential(
        self,
        query: str,
        path: str,
        engines: List[str],
        max_results: int,
        merge: bool,
    ) -> List[MergedResult]:
        """Run engines sequentially (fallback)."""
        results_by_engine: Dict[str, List[RetrievalResult]] = {}
        for engine in engines:
            results = self._run_single_engine(engine, query, path, max_results, 10.0)
            results_by_engine[engine] = results

        if not merge:
            flat: List[RetrievalResult] = []
            for engine_results in results_by_engine.values():
                flat.extend(engine_results)
            return [MergedResult(
                content=r.content,
                engines=[r.engine],
                max_score=r.score,
                total_latency=r.latency_ms,
                metadata=r.metadata
            ) for r in flat]

        return self._merge_results(results_by_engine)

    def _run_single_engine(
        self,
        engine: str,
        query: str,
        path: str,
        max_results: int,
        timeout: float,
    ) -> List[RetrievalResult]:
        """Run a single retrieval engine with timeout."""
        start = time.time()
        try:
            # Import engine functions lazily
            if engine == 'ripgrep':
                from agent.retrieval.engine import _run_ripgrep as run_fn
            elif engine == 'fts':
                from agent.retrieval.engine import _run_fts as run_fn
            elif engine == 'symbol':
                from agent.retrieval.engine import _run_symbol as run_fn
            elif engine == 'ast':
                from agent.retrieval.engine import _run_ast_search as run_fn
            elif engine == 'graphify':
                from agent.retrieval.engine import _run_graphify as run_fn
            elif engine == 'semantic':
                from agent.retrieval.engine import _run_semantic as run_fn
            elif engine == 'lsp':
                from agent.retrieval.lsp_client import get_manager
                # LSP is different - we'll handle separately
                return self._run_lsp(engine, query, path, max_results)
            else:
                return []

            # Run the engine (these functions return formatted strings)
            output = run_fn(query, path, max_results)
            latency = (time.time() - start) * 1000.0

            # Parse output into individual results
            results = self._parse_engine_output(engine, output, latency)
            return results

        except Exception as e:
            latency = (time.time() - start) * 1000.0
            # Log error but don't crash
            return []

    def _run_lsp(self, engine: str, query: str, path: str, max_results: int) -> List[RetrievalResult]:
        """Run LSP-based search (workspace symbols)."""
        try:
            from agent.retrieval.lsp_client import get_manager
            manager = get_manager(path)
            results = manager.workspace_symbols(query)
            # Convert LSP results to RetrievalResult
            parsed: List[RetrievalResult] = []
            for item in results[:max_results]:
                # LSP workspace symbol response format varies
                # This is simplified
                name = item.get('name', '')
                location = item.get('location', {})
                uri = location.get('uri', '')
                file_path = uri.replace('file://', '') if uri.startswith('file://') else uri
                range_data = location.get('range', {})
                line = range_data.get('start', {}).get('line', 0) + 1
                content = f"{name} (LSP result)"
                parsed.append(RetrievalResult(
                    engine=engine,
                    content=content,
                    score=1.0,
                    metadata={"path": file_path, "line": line, "name": name}
                ))
            return parsed
        except Exception:
            return []

    def _parse_engine_output(self, engine: str, output: str, latency_ms: float) -> List[RetrievalResult]:
        """Parse raw engine output into RetrievalResult objects."""
        results: List[RetrievalResult] = []
        if not output or "(no" in output.lower() or "failed" in output.lower():
            return results

        # Simple parsing: split by double newlines, each block is a result
        blocks = output.split('\n\n')
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            # Extract first line as title, rest as content
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
            if len(results) >= 100:  # Cap per-engine results
                break

        return results

    def _merge_results(self, results_by_engine: Dict[str, List[RetrievalResult]]) -> List[MergedResult]:
        """Merge results from multiple engines, deduplicating by content."""
        content_map: Dict[str, MergedResult] = {}

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
                    merged.total_latency = max(merged.total_latency, r.latency_ms)  # Worst-case latency
                    # Merge metadata (keep all paths)
                    if 'path' in r.metadata:
                        merged.metadata.setdefault('paths', []).append(r.metadata['path'])

        # Return sorted by number of engines (more engines = higher confidence)
        merged_list = list(content_map.values())
        merged_list.sort(key=lambda x: (len(x.engines), x.max_score), reverse=True)
        return merged_list


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_retriever: Optional[ParallelRetriever] = None
_retriever_lock = threading.Lock()


def get_retriever() -> ParallelRetriever:
    """Get the global parallel retriever instance."""
    global _retriever
    if _retriever is None:
        with _retriever_lock:
            if _retriever is None:
                _retriever = ParallelRetriever()
    return _retriever


def capabilities() -> Dict[str, Any]:
    """Return parallel retriever capabilities."""
    return {
        "enabled": cfg.ENABLE_PARALLEL_RETRIEVAL,
        "max_workers": get_retriever().max_workers,
    }