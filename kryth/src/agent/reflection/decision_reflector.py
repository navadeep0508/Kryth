from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from agent.reflection.experience_record import (
    KnowledgeLesson, LessonKind, LessonScope, ReflectionInput,
)
from agent.reflection.extractor import BaseReflector

# ---------------------------------------------------------------------------
# Module-level compiled patterns
# ---------------------------------------------------------------------------

_IMPLICIT_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bI chose\s+(.+?)\s+because\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bwe'?re?\s+using\s+(.+?)\s+for\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bdecided to use\s+(.+?)(?:\s+because\s+(.+?))?(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bselected\s+(.+?)\s+over\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bwent with\s+(.+?)(?:\s+because\s+(.+?))?(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bprefer\s+(.+?)\s+because\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\buse\s+(.+?)\s+instead of\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
]

_SECURITY_KEYWORDS: re.Pattern = re.compile(
    r"\b(auth(?:entication|orization)?|jwt|oauth|api[_\s-]?key|encryption|"
    r"tls|ssl|password|secret|token|credential|hmac|bcrypt|argon2|csrf|xss)\b",
    re.IGNORECASE,
)

_DEPENDENCY_FILES = frozenset({
    "package.json", "requirements.txt", "Cargo.toml",
    "pyproject.toml", "go.mod", "Gemfile", "build.gradle",
})

# Simple package-name extraction heuristics per file type
_PKG_EXTRACTORS: Dict[str, re.Pattern] = {
    "requirements.txt": re.compile(r"^\s*([A-Za-z0-9_\-\.]+)", re.MULTILINE),
    "package.json": re.compile(r'"([^"]+)"\s*:\s*"[^"]*"'),
    "Cargo.toml": re.compile(r'^\s*([a-z0-9_\-]+)\s*=', re.MULTILINE),
    "pyproject.toml": re.compile(r'^\s*([A-Za-z0-9_\-\.]+)\s*[>=<!\^]', re.MULTILINE),
    "go.mod": re.compile(r'^\s*require\s+(\S+)', re.MULTILINE),
    "Gemfile": re.compile(r"gem\s+['\"]([^'\"]+)['\"]"),
    "build.gradle": re.compile(r"(?:implementation|api|compile)\s+['\"]([^'\"]+)['\"]"),
}

_ARCH_DIRS = re.compile(
    r"\b(services?|modules?|controllers?|handlers?|repositories?|"
    r"adapters?|gateways?|domain|infrastructure|application|core)\b",
    re.IGNORECASE,
)


