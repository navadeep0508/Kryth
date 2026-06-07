"""JSON-schema specs for browser profile manager tools."""

from __future__ import annotations

BROWSER_PROFILE_TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "browser_profile_create",
            "description": (
                "Create a new persistent browser profile. "
                "After creation, the profile stores cookies, localStorage, "
                "and session state so logins survive across automation runs. "
                "Use named profiles (e.g. 'github', 'stripe') to keep "
                "different accounts isolated."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Profile name (e.g. 'default', 'github', 'work'). Alphanumeric and hyphens only.",
                    },
                    "profile_type": {
                        "type": "string",
                        "enum": ["default", "work", "personal", "testing", "temporary"],
                        "description": "Profile category.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional human-readable note about this profile.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_profile_list",
            "description": "List all available browser profiles and their metadata (last used, type, login status).",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_only": {
                        "type": "boolean",
                        "description": "If true, only list profiles scoped to the current project.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_profile_switch",
            "description": (
                "Switch the active browser profile. "
                "Returns metadata including the user_data_dir that should be "
                "passed to BrowserSession for persistent context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Profile name to switch to.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_profile_backup",
            "description": "Create a timestamped backup of a browser profile (cookies, storage, chromium data).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Profile name to back up (default: 'default').",
                    },
                    "label": {
                        "type": "string",
                        "description": "Optional label appended to backup directory name.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_profile_restore",
            "description": "Restore a browser profile from its latest (or specified) backup.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Profile name to restore.",
                    },
                    "backup_path": {
                        "type": "string",
                        "description": "Absolute path to the specific backup directory. Omit to use latest.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_profile_clear",
            "description": (
                "Clear all session data (cookies, storage, chromium state) from a profile "
                "while keeping backups intact. Use before a fresh login flow."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Profile name to clear.",
                    },
                    "keep_backups": {
                        "type": "boolean",
                        "description": "Preserve existing backups (default true).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot_save",
            "description": (
                "Save a complete snapshot of the current browser state "
                "(cookies, localStorage, open tab URLs) for later rollback. "
                "Always call this before risky automation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "string",
                        "description": "Profile name (default: 'default').",
                    },
                    "label": {
                        "type": "string",
                        "description": "Human-readable label for this snapshot.",
                    },
                    "cookies": {
                        "type": "array",
                        "description": "Playwright cookie list to snapshot.",
                        "items": {"type": "object"},
                    },
                    "origins": {
                        "type": "array",
                        "description": "Playwright origins list (localStorage).",
                        "items": {"type": "object"},
                    },
                    "open_tabs": {
                        "type": "array",
                        "description": "List of currently open tab URLs.",
                        "items": {"type": "string"},
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot_restore",
            "description": "Restore browser state from a previously saved snapshot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "string",
                        "description": "Profile name (default: 'default').",
                    },
                    "snapshot_path": {
                        "type": "string",
                        "description": "Absolute path to a specific snapshot JSON. Omit to use latest.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_session_status",
            "description": (
                "Check whether KRYTH is currently logged in to a website "
                "under the given profile. Returns session metadata and "
                "whether re-authentication is needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL or domain to check (e.g. 'github.com').",
                    },
                    "profile": {
                        "type": "string",
                        "description": "Profile name (default: 'default').",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_session_record_login",
            "description": (
                "Record that KRYTH has successfully logged in to a website. "
                "Call this after a successful authentication so future browser "
                "tasks can skip the login step."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the site that was logged into.",
                    },
                    "profile": {
                        "type": "string",
                        "description": "Profile name (default: 'default').",
                    },
                    "approved": {
                        "type": "boolean",
                        "description": "Whether the user has approved storing this session (default false).",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_profile_get_user_data_dir",
            "description": (
                "Return the absolute path to the user-data-dir for a named profile. "
                "Pass this to BrowserSession(user_data_dir=...) to use persistent context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Profile name (default: 'default').",
                    }
                },
                "required": [],
            },
        },
    },
]
