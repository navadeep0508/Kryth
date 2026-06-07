"""JSON-schema specs for the four retrieval-engine tools.

Merged into TOOL_SPECS at import time in tools/__init__.py.
Keep in sync with the functions in _search.py.
"""
from __future__ import annotations

RETRIEVAL_TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "fts_search",
            "description": (
                "Full-text search across the repository using a pre-built "
                "SQLite FTS5 index with BM25 ranking. Faster than grep for "
                "repeated searches. Best for: finding documentation, README "
                "content, comments, string literals, and multi-word phrases. "
                "The index is built lazily on first call; fallback to grep "
                "if you need results immediately on a fresh repo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query. Supports plain keywords, quoted "
                            "phrases, and FTS5 operators (AND, OR, NOT). "
                            "Example: 'authentication middleware', "
                            "'error AND handler', 'README*'"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (default '.').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results to return (default 20, max 100).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ast_search",
            "description": (
                "Structural code search using AST patterns. Finds code by "
                "structure rather than text — works regardless of formatting. "
                "Best for: finding all function definitions, class declarations, "
                "decorators, API route registrations, React hooks, async "
                "functions, and import patterns. Uses ast-grep CLI when "
                "available, falls back to repo_index for Python."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Named pattern or raw ast-grep pattern. Named patterns: "
                            "function, async_function, class, decorator, import, "
                            "from_import, lambda (Python); "
                            "function, arrow_function, class, async_function, "
                            "import, export, react_component, react_hook, api_route "
                            "(JavaScript/TypeScript). "
                            "Raw ast-grep patterns also accepted."
                        ),
                    },
                    "language": {
                        "type": "string",
                        "description": (
                            "Language to search: 'python', 'javascript', "
                            "'typescript'. Auto-detected from project if omitted."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (default '.').",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graphify_query",
            "description": (
                "Query the code knowledge graph powered by Graphify. Finds "
                "symbol relationships, dependency chains, call graphs, and "
                "semantic connections. Falls back to AST-based repo_index when "
                "graphifyy is not installed. Best for: 'what calls X', "
                "'what depends on Y', 'what does file Z import', "
                "'find all symbols related to authentication'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Symbol name, file path, or natural language question "
                            "about code relationships. Examples: 'authenticate_user', "
                            "'src/auth/middleware.py', 'jwt token handler'"
                        ),
                    },
                    "query_type": {
                        "type": "string",
                        "enum": [
                            "semantic",
                            "callers",
                            "callees",
                            "imports",
                            "dependents",
                        ],
                        "description": (
                            "semantic: find related symbols (default); "
                            "callers: find all call-sites of a symbol; "
                            "callees: find functions called inside a symbol; "
                            "imports: find modules a file imports; "
                            "dependents: find files that depend on this symbol."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": "Project root directory (default '.').",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_smart",
            "description": (
                "Intelligent multi-engine code search that automatically selects "
                "the fastest retrieval strategy. Classifies the query and routes "
                "to the cheapest engine first: keywords→ripgrep, symbol names→"
                "AST index, structural patterns→ast-grep, docs/comments→FTS5, "
                "relationships→Graphify, concepts→semantic retriever. For complex "
                "queries, combines all engines and merges results. Use this as "
                "the default search when you don't know which engine to pick."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Natural language or code search query. Examples: "
                            "'authentication middleware', 'find all async routes', "
                            "'what calls process_payment', 'where is JWT validated'"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (default '.').",
                    },
                    "engines": {
                        "type": "string",
                        "description": (
                            "Comma-separated list of engines to force. Options: "
                            "ripgrep, symbol, fts, ast, graphify, semantic. "
                            "Omit to let the router choose automatically."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
]
