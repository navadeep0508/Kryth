"""Intelligent Context Builder - construct minimal useful context for LLM.

Instead of loading 20 files, produce:
- 3 summaries
- 2 symbols
- 1 implementation
- 1 dependency chain

Strategies:
- Symbol + implementation + callers
- Summary + key symbols
- Dependency chain traversal
- Adaptive based on query type

Minimizes token usage while preserving completeness.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.retrieval import config as cfg
from agent.retrieval.cache import get_cache
from agent.retrieval.context_compression import get_generator, FileSummary, FolderSummary
from agent.retrieval.symbol_index import get_index as get_symbol_index
from agent.retrieval.dep_graph import get_graph as get_dep_graph
from agent.retrieval.lsp_client import get_manager as get_lsp_manager


# ---------------------------------------------------------------------------
# Context types
# ---------------------------------------------------------------------------

@dataclass
class ContextPiece:
    """A piece of context to include in the final context."""
    type: str  # 'file', 'summary', 'symbol', 'dependency', 'implementation'
    content: str
    path: Optional[str] = None
    line: Optional[int] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class BuiltContext:
    """Result of context building."""
    pieces: List[ContextPiece]
    total_tokens: int
    strategy: str
    query: str


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

class ContextBuilder:
    """Build minimal context for LLM queries."""

    def __init__(self, directory: str = "."):
        self.directory = os.path.abspath(directory)
        self._cache = get_cache("context_builder")
        self._summary_gen = get_generator(directory)
        self._symbol_index = get_symbol_index(directory)
        self._dep_graph = get_dep_graph(directory)
        self._lsp_manager = get_lsp_manager(directory) if cfg.ENABLE_LSP else None

    def build(
        self,
        query: str,
        query_type: str,
        max_tokens: int = 4000,
        base_path: Optional[str] = None,
    ) -> BuiltContext:
        """Build context for a query.

        Args:
            query: The user query
            query_type: Classified query type (keyword, symbol, etc.)
            max_tokens: Maximum tokens to include
            base_path: Optional base path to restrict context

        Returns:
            BuiltContext with pieces and total token estimate
        """
        # Check cache first
        cache_key = f"context:{query_type}:{query[:100]}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return BuiltContext(**cached)  # Reconstruct

        pieces: List[ContextPiece] = []
        tokens_used = 0

        # Choose strategy based on query type
        if query_type == 'symbol':
            pieces = self._strategy_symbol(query, base_path)
        elif query_type == 'relational':
            pieces = self._strategy_relational(query, base_path)
        elif query_type == 'structural':
            pieces = self._strategy_structural(query, base_path)
        elif query_type == 'docs':
            pieces = self._strategy_docs(query, base_path)
        else:
            pieces = self._strategy_general(query, base_path)

        # Prune to fit token budget
        pieces = self._prune_to_budget(pieces, max_tokens)
        total_tokens = self._estimate_tokens(pieces)

        result = BuiltContext(
            pieces=pieces,
            total_tokens=total_tokens,
            strategy=query_type,
            query=query,
        )

        # Cache result (without large content to save space, just metadata)
        cache_data = {
            "pieces": [(p.type, p.path, p.line, p.metadata) for p in pieces],
            "total_tokens": total_tokens,
            "strategy": result.strategy,
            "query": query,
        }
        self._cache.set(cache_key, cache_data, expire=cfg.CACHE_TTL)

        return result

    def _strategy_symbol(self, query: str, base_path: Optional[str]) -> List[ContextPiece]:
        """Strategy for symbol lookup: find symbol + its definition + references."""
        pieces: List[ContextPiece] = []

        # Try symbol index first
        symbol_name = query.split()[-1]  # crude extraction
        symbols = self._symbol_index.find_by_name(symbol_name, limit=5)

        if symbols:
            for sym in symbols[:3]:
                # Add symbol definition
                pieces.append(ContextPiece(
                    type='symbol',
                    content=f"{sym['type']} {sym['name']} in {sym['file']}:{sym['line']}",
                    path=sym['file'],
                    line=sym['line'],
                    metadata={'type': sym['type'], 'signature': sym.get('signature')}
                ))

                # Try to get implementation (read file snippet)
                snippet = self._read_snippet(sym['file'], sym['line'], context=10)
                if snippet:
                    pieces.append(ContextPiece(
                        type='implementation',
                        content=snippet,
                        path=sym['file'],
                        line=sym['line'],
                        metadata={}
                    ))

                # Try to get references via LSP or dep graph
                refs = self._find_references(sym['file'], sym['line'], sym['name'])
                if refs:
                    ref_summary = f"Found {len(refs)} references in: " + ", ".join(set(r['file'] for r in refs[:5]))
                    pieces.append(ContextPiece(
                        type='references',
                        content=ref_summary,
                        metadata={'count': len(refs)}
                    ))

        return pieces

    def _strategy_relational(self, query: str, base_path: Optional[str]) -> List[ContextPiece]:
        """Strategy for relational queries: dependency chains."""
        pieces: List[ContextPiece] = []

        # Extract file/symbol from query
        # Example: "what imports X" or "who calls Y"
        if 'imports' in query.lower():
            # Find file and its imports
            # This is simplified - would need to parse query better
            target = query.split()[-1]
            # Search in symbol index
            symbols = self._symbol_index.find_by_name(target, limit=1)
            if symbols:
                sym = symbols[0]
                deps = self._dep_graph.get_imports(sym['file'])
                if deps:
                    dep_list = [d['target_file'] for d in deps[:10]]
                    pieces.append(ContextPiece(
                        type='dependency',
                        content=f"{sym['file']} imports: " + ", ".join(dep_list),
                        metadata={'count': len(deps)}
                    ))

        return pieces

    def _strategy_structural(self, query: str, base_path: Optional[str]) -> List[ContextPiece]:
        """Strategy for structural queries: find all X."""
        pieces: List[ContextPiece] = []

        # Use symbol index to find all of a type
        # Example: "find all function" -> query_type already indicates this
        # We can query symbol index by type
        if 'function' in query.lower():
            symbols = self._symbol_index.find_by_type('function', limit=20)
            summary = f"Found {len(symbols)} functions. Top: " + ", ".join(s['name'] for s in symbols[:5])
            pieces.append(ContextPiece(
                type='structural',
                content=summary,
                metadata={'count': len(symbols)}
            ))

        return pieces

    def _strategy_docs(self, query: str, base_path: Optional[str]) -> List[ContextPiece]:
        """Strategy for documentation search: summaries + FTS."""
        pieces: List[ContextPiece] = []

        # Use folder summaries to give overview
        root_summary = self._summary_gen.get_folder_summary(self.directory)
        if root_summary:
            pieces.append(ContextPiece(
                type='summary',
                content=f"Repository: {root_summary.name}, {root_summary.files} files, languages: {', '.join(root_summary.languages.keys())}",
                path=root_summary.path,
                metadata={'type': 'folder'}
            ))

        return pieces

    def _strategy_general(self, query: str, base_path: Optional[str]) -> List[ContextPiece]:
        """General strategy: use summaries and key symbols."""
        pieces: List[ContextPiece] = []

        # Get root folder summary
        root_summary = self._summary_gen.get_folder_summary(self.directory)
        if root_summary:
            pieces.append(ContextPiece(
                type='summary',
                content=f"Root: {root_summary.name}, {root_summary.files} files, key symbols: " + 
                       ", ".join(s['name'] for s in root_summary.key_symbols[:10]),
                path=root_summary.path,
                metadata={'type': 'folder'}
            ))

        return pieces

    def _prune_to_budget(self, pieces: List[ContextPiece], max_tokens: int) -> List[ContextPiece]:
        """Prune pieces to fit token budget."""
        # Simple: keep adding until budget exceeded
        kept: List[ContextPiece] = []
        tokens = 0
        for piece in pieces:
            piece_tokens = len(piece.content.split()) * 1.3  # Rough estimate
            if tokens + piece_tokens > max_tokens:
                break
            kept.append(piece)
            tokens += piece_tokens
        return kept

    def _estimate_tokens(self, pieces: List[ContextPiece]) -> int:
        """Estimate total tokens in pieces."""
        total = 0
        for piece in pieces:
            total += len(piece.content.split()) * 1.3
        return int(total)

    def _read_snippet(self, path: str, line: int, context: int = 5) -> Optional[str]:
        """Read a snippet of code around a line."""
        try:
            content = read_file(path, limit=line + context)
            if not content:
                return None
            lines = content.split('\n')
            start = max(0, line - context - 1)
            end = min(len(lines), line + context)
            snippet_lines = lines[start:end]
            snippet = '\n'.join(snippet_lines)
            return snippet
        except Exception:
            return None

    def _find_references(self, path: str, line: int, symbol: str) -> List[Dict[str, Any]]:
        """Find references to a symbol using LSP or dep graph."""
        refs: List[Dict[str, Any]] = []

        if self._lsp_manager:
            try:
                # LSP expects 0-indexed line and character; we don't have character
                refs = self._lsp_manager.find_references(path, line, 0)
            except Exception:
                pass

        if not refs:
            # Fallback to dep graph
            refs = self._dep_graph.find_callers(symbol, file=path)

        return refs


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_builder: Optional[ContextBuilder] = None
_builder_lock = threading.Lock()


def get_builder(directory: str = ".") -> ContextBuilder:
    """Get or create the context builder for a directory."""
    global _builder
    if _builder is None:
        with _builder_lock:
            if _builder is None:
                _builder = ContextBuilder(directory)
    return _builder


def capabilities() -> Dict[str, Any]:
    """Return context builder capabilities."""
    return {
        "enabled": cfg.ENABLE_CONTEXT_COMPRESSION,
        "has_summaries": True,
        "has_symbol_index": cfg.ENABLE_SYMBOL_INDEX,
        "has_dep_graph": cfg.ENABLE_DEP_GRAPH,
        "has_lsp": cfg.ENABLE_LSP,
    }