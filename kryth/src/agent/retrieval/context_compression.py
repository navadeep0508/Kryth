"""Context Compression Layer - generate repository summaries.

Instead of loading 20 files, load one compressed summary.

Creates hierarchical summaries:
- File summary (responsibilities, key functions, dependencies)
- Folder summary (aggregated from files)
- Module summary (aggregated from files in module)
- Package summary (aggregated from modules)

All summaries are cached and incrementally updated.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from agent.retrieval import config as cfg
from agent.retrieval.cache import get_cache, file_fingerprint
from agent.retrieval.file_reader import read_file


# ---------------------------------------------------------------------------
# Summary data structures
# ---------------------------------------------------------------------------

@dataclass
class FileSummary:
    """Summary of a single file."""
    path: str
    language: str
    size: int
    responsibilities: List[str] = field(default_factory=list)
    key_symbols: List[Dict[str, Any]] = field(default_factory=list)  # name, type, line
    imports: List[str] = field(default_factory=list)
    exports: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)  # file paths
    docstring: Optional[str] = None
    last_updated: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "size": self.size,
            "responsibilities": self.responsibilities,
            "key_symbols": self.key_symbols,
            "imports": self.imports,
            "exports": self.exports,
            "dependencies": self.dependencies,
            "docstring": self.docstring,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileSummary":
        return cls(**data)


@dataclass
class FolderSummary:
    """Summary of a folder/package."""
    path: str
    name: str
    files: int
    subfolders: int
    languages: Dict[str, int] = field(default_factory=dict)
    key_symbols: List[Dict[str, Any]] = field(default_factory=list)
    modules: List[str] = field(default_factory=list)
    responsibilities: List[str] = field(default_factory=list)
    last_updated: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "files": self.files,
            "subfolders": self.subfolders,
            "languages": self.languages,
            "key_symbols": self.key_symbols,
            "modules": self.modules,
            "responsibilities": self.responsibilities,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FolderSummary":
        return cls(**data)


# ---------------------------------------------------------------------------
# Summary generator
# ---------------------------------------------------------------------------

class SummaryGenerator:
    """Generate and cache file/folder summaries."""

    def __init__(self, directory: str = "."):
        self.directory = os.path.abspath(directory)
        self._cache = get_cache("summaries")
        self._file_cache: Dict[str, FileSummary] = {}
        self._folder_cache: Dict[str, FolderSummary] = {}

    def get_file_summary(self, path: str, force_rebuild: bool = False) -> Optional[FileSummary]:
        """Get or build summary for a file."""
        if not os.path.isfile(path):
            return None

        rel_path = os.path.relpath(path, self.directory)
        cache_key = f"file_summary:{rel_path}"

        # Check cache
        if not force_rebuild:
            cached = self._cache.get(cache_key)
            if cached is not None:
                # Verify file hasn't changed
                current_fp = file_fingerprint(path)
                if cached.get('file_hash') == current_fp:
                    return FileSummary.from_dict(cached)
                # Cache stale, rebuild

        # Build summary
        summary = self._build_file_summary(path)
        if summary is None:
            return None

        # Cache it
        data = summary.to_dict()
        data['file_hash'] = file_fingerprint(path)
        self._cache.set(cache_key, data, expire=cfg.CACHE_TTL)
        self._file_cache[rel_path] = summary
        return summary

    def get_folder_summary(self, path: str, force_rebuild: bool = False) -> Optional[FolderSummary]:
        """Get or build summary for a folder."""
        if not os.path.isdir(path):
            return None

        rel_path = os.path.relpath(path, self.directory)
        cache_key = f"folder_summary:{rel_path}"

        if not force_rebuild:
            cached = self._cache.get(cache_key)
            if cached is not None:
                # Check if any file in folder changed (simplified: check folder mtime)
                try:
                    folder_mtime = os.path.getmtime(path)
                    if cached.get('folder_mtime') == folder_mtime:
                        return FolderSummary.from_dict(cached)
                except Exception:
                    pass

        # Build summary
        summary = self._build_folder_summary(path)
        if summary is None:
            return None

        data = summary.to_dict()
        try:
            data['folder_mtime'] = os.path.getmtime(path)
        except Exception:
            data['folder_mtime'] = 0.0
        self._cache.set(cache_key, data, expire=cfg.CACHE_TTL)
        self._folder_cache[rel_path] = summary
        return summary

    def _build_file_summary(self, path: str) -> Optional[FileSummary]:
        """Analyze a file and produce a summary."""
        try:
            content = read_file(path, limit=200)  # Read first 200 lines
            if not content:
                return None

            # Basic info
            stat = os.stat(path)
            ext = os.path.splitext(path)[1].lower()
            language = self._guess_language(ext)

            # Extract symbols (simple regex-based for now)
            symbols = self._extract_symbols_simple(content, language)

            # Extract imports
            imports = self._extract_imports_simple(content, language)

            # Guess responsibilities from comments and docstring
            responsibilities = self._extract_responsibilities(content)

            # Extract module-level exports (for Python __all__, etc.)
            exports = self._extract_exports(content, language)

            summary = FileSummary(
                path=path,
                language=language,
                size=stat.st_size,
                responsibilities=responsibilities,
                key_symbols=symbols[:20],  # Limit to top 20
                imports=imports[:50],
                exports=exports[:50],
                dependencies=[],  # Would need dep graph
                docstring=self._extract_top_docstring(content),
                last_updated=stat.st_mtime,
            )
            return summary
        except Exception:
            return None

    def _build_folder_summary(self, path: str) -> Optional[FolderSummary]:
        """Aggregate summaries from files in a folder."""
        try:
            files = []
            subdirs = []
            language_counts: Dict[str, int] = {}
            all_symbols: List[Dict[str, Any]] = []
            all_imports: Set[str] = set()
            responsibilities: List[str] = []

            for root, dirs, filenames in os.walk(path):
                # Skip hidden and common ignore dirs
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('__pycache__', 'node_modules', 'venv', '.venv', 'target', 'build', 'dist')]
                for fname in filenames:
                    if fname.startswith('.'):
                        continue
                    fpath = os.path.join(root, fname)
                    files.append(fpath)
                    # Get file summary (cached)
                    fsum = self.get_file_summary(fpath)
                    if fsum:
                        language_counts[fsum.language] = language_counts.get(fsum.language, 0) + 1
                        all_symbols.extend(fsum.key_symbols)
                        all_imports.update(fsum.imports)
                        responsibilities.extend(fsum.responsibilities)

            # Count subdirectories (top-level only)
            try:
                with os.scandir(path) as it:
                    subdirs = [entry.name for entry in it if entry.is_dir() and not entry.name.startswith('.')]
            except Exception:
                subdirs = []

            # Deduplicate and limit
            unique_symbols = {s['name']: s for s in all_symbols}.values()
            top_symbols = sorted(unique_symbols, key=lambda s: s.get('line', 0))[:50]

            summary = FolderSummary(
                path=path,
                name=os.path.basename(path) or path,
                files=len(files),
                subfolders=len(subdirs),
                languages=language_counts,
                key_symbols=list(top_symbols),
                modules=self._guess_modules(path, files),
                responsibilities=list(set(responsibilities))[:20],
                last_updated=time.time(),
            )
            return summary
        except Exception:
            return None

    def _guess_language(self, ext: str) -> str:
        """Guess language from file extension."""
        mapping = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'javascript',
            '.tsx': 'typescript',
            '.java': 'java',
            '.go': 'go',
            '.rs': 'rust',
            '.c': 'c',
            '.cpp': 'cpp',
            '.h': 'c',
            '.hpp': 'cpp',
            '.rb': 'ruby',
            '.php': 'php',
            '.swift': 'swift',
            '.kt': 'kotlin',
            '.scala': 'scala',
            '.ml': 'ocaml',
        }
        return mapping.get(ext, 'unknown')

    def _extract_symbols_simple(self, content: str, language: str) -> List[Dict[str, Any]]:
        """Simple symbol extraction using regex (fallback)."""
        symbols = []
        lines = content.split('\n')
        if language == 'python':
            import re
            func_pattern = re.compile(r'^(\s*)(async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(')
            class_pattern = re.compile(r'^(\s*)class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\(|:)')
            for i, line in enumerate(lines, 1):
                func_match = func_pattern.match(line)
                if func_match:
                    indent = len(func_match.group(1))
                    name = func_match.group(3)
                    symbols.append({"name": name, "type": "function", "line": i, "indent": indent})
                class_match = class_pattern.match(line)
                if class_match:
                    indent = len(class_match.group(1))
                    name = class_match.group(2)
                    symbols.append({"name": name, "type": "class", "line": i, "indent": indent})
        return symbols

    def _extract_imports_simple(self, content: str, language: str) -> List[str]:
        """Extract import statements."""
        imports = []
        if language == 'python':
            import re
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('import ') or line.startswith('from '):
                    imports.append(line)
        return imports

    def _extract_exports(self, content: str, language: str) -> List[str]:
        """Extract exported symbols."""
        exports = []
        if language == 'python':
            # Look for __all__ = [...]
            import re
            match = re.search(r'__all__\s*=\s*\[(.*?)\]', content, re.DOTALL)
            if match:
                items = re.findall(r'[\'"]([^\'"]+)[\'"]', match.group(1))
                exports = items
        return exports

    def _extract_responsibilities(self, content: str) -> List[str]:
        """Guess file responsibilities from comments and docstrings."""
        responsibilities = []
        lines = content.split('\n')[:50]  # First 50 lines
        in_docstring = False
        docstring_lines = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if not in_docstring:
                    in_docstring = True
                    docstring_lines.append(stripped[3:])
                else:
                    in_docstring = False
                    break
            elif in_docstring:
                docstring_lines.append(stripped)

        if docstring_lines:
            # Extract first sentence or two
            text = ' '.join(docstring_lines)
            sentences = text.split('. ')[:2]
            responsibilities.extend([s.strip() + '.' for s in sentences if s])

        # Also look for high-level comments
        for line in lines[:20]:
            if line.strip().startswith('#') and len(line.strip()) > 5:
                comment = line.strip()[1:].strip()
                if len(comment) < 100:
                    responsibilities.append(comment)

        return list(set(responsibilities))[:5]

    def _extract_top_docstring(self, content: str) -> Optional[str]:
        """Extract the top-level module/class docstring."""
        # Simplified: look for triple-quoted string at the start
        import re
        match = re.search(r'(["\']{3})(.*?)\1', content, re.DOTALL)
        if match:
            return match.group(2).strip()[:500]
        return None

    def _guess_modules(self, folder_path: str, files: List[str]) -> List[str]:
        """Guess module names from files in folder."""
        modules = set()
        for fpath in files:
            rel = os.path.relpath(fpath, folder_path)
            if rel.endswith('__init__.py'):
                modules.add(os.path.dirname(rel).replace(os.sep, '.'))
            elif rel.endswith('.py'):
                mod = os.path.splitext(rel)[0].replace(os.sep, '.')
                modules.add(mod)
        return sorted(list(modules))

    def clear_cache(self) -> None:
        """Clear all summary caches."""
        self._cache.clear()
        self._file_cache.clear()
        self._folder_cache.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_generator: Optional[SummaryGenerator] = None
_generator_lock = threading.Lock()


def get_generator(directory: str = ".") -> SummaryGenerator:
    """Get or create the summary generator for a directory."""
    global _generator
    if _generator is None:
        with _generator_lock:
            if _generator is None:
                _generator = SummaryGenerator(directory)
    return _generator


def capabilities() -> Dict[str, Any]:
    """Return context compression capabilities."""
    return {
        "enabled": cfg.ENABLE_CONTEXT_COMPRESSION,
        "has_cache": True,
    }