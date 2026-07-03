"""RepoMemory — authoritative repository knowledge store.

Prevents duplicate reads and preserves structural understanding
of the codebase across turns within a session.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FileRecord:
    path: str
    hash: str = ""
    purpose: str = ""
    language: str = ""
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    last_read_turn: int = 0
    read_count: int = 0
    lines: int = 0
    importance_score: float = 0.5
    edit_count: int = 0

    def update_importance(self, current_turn: int, total_files: int) -> None:
        recency = min(1.0, 10.0 / max(1, current_turn - self.last_read_turn + 1))
        task_relevance = min(1.0, self.read_count / max(1, total_files))
        dependency_centrality = min(1.0, len(self.imports) / 10.0)
        edit_freq = min(1.0, self.edit_count / 5.0)
        self.importance_score = (
            0.35 * recency +
            0.35 * task_relevance +
            0.20 * dependency_centrality +
            0.10 * edit_freq
        )


@dataclass
class RepoMemory:
    files: dict[str, FileRecord] = field(default_factory=dict)
    framework: str = ""
    entrypoints: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    symbols: dict[str, list[str]] = field(default_factory=dict)
    dependency_graph: dict[str, list[str]] = field(default_factory=dict)
    _turn: int = 0

    def add_file(self, path: str, content: str, turn: int) -> FileRecord:
        # Normalize path: resolve relative paths using basename match
        # to avoid duplicates like "chatbot.py" vs "src/chatbot.py"
        norm_path = path.replace("\\", "/")
        # Check if a file with the same basename already exists
        norm_base = os.path.basename(norm_path)
        for stored_path in list(self.files.keys()):
            if os.path.basename(stored_path.replace("\\", "/")) == norm_base and stored_path != norm_path:
                norm_path = stored_path
                break
        path = norm_path

        file_hash = self._hash_content(content)
        existing = self.files.get(path)
        if existing and existing.hash == file_hash:
            existing.read_count += 1
            existing.last_read_turn = turn
            existing.update_importance(turn, len(self.files))
            return existing

        lang = self._detect_language(path)
        record = FileRecord(
            path=path,
            hash=file_hash,
            language=lang,
            last_read_turn=turn,
            read_count=1,
            lines=len(content.splitlines()),
        )
        self._extract_purpose(record, content)
        self._extract_symbols(record, content)
        self.files[path] = record
        self._turn = turn
        self._rebuild_aggregates()
        self._compute_importance()
        return record

    def invalidate_file(self, path: str) -> bool:
        stored_key = self._resolve_key(path)
        record = self.files.get(stored_key)
        if not record:
            return False
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                current = hashlib.sha256(f.read().replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]
            if current != record.hash:
                self.files.pop(stored_key, None)
                self._rebuild_aggregates()
                return True
        except Exception:
            pass
        return False

    def remove_file(self, path: str) -> None:
        key = self._resolve_key(path)
        self.files.pop(key, None)
        self._rebuild_aggregates()

    def has_file(self, path: str) -> bool:
        return self._resolve_key(path) in self.files

    def _resolve_key(self, path: str) -> str:
        """Resolve a path to its stored key, with basename fallback."""
        norm = path.replace("\\", "/")
        if norm in self.files:
            return norm
        base = os.path.basename(norm)
        for stored_path in self.files:
            if os.path.basename(stored_path.replace("\\", "/")) == base:
                return stored_path
        return path

    def get_file(self, path: str) -> Optional[FileRecord]:
        return self.files.get(self._resolve_key(path))

    def _rebuild_aggregates(self) -> None:
        self.entrypoints.clear()
        self.routes.clear()
        self.symbols.clear()
        self.dependency_graph.clear()
        frameworks = set()

        for path, rec in self.files.items():
            for imp in rec.imports:
                il = imp.lower()
                if "flask" in il:
                    frameworks.add("Flask")
                elif "django" in il:
                    frameworks.add("Django")
                elif "fastapi" in il:
                    frameworks.add("FastAPI")
                elif "express" in il:
                    frameworks.add("Express")
                elif "react" in il:
                    frameworks.add("React")
                elif "vue" in il:
                    frameworks.add("Vue")

            self.routes.extend(rec.routes)
            self.routes.extend(rec.endpoints)

            syms = rec.functions + rec.classes
            if syms:
                self.symbols[path] = syms[:20]

            if rec.imports:
                self.dependency_graph[path] = rec.imports[:15]

            if any(kw in path.lower() for kw in ("main", "app", "index", "server", "entry", "run", "cli", "__main__")):
                self.entrypoints.append(path)

        self.framework = ", ".join(sorted(frameworks)) if frameworks else ""
        self.routes = list(dict.fromkeys(self.routes))[:25]
        self.entrypoints = list(dict.fromkeys(self.entrypoints))[:5]

    def _compute_importance(self) -> None:
        total = len(self.files)
        for rec in self.files.values():
            rec.update_importance(self._turn, total)

    def to_prompt_block(self, max_chars: int = 4000) -> str:
        if not self.files:
            return ""

        parts = ["KNOWN REPO STATE"]
        if self.framework:
            parts.append(f"Framework: {self.framework}")
        if self.entrypoints:
            parts.append("Entry points:\n  " + "\n  ".join(self.entrypoints))
        if self.routes:
            parts.append("Routes:\n  " + ", ".join(self.routes[:15]))
        if self.symbols:
            sym_lines = [f"  {p}: {', '.join(s[:6])}" for p, s in list(self.symbols.items())[:10]]
            parts.append("Key symbols:\n" + "\n".join(sym_lines))
        file_lines = []
        for p in sorted(self.files.keys())[:15]:
            r = self.files[p]
            desc = f"  {p} ({r.language}, {r.lines} lines)"
            if r.purpose:
                desc += f" — {r.purpose[:50]}"
            file_lines.append(desc)
        if file_lines:
            parts.append(f"Files read ({len(self.files)}):\n" + "\n".join(file_lines))

        block = "\n\n".join(parts)
        if len(block) > max_chars:
            return block[:max_chars] + "\n... (truncated)"
        return block

    def increment_edit_count(self, path: str) -> None:
        rec = self.files.get(path)
        if rec:
            rec.edit_count += 1
            rec.update_importance(self._turn, len(self.files))

    def get_stats(self) -> dict:
        return {
            "files": len(self.files),
            "entrypoints": len(self.entrypoints),
            "routes": len(self.routes),
            "symbols": sum(len(v) for v in self.symbols.values()),
            "avg_importance": sum(r.importance_score for r in self.files.values()) / max(1, len(self.files)),
        }

    @staticmethod
    def _hash_content(content: str) -> str:
        # Normalize line endings so hash is OS-independent
        return hashlib.sha256(content.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _detect_language(path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {
            ".py": "python", ".js": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".tsx": "typescript",
            ".html": "html", ".htm": "html", ".css": "css",
            ".json": "json", ".yaml": "yaml", ".yml": "yaml",
            ".md": "markdown", ".rs": "rust", ".go": "go",
            ".java": "java", ".rb": "ruby", ".kt": "kotlin",
        }.get(ext, "text")

    def _extract_purpose(self, record: FileRecord, content: str) -> None:
        lines = content.strip().splitlines()
        if not lines:
            return
        if record.language == "python":
            for i, line in enumerate(lines[:20]):
                if '"""' in line or "'''" in line:
                    doc_lines = []
                    q = '"""' if '"""' in line else "'''"
                    for j in range(i, min(len(lines), i + 30)):
                        doc_lines.append(lines[j])
                        if q in lines[j] and j > i:
                            break
                    doc = "\n".join(doc_lines).replace(q, "").strip()
                    if len(doc) > 20:
                        record.purpose = doc[:200]
                    return
        elif record.language in ("javascript", "typescript"):
            for i, line in enumerate(lines[:20]):
                if line.strip().startswith("/**"):
                    doc_lines = []
                    for j in range(i, min(len(lines), i + 30)):
                        doc_lines.append(lines[j])
                        if "*/" in lines[j]:
                            break
                    doc = "\n".join(doc_lines).replace("/**", "").replace("*/", "").replace("*", "").strip()
                    if len(doc) > 20:
                        record.purpose = doc[:200]
                    return
        for line in lines[:10]:
            s = line.strip()
            if s.startswith("#") or s.startswith("//"):
                record.purpose = s.lstrip("#/ *").strip()[:200]
                return
        record.purpose = os.path.basename(record.path)

    def _extract_symbols(self, record: FileRecord, content: str) -> None:
        lang = record.language
        if lang == "python":
            record.functions = re.findall(r'^\s*(?:async\s+)?def\s+(\w+)\s*\(', content, re.MULTILINE)[:20]
            record.classes = re.findall(r'^\s*class\s+(\w+)\s*[\(:]', content, re.MULTILINE)[:10]
            record.imports = re.findall(r'^(?:from\s+(\S+)\s+)?import\s+(.+)$', content, re.MULTILINE)
            record.imports = [f"{f} {i}" if f else i for f, i in record.imports][:15]
            routes = re.findall(r'@(?:app|router|api)\.(?:route|get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            record.routes = list(dict.fromkeys(routes))[:15]
        elif lang in ("javascript", "typescript"):
            funcs = re.findall(r'(?:function\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s*)?\()', content, re.MULTILINE)
            record.functions = [f for t in funcs for f in t if f][:20]
            record.classes = re.findall(r'class\s+(\w+)', content, re.MULTILINE)[:10]
            record.imports = re.findall(r'''import\s+(?:.+?\s+from\s+)?["']([^"']+)["']''', content, re.MULTILINE)[:15]
            routes = re.findall(r'(?:app|router)\.(?:get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            record.routes = list(dict.fromkeys(routes))[:15]
        elif lang == "html":
            scripts = re.findall(r'<script\s+src=["\']([^"\']+)["\']', content, re.IGNORECASE)
            record.imports = scripts[:10]
        elif lang in ("go", "java", "rust", "ruby", "kotlin"):
            record.functions = re.findall(r'(?:func|def|fn|fun)\s+(\w+)\s*\(', content, re.MULTILINE)[:20]
            record.classes = re.findall(r'(?:class|struct|type)\s+(\w+)', content, re.MULTILINE)[:10]


class RepoMemoryManager:
    """Thread-safe per-session RepoMemory."""

    def __init__(self):
        self._sessions: dict[int, RepoMemory] = {}
        self._lock = threading.RLock()

    def _get(self, session_id: int) -> RepoMemory:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = RepoMemory()
            return self._sessions[session_id]

    def add_file(self, session_id: int, path: str, content: str, turn: int) -> FileRecord:
        return self._get(session_id).add_file(path, content, turn)

    def invalidate_file(self, session_id: int, path: str) -> bool:
        return self._get(session_id).invalidate_file(path)

    def has_file(self, session_id: int, path: str) -> bool:
        return self._get(session_id).has_file(path)

    def get_file(self, session_id: int, path: str) -> Optional[FileRecord]:
        return self._get(session_id).get_file(path)

    def remove_file(self, session_id: int, path: str) -> None:
        self._get(session_id).remove_file(path)

    def clear(self, session_id: int) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def get_prompt_block(self, session_id: int, max_chars: int = 4000) -> str:
        return self._get(session_id).to_prompt_block(max_chars)

    def get_stats(self, session_id: int) -> dict:
        return self._get(session_id).get_stats()
