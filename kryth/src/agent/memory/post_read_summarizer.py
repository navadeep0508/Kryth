"""
Post-Read Summarizer — extracts semantic summaries from file content.
Replaces the old file cache with pure semantic memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class FileSummary:
    """Semantic summary of a file — no full content stored."""
    path: str
    purpose: str = ""
    language: str = ""
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    file_hash: str = ""
    read_count: int = 0
    lines: int = 0

    def to_context_block(self) -> str:
        """Generate a compact context block for LLM injection."""
        parts = [f"📄 {self.path} ({self.language}, {self.lines} lines, read #{self.read_count})"]
        if self.purpose:
            parts.append(f"   Purpose: {self.purpose}")
        if self.functions:
            parts.append(f"   Functions: {', '.join(self.functions[:10])}")
        if self.classes:
            parts.append(f"   Classes: {', '.join(self.classes[:8])}")
        if self.routes:
            parts.append(f"   Routes: {', '.join(self.routes[:10])}")
        if self.endpoints:
            parts.append(f"   Endpoints: {', '.join(self.endpoints[:10])}")
        if self.imports:
            parts.append(f"   Imports: {', '.join(self.imports[:8])}")
        return "\n".join(parts)


class PostReadSummarizer:
    """Extracts semantic information from file content after read."""

    # Language-specific patterns
    PATTERNS = {
        "python": {
            "functions": r'^\s*(?:async\s+)?def\s+(\w+)\s*\([^)]*\)(?:\s*->\s*[^:]+)?\s*:',
            "classes": r'^\s*class\s+(\w+)\s*[\(:]',
            "imports": r'^(?:from\s+(\S+)\s+)?import\s+(.+)$',
            "routes": r'@app\.(route|get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
            "endpoints": r'@(?:app|router|api)\.(?:get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
        },
        "javascript": {
            "functions": r'(?:function\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s*)?\(|(\w+)\s*:\s*(?:async\s*)?\()',
            "classes": r'class\s+(\w+)',
            "imports": r'import\s+(?:.+?\s+from\s+)?["\']([^"\']+)["\']',
            "routes": r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
            "endpoints": r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
        },
        "typescript": {
            "functions": r'(?:function\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s*)?\(|(\w+)\s*:\s*(?:async\s*)?\()',
            "classes": r'class\s+(\w+)',
            "imports": r'import\s+(?:.+?\s+from\s+)?["\']([^"\']+)["\']',
            "routes": r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
            "endpoints": r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
        },
        "html": {
            "functions": [],
            "classes": [],
            "imports": [],
            "routes": [],
            "endpoints": [],
        },
        "css": {
            "functions": [],
            "classes": [],
            "imports": [],
            "routes": [],
            "endpoints": [],
        },
        "json": {
            "functions": [],
            "classes": [],
            "imports": [],
            "routes": [],
            "endpoints": [],
        },
    }

    def __init__(self):
        self._cache: dict[str, FileSummary] = {}

    def _detect_language(self, path: str, content: str) -> str:
        """Detect file language from extension."""
        ext = os.path.splitext(path)[1].lower()
        lang_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".html": "html",
            ".htm": "html",
            ".css": "css",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".md": "markdown",
            ".txt": "text",
        }
        return lang_map.get(ext, "text")

    def _hash_file(self, path: str) -> str:
        """Compute file hash for change detection."""
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()[:16]
        except Exception:
            return ""

    def _extract_purpose(self, path: str, content: str, lang: str) -> str:
        """Extract file purpose from docstrings, comments, or structure."""
        lines = content.strip().splitlines()
        if not lines:
            return ""

        # Look for module docstring (Python)
        if lang == "python":
            for i, line in enumerate(lines[:20]):
                if '"""' in line or "'''" in line:
                    # Extract docstring
                    doc_lines = []
                    quote = '"""' if '"""' in line else "'''"
                    start = i
                    for j in range(i, min(len(lines), i + 30)):
                        doc_lines.append(lines[j])
                        if quote in lines[j] and j > i:
                            break
                    doc = "\n".join(doc_lines).replace(quote, "").strip()
                    if len(doc) > 20:
                        return doc[:200]
                    break

        # Look for JSDoc / comment block
        if lang in ("javascript", "typescript"):
            for i, line in enumerate(lines[:20]):
                if line.strip().startswith("/**"):
                    doc_lines = []
                    for j in range(i, min(len(lines), i + 30)):
                        doc_lines.append(lines[j])
                        if "*/" in lines[j]:
                            break
                    doc = "\n".join(doc_lines).replace("/**", "").replace("*/", "").replace("*", "").strip()
                    if len(doc) > 20:
                        return doc[:200]
                    break

        # Default: first meaningful comment or first line
        for line in lines[:10]:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("/*"):
                return stripped.lstrip("#/ *").strip()[:200]

        # Last resort: infer from filename
        name = os.path.basename(path)
        return f"File: {name}"

    def _extract_symbols(self, content: str, lang: str) -> dict[str, list[str]]:
        """Extract functions, classes, imports, routes, endpoints."""
        patterns = self.PATTERNS.get(lang, {})
        result = {
            "functions": [],
            "classes": [],
            "imports": [],
            "routes": [],
            "endpoints": [],
        }

        # Extract functions
        func_pattern = patterns.get("functions")
        if func_pattern:
            matches = re.findall(func_pattern, content, re.MULTILINE)
            for m in matches:
                if isinstance(m, tuple):
                    name = next((x for x in m if x), "")
                else:
                    name = m
                if name and name not in result["functions"]:
                    result["functions"].append(name)

        # Extract classes
        class_pattern = patterns.get("classes")
        if class_pattern:
            matches = re.findall(class_pattern, content, re.MULTILINE)
            for m in matches:
                if m and m not in result["classes"]:
                    result["classes"].append(m)

        # Extract imports
        import_pattern = patterns.get("imports")
        if import_pattern:
            matches = re.findall(import_pattern, content, re.MULTILINE)
            for m in matches:
                if isinstance(m, tuple):
                    imp = m[0] if m[0] else m[1] if len(m) > 1 else ""
                else:
                    imp = m
                if imp and imp not in result["imports"]:
                    result["imports"].append(imp)

        # Extract routes
        route_pattern = patterns.get("routes")
        if route_pattern:
            matches = re.findall(route_pattern, content, re.MULTILINE)
            for m in matches:
                if isinstance(m, tuple):
                    route = m[1] if len(m) > 1 else m[0]
                else:
                    route = m
                if route and route not in result["routes"]:
                    result["routes"].append(route)

        # Extract endpoints
        endpoint_pattern = patterns.get("endpoints")
        if endpoint_pattern:
            matches = re.findall(endpoint_pattern, content, re.MULTILINE)
            for m in matches:
                if isinstance(m, tuple):
                    ep = m[1] if len(m) > 1 else m[0]
                else:
                    ep = m
                if ep and ep not in result["endpoints"]:
                    result["endpoints"].append(ep)

        return result

    def summarize(self, path: str, content: str) -> FileSummary:
        """Create a semantic summary of a file."""
        # Check cache
        file_hash = self._hash_file(path)
        cached = self._cache.get(path)
        if cached and cached.file_hash == file_hash:
            cached.read_count += 1
            return cached

        lang = self._detect_language(path, content)
        purpose = self._extract_purpose(path, content, lang)
        symbols = self._extract_symbols(content, lang)
        lines = len(content.splitlines())

        summary = FileSummary(
            path=path,
            purpose=purpose,
            language=lang,
            functions=symbols["functions"][:20],  # cap
            classes=symbols["classes"][:10],
            routes=symbols["routes"][:15],
            endpoints=symbols["endpoints"][:15],
            imports=symbols["imports"][:15],
            file_hash=file_hash,
            read_count=1,
            lines=lines,
        )

        self._cache[path] = summary
        return summary

    def get_cached_summary(self, path: str) -> Optional[FileSummary]:
        """Get cached summary if file unchanged."""
        file_hash = self._hash_file(path)
        cached = self._cache.get(path)
        if cached and cached.file_hash == file_hash:
            return cached
        return None

    def invalidate(self, path: str) -> None:
        """Invalidate cache for a file (e.g., after write)."""
        self._cache.pop(path, None)


# Global instance
_summarizer = PostReadSummarizer()


def summarize_file(path: str, content: str) -> FileSummary:
    """Convenience function to summarize a file."""
    return _summarizer.summarize(path, content)


def get_cached_summary(path: str) -> Optional[FileSummary]:
    """Get cached summary if unchanged."""
    return _summarizer.get_cached_summary(path)


def invalidate_summary(path: str) -> None:
    """Invalidate cache for a file."""
    _summarizer.invalidate(path)