class DecisionReflector(BaseReflector):
    name = "decision_reflector"

    _DECISION_PATTERNS: List[re.Pattern] = _IMPLICIT_PATTERNS
    _SECURITY_KEYWORDS: re.Pattern = _SECURITY_KEYWORDS
    _DEPENDENCY_FILES = _DEPENDENCY_FILES

    def extract(self, inp: ReflectionInput) -> List[KnowledgeLesson]:
        lessons: List[KnowledgeLesson] = []

        lessons.extend(self._extract_explicit_decisions(inp.decisions))

        text_sources = inp.session_messages
        lessons.extend(self._extract_implicit_decisions(text_sources, inp.final_output))

        lessons.extend(self._extract_dependency_decisions(inp.tool_calls))

        lessons.extend(self._extract_architecture_decisions(inp.file_changes))

        return lessons

    # ------------------------------------------------------------------
    # Rule 1: explicit decisions
    # ------------------------------------------------------------------

    def _extract_explicit_decisions(self, decisions: list) -> List[KnowledgeLesson]:
        lessons: List[KnowledgeLesson] = []
        for dec in decisions:
            title = dec.get("title", "")
            choice = dec.get("choice", "")
            rationale = dec.get("rationale", "")
            alternatives = dec.get("alternatives", [])
            if not title and not choice:
                continue

            text_for_sec = f"{title} {choice} {rationale}"
            kind = LessonKind.SECURITY if self._is_security_relevant(text_for_sec) else LessonKind.DECISION

            lessons.append(self._make_lesson(
                key=f"decision:{self._slugify(title or choice)}",
                kind=kind,
                scope=LessonScope.PROJECT,
                value={
                    "title": title,
                    "choice": choice,
                    "rationale": rationale,
                    "alternatives": alternatives,
                    "source": "explicit",
                },
                importance=0.9,
                confidence=0.95,
                reuse_potential=0.8,
            ))
        return lessons

    # ------------------------------------------------------------------
    # Rule 2: implicit decisions mined from free text
    # ------------------------------------------------------------------

    def _extract_implicit_decisions(
        self,
        messages: List[dict],
        output: str,
    ) -> List[KnowledgeLesson]:
        lessons: List[KnowledgeLesson] = []
        seen_choices: set = set()

        texts: List[str] = [output] if output else []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        texts.append(block.get("text", ""))

        for text in texts:
            for pattern in self._DECISION_PATTERNS:
                for match in pattern.finditer(text):
                    groups = [g.strip() for g in match.groups() if g]
                    if not groups:
                        continue
                    choice = groups[0][:80]
                    rationale = groups[1][:160] if len(groups) > 1 else ""

                    if choice.lower() in seen_choices:
                        continue
                    seen_choices.add(choice.lower())

                    is_sec = self._is_security_relevant(match.group(0))
                    kind = LessonKind.SECURITY if is_sec else LessonKind.DECISION
                    importance = 0.9 if is_sec else 0.75

                    lessons.append(self._make_lesson(
                        key=f"decision:implicit:{self._slugify(choice)}",
                        kind=kind,
                        scope=LessonScope.PROJECT,
                        value={
                            "choice": choice,
                            "rationale": rationale,
                            "source": "implicit",
                            "matched_text": match.group(0)[:200],
                        },
                        importance=importance,
                        confidence=0.6,
                        reuse_potential=0.6,
                    ))
        return lessons

    # ------------------------------------------------------------------
    # Rule 3: dependency decisions from file writes
    # ------------------------------------------------------------------

    def _extract_dependency_decisions(self, tool_calls: list) -> List[KnowledgeLesson]:
        lessons: List[KnowledgeLesson] = []
        for call in tool_calls:
            path = call.get("path", call.get("file", call.get("filename", "")))
            if not path:
                continue
            basename = os.path.basename(path)
            if basename not in self._DEPENDENCY_FILES:
                continue

            content = call.get("content", call.get("new_content", ""))
            if not isinstance(content, str) or not content.strip():
                continue

            extractor = _PKG_EXTRACTORS.get(basename)
            packages = []
            if extractor:
                packages = [m.group(1) for m in extractor.finditer(content)]
                # Filter out JSON boilerplate keys for package.json
                if basename == "package.json":
                    _JSON_SKIP = {"name", "version", "description", "main", "scripts",
                                  "keywords", "author", "license", "dependencies",
                                  "devDependencies", "peerDependencies"}
                    packages = [p for p in packages if p not in _JSON_SKIP]

            for pkg in packages[:20]:  # cap to avoid noise from large lockfiles
                lessons.append(self._make_lesson(
                    key=f"decision:dependency:{self._slugify(pkg)}",
                    kind=LessonKind.DECISION,
                    scope=LessonScope.PROJECT,
                    value={
                        "package": pkg,
                        "file": basename,
                        "note": "New dependency added",
                    },
                    importance=0.7,
                    confidence=0.75,
                    reuse_potential=0.65,
                ))
        return lessons

    # ------------------------------------------------------------------
    # Rule 4: architecture decisions from file structure changes
    # ------------------------------------------------------------------

    def _extract_architecture_decisions(self, file_changes: list) -> List[KnowledgeLesson]:
        lessons: List[KnowledgeLesson] = []
        new_dirs: Dict[str, int] = {}

        for change in file_changes:
            path = change.get("path", "")
            op = change.get("operation", "")
            if op not in ("create", "add", "write", ""):
                continue
            parts = path.replace("\\", "/").split("/")
            for part in parts[:-1]:
                m = _ARCH_DIRS.match(part)
                if m:
                    new_dirs[part.lower()] = new_dirs.get(part.lower(), 0) + 1

        for dir_name, count in new_dirs.items():
            if count >= 2:
                pattern_slug = self._slugify(dir_name)
                lessons.append(self._make_lesson(
                    key=f"decision:architecture:{pattern_slug}",
                    kind=LessonKind.DECISION,
                    scope=LessonScope.PROJECT,
                    value={
                        "pattern": dir_name,
                        "file_count": count,
                        "note": f"Architecture pattern '{dir_name}' inferred from file layout",
                    },
                    importance=0.8,
                    confidence=0.65,
                    reuse_potential=0.75,
                ))
        return lessons

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _is_security_relevant(self, text: str) -> bool:
        return bool(self._SECURITY_KEYWORDS.search(text))
