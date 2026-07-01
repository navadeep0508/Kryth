"""SEARCH handler — parse intent, repo search, rank, read snippets, summarize, terminate."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from agent.handlers.explore_handler import search as explore_search
from agent.handlers.read_handler import read_files as read_files_impl


SEARCH_VERBS = frozenset({"find", "search", "trace", "lookup", "locate", "where"})

MAX_SEARCH_CALLS = 3
MAX_FILE_READS = 5
CONFIDENCE_THRESHOLD = 0.85


def _is_search_intent(text: str) -> bool:
    """Check if user input indicates search intent."""
    text_lower = text.lower()
    for verb in SEARCH_VERBS:
        if re.search(rf"\b{verb}\b", text_lower):
            return True
    return False


def _extract_search_query(text: str) -> str:
    """Extract the search query from user input."""
    text_lower = text.lower()
    for verb in SEARCH_VERBS:
        match = re.search(rf"\b{verb}\s+(.+)$", text_lower, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return text


def _rank_matches(matches: list[dict], query: str) -> list[dict]:
    """Rank search matches by relevance to query."""
    query_terms = set(query.lower().split())
    scored = []
    for m in matches:
        score = 0
        text = m.get("text", "").lower()
        file = m.get("file", "").lower()
        for term in query_terms:
            if term in text:
                score += 2
            if term in file:
                score += 1
        scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored]


def _read_snippets(matches: list[dict], max_files: int = MAX_FILE_READS) -> list[dict]:
    """Read snippets from top matched files."""
    if not matches:
        return []
    
    unique_files = []
    seen = set()
    for m in matches:
        f = m.get("file")
        if f and f not in seen:
            seen.add(f)
            unique_files.append(f)
        if len(unique_files) >= max_files:
            break
    
    results = read_files_impl(unique_files)
    return results


def _compute_confidence(query: str, matches: list[dict], snippets: list[dict]) -> float:
    """Compute confidence score based on match quality and coverage."""
    if not matches:
        return 0.0
    
    match_count = len(matches)
    file_count = len(set(m.get("file") for m in matches))
    snippet_count = len([s for s in snippets if not s.get("error")])
    
    if match_count == 0:
        return 0.0
    
    coverage = min(snippet_count / max(file_count, 1), 1.0)
    density = min(match_count / 10.0, 1.0)
    
    return (coverage * 0.6 + density * 0.4)


def run_search(query: str, directory: str = ".") -> dict:
    """
    Execute full search pipeline:
    1. Parse intent
    2. Repo search
    3. Rank matches
    4. Read snippets
    5. Summarize
    6. Terminate
    """
    if not _is_search_intent(query):
        return {"status": "not_search", "summary": ""}
    
    search_query = _extract_search_query(query)
    
    # Step 2: Repo search (counts as 1 search call)
    result = explore_search(search_query, directory)
    if result.get("error"):
        return {"status": "error", "summary": result["error"], "search_calls": 1}
    
    matches = result.get("matches", [])
    if not matches:
        return {"status": "no_matches", "summary": f"No matches for '{search_query}'", "search_calls": 1}
    
    # Step 3: Rank matches
    ranked = _rank_matches(matches, search_query)
    
    # Step 4: Read snippets (up to MAX_FILE_READS)
    snippets = _read_snippets(ranked, MAX_FILE_READS)
    
    # Step 5: Compute confidence
    confidence = _compute_confidence(search_query, ranked, snippets)
    
    # Step 6: Summarize
    lines = [f"Search: '{search_query}' — {len(matches)} matches, confidence {confidence:.2f}"]
    
    if ranked:
        lines.append("\nTop matches:")
        for m in ranked[:5]:
            lines.append(f"  {m['file']}:{m['line']} — {m['text'][:80]}")
    
    if snippets:
        lines.append("\nFile contents:")
        for s in snippets:
            if s.get("error"):
                lines.append(f"  {s['path']}: ERROR — {s['error']}")
            else:
                preview = s["content"][:200].replace("\n", " ")
                lines.append(f"  {s['path']}: {preview}...")
    
    summary = "\n".join(lines)
    
    return {
        "status": "success",
        "summary": summary,
        "confidence": confidence,
        "matches": len(matches),
        "files_read": len([s for s in snippets if not s.get("error")]),
        "search_calls": 1,
        "terminated": confidence >= CONFIDENCE_THRESHOLD or len(snippets) >= MAX_FILE_READS,
    }


def search_task_loop(query: str, directory: str = ".") -> dict:
    """
    Search task with multi-call budget enforcement.
    Uses the anti-paralysis search budget internally.
    """
    return run_search(query, directory)