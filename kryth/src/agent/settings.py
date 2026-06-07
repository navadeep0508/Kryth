import json
import os


SETTINGS_PATH = os.path.join(".kryth", "settings.json")


DEFAULTS = {
    "permissions": {
        "allow": [
            "read_file:*",
            "list_files:*",
            "search_code:*",
            "grep:*",
            "glob:*",
            "semantic_search:*",
            "lookup_symbol:*",
            "fts_search:*",
            "ast_search:*",
            "graphify_query:*",
            "search_smart:*",
            "todo_write:*",
            "todo_read:*",
            "task_output:*",
            "exit_plan_mode:*",
        ],
        "ask": [
            "write_file:*",
            "edit_file:*",
            "multi_edit:*",
            "delete_file:*",
            "run_command:pip install*",
            "run_command:npm install*",
            "run_command:git push*",
            "run_command:docker*",
            "run_command:sudo*",
        ],
        "deny": [
            "run_command:rm -rf*",
            "run_command:shutdown*",
            "run_command:reboot*",
            "run_command:mkfs*",
        ],
        "default": "allow",
    },
    "hooks": {
        "PreToolUse": [],
        "PostToolUse": [],
        "Stop": [],
    },
    "skills_dir": os.path.join(".kryth", "skills"),
    # Multi-agent orchestration mode.
    # ASK: always prompt before spawning multiple agents (default, safe)
    # AUTO: orchestration engine decides based on cost/benefit
    # SESSION_APPROVED: user approved multi-agent this session
    # ALWAYS_SINGLE: never use multi-agent execution
    "multi_agent_mode": "ASK",
    # Retrieval engine feature flags.
    # All can also be set via environment variables (see retrieval/config.py).
    "retrieval": {
        "ENABLE_GRAPHIFY":    True,
        "ENABLE_RIPGREP":     True,
        "ENABLE_AST_GREP":    True,
        "ENABLE_FTS":         True,
        "ENABLE_MMAP":        True,
        "ENABLE_ASYNC_IO":    True,
        "ENABLE_CACHE":       True,
        "ENABLE_WATCHER":     False,
        "MMAP_THRESHOLD":     1048576,    # 1 MB
        "STREAM_THRESHOLD":   52428800,   # 50 MB
        "MAX_FILE_SIZE":      104857600,  # 100 MB
        "MAX_CONCURRENT_READS": 8,
        "CACHE_SIZE":         1.0,        # GB
        "CACHE_TTL":          3600,       # seconds
        "INDEX_BATCH_SIZE":   100,
    },
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    if not os.path.exists(SETTINGS_PATH):
        return DEFAULTS
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
    except Exception:
        return DEFAULTS
    return _deep_merge(DEFAULTS, user)


def load_user_settings() -> dict:
    """Return ONLY what the user explicitly wrote in ``.kryth/settings.json``.

    Distinct from ``load_settings()``, which folds in ``DEFAULTS``.
    Permission evaluation needs this raw view so the built-in default
    rules (which mirror the ``default`` profile) do not override more
    restrictive profiles like ``readonly`` or ``safe``.
    """
    if not os.path.exists(SETTINGS_PATH):
        return {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}
