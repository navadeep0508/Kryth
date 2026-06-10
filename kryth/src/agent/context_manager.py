"""Tool output compressor — strips large payloads from context.

Prevents token overflow by:
1. Capping HTML/page content at MAX_HTML_CHARS chars
2. Compressing tool results > LARGE_RESULT_THRESHOLD into summaries
3. Replacing old browser/search results with stubs after KEEP_RECENT_N calls
4. Hard limits: MAX_SEARCHES, MAX_OPEN_PAGES per session
"""

from __future__ import annotations

import re
from typing import Optional

# Hard limits
MAX_HTML_CHARS    = 100000  # max raw HTML/page text kept in a tool result
MAX_RESULT_CHARS  = 100000  # max any single tool result before compression
LARGE_THRESHOLD   = 50000   # compress results above this
MAX_SEARCHES      = 10000   # max browser_search calls per session
MAX_OPEN_PAGES    = 5000    # max open_url calls per session
COMPRESS_EVERY_N  = 40      # auto-compress after this many tool calls

# Tool result types that contain large volatile content
_BROWSER_TOOLS = {
    "open_url", "browser_get_html", "extract_data",
    "browser_search", "download_content",
}

# Counters (reset per session via reset())
_search_count = 0
_page_count   = 0


def reset() -> None:
    """Reset per-session counters."""
    global _search_count, _page_count
    _search_count = 0
    _page_count = 0


def check_limit(tool_name: str) -> Optional[str]:
    """Return an error string if a hard limit is hit, else None."""
    global _search_count, _page_count
    if tool_name == "browser_search":
        _search_count += 1
        if _search_count > MAX_SEARCHES:
            return (
                f"[LIMIT] MAX_SEARCHES ({MAX_SEARCHES}) reached. "
                "Stop searching and synthesize findings from what you already have. "
                "Use get_research_report() to see accumulated findings."
            )
    if tool_name == "open_url":
        _page_count += 1
        if _page_count > MAX_OPEN_PAGES:
            return (
                f"[LIMIT] MAX_OPEN_PAGES ({MAX_OPEN_PAGES}) reached. "
                "Stop opening pages and write your final report from existing findings."
            )
    return None


def compress_html(content: str) -> str:
    """Strip a raw HTML string down to useful text only."""
    if not content or len(content) <= MAX_HTML_CHARS:
        return content

    # Remove script, style, noscript, head blocks
    content = re.sub(r'<(script|style|noscript|head)[^>]*>.*?</\1>', ' ', content, flags=re.DOTALL | re.IGNORECASE)
    # Remove all HTML tags
    content = re.sub(r'<[^>]+>', ' ', content)
    # Collapse whitespace
    content = re.sub(r'\s+', ' ', content).strip()
    # Cap size
    if len(content) > MAX_HTML_CHARS:
        content = content[:MAX_HTML_CHARS] + f"\n...[truncated, {len(content)-MAX_HTML_CHARS} more chars]"
    return content


def compress_result(tool_name: str, result: str) -> str:
    """Compress a tool result to fit within MAX_RESULT_CHARS."""
    if not result or len(result) <= LARGE_THRESHOLD:
        return result

    # HTML content — extract text
    if "<html" in result.lower() or "<body" in result.lower() or "<!DOCTYPE" in result[:100]:
        result = compress_html(result)
        return result

    # Long plain text — keep head + tail
    if len(result) > MAX_RESULT_CHARS:
        head = result[:MAX_RESULT_CHARS // 2]
        tail = result[-(MAX_RESULT_CHARS // 4):]
        dropped = len(result) - len(head) - len(tail)
        return f"{head}\n...[{dropped} chars compressed — save key facts before continuing]...\n{tail}"

    return result


def compress_messages(messages: list) -> tuple[list, int]:
    """Compress old browser/search tool results in the message list.

    Returns (compressed_messages, chars_removed).
    Keeps the most recent KEEP_RECENT_N browser results intact,
    replaces older ones with a one-line stub.
    """
    KEEP_RECENT_N = 4  # keep last N browser tool results unmodified

    browser_tool_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool" and m.get("name") in _BROWSER_TOOLS
    ]

    if len(browser_tool_indices) <= KEEP_RECENT_N:
        return messages, 0

    to_stub = browser_tool_indices[:-KEEP_RECENT_N]
    chars_removed = 0
    new_messages = []

    for i, m in enumerate(messages):
        if i in to_stub and m.get("role") == "tool":
            body = str(m.get("content") or "")
            if len(body) > 200:
                stub = f"[result compressed — {len(body)} chars; findings saved to research memory]"
                chars_removed += len(body) - len(stub)
                m = {**m, "content": stub}
        new_messages.append(m)

    return new_messages, chars_removed
