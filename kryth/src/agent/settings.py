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
