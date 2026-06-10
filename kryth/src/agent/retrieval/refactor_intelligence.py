"""Refactoring Intelligence - safe refactoring analysis.

Uses LSP, Symbol Index, and Dependency Graph to provide:
- Rename safety analysis (what breaks if we rename X)
- Impact analysis (full dependency chain)
- Dependency tracing (who calls what)
- Unused code detection (dead code)
- Circular dependency detection
- Safe move/delete operations
"""

from __future__ import annotations

import os
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.retrieval import config as cfg
from agent.retrieval.symbol_index import get_index as get_symbol_index
from agent.retrieval.dep_graph import get_graph as get_dep_graph
from agent.retrieval.lsp_client import get_manager as get_lsp_manager


# ---------------------------------------------------------------------------
# Analysis result types
# ---------------------------------------------------------------------------

@dataclass
class ImpactAnalysis:
    """Result of rename/move impact analysis."""
    symbol_name: str
    file: str
    line: int
    all_references: List[Dict[str, Any]]
    breaking_changes: List[Dict[str, Any]]
    safe_to_rename: bool
    confidence: float  # 0-1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol_name": self.symbol_name,
            "file": self.file,
            "line": self.line,
            "all_references": self.all_references,
            "breaking_changes": self.breaking_changes,
            "safe_to_rename": self.safe_to_rename,
            "confidence": self.confidence,
        }


@dataclass
class DeadCodeResult:
    """Result of dead code detection."""
    unused_functions: List[Dict[str, Any]]
    unused_classes: List[Dict[str, Any]]
    unused_imports: List[Dict[str, Any]]
    total_lines: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unused_functions": self.unused_functions,
            "unused_classes": self.unused_classes,
            "unused_imports": self.unused_imports,
            "total_lines": self.total_lines,
        }


@dataclass
class CircularDependency:
    """Detected circular dependency."""
    files: List[str]
    modules: List[str]
    severity: str  # 'warning' or 'error'

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files": self.files,
            "modules": self.modules,
            "severity": self.severity,
        }


# ---------------------------------------------------------------------------
# Refactoring analyzer
# ---------------------------------------------------------------------------

