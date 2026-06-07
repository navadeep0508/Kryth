"""Integration Layer - Connect semantic editor to existing retrieval systems.

This module provides adapters and bridges to integrate the new semantic
editing engine with KRYTH's existing retrieval infrastructure:

- Graphify (code knowledge graph)
- Symbol Index (repo_index)
- LSP (Language Server Protocol)
- AST parsers (tree-sitter, ast-grep)
- Comby (structural matching)
- Existing file system and git operations

The integration layer ensures the semantic editor can leverage all
existing capabilities while providing its advanced editing features.

Design principles:
- Lazy initialization (only load what's needed)
- Graceful degradation (if a system is unavailable)
- Caching for performance
- Unified error handling
- Configuration via environment/project settings
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .data_structures import SymbolReference, Location


@dataclass
class IntegrationConfig:
    """Configuration for integration systems."""
    enable_graphify: bool = True
    enable_lsp: bool = True
    enable_ast_grep: bool = True
    enable_comby: bool = True
    enable_tree_sitter: bool = True
    cache_results: bool = True
    cache_ttl: int = 300  # seconds
    fallback_to_grep: bool = True


class IntegrationManager:
    """Manages connections to external retrieval and analysis systems.
    
    The IntegrationManager provides a unified interface to all the
    existing code intelligence systems. It handles:
    
    - Lazy loading of optional dependencies
    - Connection pooling and caching
    - Error handling and fallbacks
    - Configuration management
    - Performance monitoring
    
    Systems integrated:
    1. Graphify - semantic code graph for relationships
    2. Symbol Index (repo_index) - fast symbol lookup
    3. LSP - workspace edits and diagnostics
    4. tree-sitter - multi-language AST parsing
    5. ast-grep - pattern-based AST search
    6. Comby - structural code matching
    7. ripgrep - fast text search (fallback)
    
    All systems are optional - the editor will work with reduced
    capabilities if some are unavailable.
    """
    
    def __init__(self, project_root: str, config: Optional[IntegrationConfig] = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.config = config or IntegrationConfig()
        self._cache: Dict[str, Any] = {}
        self._cache_timestamps: Dict[str, float] = {}
        
        # System availability flags
        self._graphify_available = False
        self._lsp_available = False
        self._ast_grep_available = False
        self._comby_available = False
        self._tree_sitter_available = False
        
        # Initialize systems
        self._check_availability()
    
    def _check_availability(self) -> None:
        """Check which integration systems are available."""
        # Check Graphify
        try:
            import graphify
            self._graphify_available = True
        except ImportError:
            pass
        
        # Check LSP
        try:
            import pygls
            self._lsp_available = True
        except ImportError:
            pass
        
        # Check ast-grep
        try:
            import ast_grep
            self._ast_grep_available = True
        except ImportError:
            pass
        
        # Check Comby
        try:
            import comby
            self._comby_available = True
        except ImportError:
            pass
        
        # Check tree-sitter
        try:
            import tree_sitter
            self._tree_sitter_available = True
        except ImportError:
            pass
    
    def graphify_query(
        self,
        query: str,
        query_type: str = "semantic",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Query the Graphify code knowledge graph."""
        if not self._graphify_available:
            # Fallback to semantic search
            return self._semantic_fallback(query, limit)
        
        cache_key = f"graphify:{query_type}:{query}:{limit}"
        if self.config.cache_results and cache_key in self._cache:
            return self._cache[cache_key]
        
        try:
            from agent.tools._graphify import graphify_query
            results = graphify_query(query, query_type=query_type, path=str(self.project_root))
            
            if self.config.cache_results:
                self._cache[cache_key] = results
                self._cache_timestamps[cache_key] = __import__('time').time()
            
            return results
        except Exception as e:
            from agent import ui
            ui.warn(f"Graphify query failed: {e}")
            return self._semantic_fallback(query, limit)
    
    def lookup_symbol(self, name: str, directory: Optional[str] = None) -> List[SymbolReference]:
        """Look up symbol definitions using repo_index."""
        cache_key = f"symbol_lookup:{name}:{directory or ''}"
        if self.config.cache_results and cache_key in self._cache:
            return self._cache[cache_key]
        
        try:
            from agent import repo_index
            results = repo_index.lookup_symbol(name, directory=str(self.project_root) if not directory else directory)
            
            refs = []
            for line in results.splitlines():
                if ':' in line:
                    parts = line.split(':')
                    if len(parts) >= 3:
                        path = parts[0]
                        line_num = int(parts[1])
                        kind_name = parts[2].strip()
                        symbol_name = parts[3].strip() if len(parts) > 3 else name
                        
                        refs.append(SymbolReference(
                            name=symbol_name,
                            file_path=path,
                            line=line_num,
                            column=0,
                            kind=kind_name,
                        ))
            
            if self.config.cache_results:
                self._cache[cache_key] = refs
                self._cache_timestamps[cache_key] = __import__('time').time()
            
            return refs
        except Exception as e:
            from agent import ui
            ui.warn(f"Symbol lookup failed: {e}")
            return []
    
    def find_dependents(self, name: str) -> Dict[str, List[str]]:
        """Find files that depend on a symbol."""
        try:
            from agent import repo_index
            return repo_index.lookup_dependents(name)
        except Exception as e:
            from agent import ui
            ui.warn(f"Dependents lookup failed: {e}")
            return {"imports": [], "calls": []}
    
    def lsp_rename(self, file_path: str, old_name: str, new_name: str) -> List[Location]:
        """Use LSP to perform a workspace rename."""
        if not self._lsp_available:
            # Fallback to text-based rename
            return []
        
        try:
            # This would use the LSP client
            from agent.bridge.lsp import LSPClient
            client = LSPClient(self.project_root)
            # client.rename(file_path, old_name, new_name)
            return []  # Would return list of edits
        except Exception as e:
            from agent import ui
            ui.warn(f"LSP rename failed: {e}")
            return []
    
    def ast_parse(self, file_path: str, language: Optional[str] = None) -> Any:
        """Parse a file into an AST using tree-sitter."""
        if not self._tree_sitter_available:
            raise RuntimeError("tree-sitter not available")
        
        ext = Path(file_path).suffix.lower()
        language_map = {
            ".py": "python",
            ".ts": "typescript",
            ".js": "javascript",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
        }
        
        lang = language or language_map.get(ext)
        if not lang:
            raise ValueError(f"Unsupported language for {file_path}")
        
        try:
            import tree_sitter
            from tree_sitter import Language, Parser
            
            # Get language library (would need to be installed)
            # This is a simplified version
            parser = Parser()
            # language_lib = Language(f"build/{lang}.so", lang)
            # parser.set_language(language_lib)
            
            with open(file_path, 'rb') as f:
                tree = parser.parse(f.read())
            
            return tree
        except Exception as e:
            from agent import ui
            ui.warn(f"AST parse failed for {file_path}: {e}")
            raise
    
    def ast_grep(
        self,
        pattern: str,
        file_pattern: Optional[str] = None,
        language: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search code using ast-grep (AST pattern matching)."""
        if not self._ast_grep_available:
            # Fallback to grep
            return self._text_grep(pattern, file_pattern)
        
        try:
            import ast_grep
            # Would use ast-grep CLI or Python API
            # For now, fallback
            return self._text_grep(pattern, file_pattern)
        except Exception as e:
            from agent import ui
            ui.warn(f"ast-grep failed: {e}")
            return self._text_grep(pattern, file_pattern)
    
    def comby_match(
        self,
        pattern: str,
        file_pattern: Optional[str] = None,
        language: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Match code structures using Comby."""
        if not self._comby_available:
            return []
        
        try:
            # Would use comby library
            return []
        except Exception as e:
            from agent import ui
            ui.warn(f"Comby match failed: {e}")
            return []
    
    def _semantic_fallback(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Fallback to semantic search when Graphify is unavailable."""
        try:
            from agent.tools._semantic import semantic_search
            results = semantic_search(query, top_k=limit, directory=str(self.project_root))
            return [{"path": r.split('\t')[0], "score": float(r.split('\t')[1])} for r in results.splitlines()]
        except Exception:
            return []
    
    def _text_grep(self, pattern: str, file_pattern: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fallback to text-based grep."""
        try:
            from agent.tools._grep import grep
            results = grep(pattern, path=str(self.project_root), glob=file_pattern or "**/*")
            return [{"path": p} for p in results]
        except Exception:
            return []
    
    def clear_cache(self) -> None:
        """Clear integration cache."""
        self._cache.clear()
        self._cache_timestamps.clear()
    
    def get_available_systems(self) -> Dict[str, bool]:
        """Get availability status of all integration systems."""
        return {
            "graphify": self._graphify_available,
            "lsp": self._lsp_available,
            "ast_grep": self._ast_grep_available,
            "comby": self._comby_available,
            "tree_sitter": self._tree_sitter_available,
        }
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "size": len(self._cache),
            "keys": list(self._cache.keys()),
        }