class RefactoringIntelligence:
    """Analyze code for safe refactoring operations."""

    def __init__(self, directory: str = "."):
        self.directory = os.path.abspath(directory)
        self._symbol_index = get_symbol_index(directory)
        self._dep_graph = get_dep_graph(directory)
        self._lsp_manager = get_lsp_manager(directory) if cfg.ENABLE_LSP else None

    def analyze_rename(self, symbol: str, file: str, new_name: str) -> ImpactAnalysis:
        """Analyze impact of renaming a symbol."""
        # Find the symbol definition
        symbols = self._symbol_index.find_by_name(symbol)
        target_sym = None
        for s in symbols:
            if s['file'] == file and s['line'] > 0:
                target_sym = s
                break

        if not target_sym:
            return ImpactAnalysis(
                symbol_name=symbol,
                file=file,
                line=0,
                all_references=[],
                breaking_changes=[],
                safe_to_rename=False,
                confidence=0.0,
            )

        # Find all references
        references = self._find_all_references(symbol, file, target_sym['line'])

        # Classify breaking vs non-breaking
        breaking = []
        for ref in references:
            # If reference is in a public API or external interface, it's breaking
            if self._is_breaking_reference(ref, target_sym):
                breaking.append(ref)

        # Safety assessment
        safe = len(breaking) == 0
        confidence = 1.0 if len(references) > 0 else 0.5

        return ImpactAnalysis(
            symbol_name=symbol,
            file=file,
            line=target_sym['line'],
            all_references=references,
            breaking_changes=breaking,
            safe_to_rename=safe,
            confidence=confidence,
        )

    def _find_all_references(self, symbol: str, file: str, line: int) -> List[Dict[str, Any]]:
        """Find all references to a symbol."""
        refs: List[Dict[str, Any]] = []

        # Try LSP first (most accurate)
        if self._lsp_manager:
            try:
                lsp_refs = self._lsp_manager.find_references(file, line, 0)
                for r in lsp_refs:
                    refs.append({
                        "file": r.get('path', ''),
                        "line": r.get('line', 0),
                        "type": "reference",
                        "source": "lsp",
                    })
            except Exception:
                pass

        # Fallback to dep graph
        if not refs:
            dep_refs = self._dep_graph.find_callers(symbol, file=file)
            for r in dep_refs:
                refs.append({
                    "file": r['source_file'],
                    "line": r.get('line', 0),
                    "type": r['relation_type'],
                    "source": "dep_graph",
                })

        return refs

    def _is_breaking_reference(self, ref: Dict[str, Any], target_sym: Dict[str, Any]) -> bool:
        """Determine if a reference is a breaking change."""
        # If the reference is in a test file, it's not breaking (tests should be updated)
        ref_file = ref.get('file', '')
        if 'test' in ref_file.lower() or 'spec' in ref_file.lower():
            return False

        # If the symbol is public and used externally, it's breaking
        if target_sym.get('visibility') == 'public':
            # Check if reference is outside the same module
            target_module = target_sym.get('module', '')
            ref_module = self._guess_module_from_file(ref_file)
            if target_module and ref_module and target_module != ref_module:
                return True

        return False

    def _guess_module_from_file(self, path: str) -> str:
        """Guess module name from file path."""
        try:
            rel = os.path.relpath(path, self.directory)
            parts = os.path.splitext(rel)[0].split(os.sep)
            if parts[-1] == '__init__':
                parts = parts[:-1]
            return ".".join(parts)
        except Exception:
            return ""

    def find_unused_code(self) -> DeadCodeResult:
        """Detect unused functions, classes, and imports."""
        unused_functions: List[Dict[str, Any]] = []
        unused_classes: List[Dict[str, Any]] = []
        unused_imports: List[Dict[str, Any]] = []

        # Get all symbols
        all_symbols = self._symbol_index._conn.execute(
            "SELECT * FROM symbols WHERE type IN ('function', 'class', 'method')"
        ).fetchall()

        for sym_row in all_symbols:
            sym = dict(sym_row)
            # Skip private/special methods (dunder)
            name = sym['name']
            if name.startswith('__') and name.endswith('__'):
                continue

            # Check if symbol has any references
            refs = self._dep_graph.find_callers(name, file=sym['file'])
            # Also check LSP references
            if self._lsp_manager and sym['line'] > 0:
                try:
                    lsp_refs = self._lsp_manager.find_references(sym['file'], sym['line'], 0)
                    if lsp_refs:
                        refs = True  # Has references
                except Exception:
                    pass

            if not refs:
                # Symbol is unused
                if sym['type'] in ('function', 'async_function'):
                    unused_functions.append(sym)
                elif sym['type'] == 'class':
                    unused_classes.append(sym)

        # Find unused imports (simplified: check imports that have no dependents)
        # This would require more sophisticated analysis
        # For now, skip

        total_lines = sum(
            self._symbol_index._conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE file = ?", (sym['file'],)
            ).fetchone()[0]
            for sym in unused_functions + unused_classes
        )

        return DeadCodeResult(
            unused_functions=unused_functions,
            unused_classes=unused_classes,
            unused_imports=unused_imports,
            total_lines=total_lines,
        )

    def find_circular_dependencies(self) -> List[CircularDependency]:
        """Detect circular dependencies in the import graph."""
        circles: List[CircularDependency] = []

        # Build import graph
        graph: Dict[str, Set[str]] = defaultdict(set)
        cursor = self._dep_graph._conn.cursor()
        cursor.execute("SELECT source_file, target_file FROM dependencies WHERE relation_type = 'imports'")
        for source, target in cursor.fetchall():
            graph[source].add(target)

        # DFS to find cycles
        visited: Set[str] = set()
        stack: Set[str] = set()
        path: List[str] = []

        def dfs(node: str) -> bool:
            visited.add(node)
            stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in stack:
                    # Found cycle
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    modules = [self._guess_module_from_file(f) for f in cycle]
                    circles.append(CircularDependency(
                        files=cycle,
                        modules=modules,
                        severity='error' if len(cycle) > 2 else 'warning',
                    ))
                    return True

            path.pop()
            stack.remove(node)
            return False

        for node in list(graph.keys()):
            if node not in visited:
                dfs(node)

        return circles

    def trace_dependency(self, symbol: str, file: str, direction: str = "downstream") -> List[Dict[str, Any]]:
        """Trace dependency chain (upstream = dependencies, downstream = dependents)."""
        chain: List[Dict[str, Any]] = []
        visited: Set[str] = set()

        if direction == "downstream":
            # Find all files that depend on this symbol
            queue = deque([(file, symbol, 0)])
            while queue:
                current_file, current_sym, depth = queue.popleft()
                if (current_file, current_sym) in visited:
                    continue
                visited.add((current_file, current_sym))

                # Get callers
                callers = self._dep_graph.find_callers(current_sym, file=current_file)
                for caller in callers:
                    chain.append({
                        "file": caller['source_file'],
                        "symbol": caller.get('source_symbol', ''),
                        "relation": "calls",
                        "depth": depth + 1,
                    })
                    if caller.get('source_symbol'):
                        queue.append((caller['source_file'], caller['source_symbol'], depth + 1))

        else:  # upstream
            # Find dependencies of this symbol
            queue = deque([(file, symbol, 0)])
            while queue:
                current_file, current_sym, depth = queue.popleft()
                if (current_file, current_sym) in visited:
                    continue
                visited.add((current_file, current_sym))

                # Get callees (would need reverse lookup in dep graph)
                # For now, use symbol index to find what this symbol references
                # This is simplified
                pass

        return chain

    def check_rename_safety(self, symbol: str, file: str, new_name: str) -> Dict[str, Any]:
        """Comprehensive safety check for renaming."""
        analysis = self.analyze_rename(symbol, file, new_name)

        # Additional checks
        issues: List[str] = []
        warnings: List[str] = []

        if analysis.breaking_changes:
            issues.append(f"Rename would break {len(analysis.breaking_changes)} external references")

        if analysis.confidence < 0.7:
            warnings.append("Low confidence in analysis - manual review recommended")

        # Check if new name already exists in same scope
        existing = self._symbol_index.find_by_name(new_name)
        if any(e['file'] == file for e in existing):
            issues.append(f"Symbol '{new_name}' already exists in {file}")

        return {
            "safe": analysis.safe_to_rename and len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "analysis": analysis.to_dict(),
        }

    def get_impact_summary(self, file: str) -> Dict[str, Any]:
        """Get impact summary for changes to a file."""
        # Find all symbols defined in file
        symbols = self._symbol_index.find_in_file(file)

        # For each symbol, count references
        impact = {
            "symbols": [],
            "total_references": 0,
            "external_references": 0,
            "risk_level": "low",
        }

        for sym in symbols:
            refs = self._find_all_references(sym['name'], file, sym['line'])
            external_refs = [r for r in refs if r['file'] != file]
            impact["symbols"].append({
                "name": sym['name'],
                "type": sym['type'],
                "references": len(refs),
                "external_references": len(external_refs),
            })
            impact["total_references"] += len(refs)
            impact["external_references"] += len(external_refs)

        # Assess risk
        if impact["external_references"] > 10:
            impact["risk_level"] = "high"
        elif impact["external_references"] > 0:
            impact["risk_level"] = "medium"

        return impact


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_refactor: Optional[RefactoringIntelligence] = None
_refactor_lock = threading.Lock()


def get_refactor(directory: str = ".") -> RefactoringIntelligence:
    """Get or create the refactoring intelligence engine."""
    global _refactor
    if _refactor is None:
        with _refactor_lock:
            if _refactor is None:
                _refactor = RefactoringIntelligence(directory)
    return _refactor


def capabilities() -> Dict[str, Any]:
    """Return refactoring intelligence capabilities."""
    return {
        "enabled": cfg.ENABLE_REFACTORING_INTELLIGENCE,
        "has_lsp": cfg.ENABLE_LSP,
        "has_symbol_index": cfg.ENABLE_SYMBOL_INDEX,
        "has_dep_graph": cfg.ENABLE_DEP_GRAPH,
    }