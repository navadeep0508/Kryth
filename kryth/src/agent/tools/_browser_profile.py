"""Tool implementations for the browser profile manager.

These functions are registered with the KRYTH tool registry and exposed
to the LLM via BROWSER_PROFILE_TOOL_SPECS.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _mgr(project_hash: str = "") -> "BrowserProfileManager":  # noqa: F821
    from agent.browser.profile_manager import BrowserProfileManager
    return BrowserProfileManager(project_hash=project_hash)


def _login_memory() -> "LoginMemory":  # noqa: F821
    from agent.browser.login_memory import LoginMemory
    return LoginMemory()


# ------------------------------------------------------------------
# Profile CRUD
# ------------------------------------------------------------------


def browser_profile_create(
    name: str = "default",
    profile_type: str = "default",
    description: str = "",
) -> str:
    """Create a new persistent browser profile."""
    try:
        mgr = _mgr()
        pdir = mgr.create(name=name, profile_type=profile_type, description=description)
        return json.dumps({
            "status": "created",
            "name": name,
            "profile_dir": str(pdir),
            "user_data_dir": str(pdir / "chromium"),
        })
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def browser_profile_list(project_only: bool = False) -> str:
    """List all available browser profiles."""
    try:
        mgr = _mgr()
        profiles = []
        for pname in mgr.list_profiles():
            meta = mgr.load(pname)
            profiles.append({
                "name": pname,
                "type": meta.profile_type,
                "last_used": meta.last_used,
                "description": meta.description,
                "user_data_dir": str(mgr.profile_dir(pname) / "chromium"),
            })
        return json.dumps({"profiles": profiles, "count": len(profiles)})
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def browser_profile_switch(name: str) -> str:
    """Switch to a named browser profile."""
    try:
        mgr = _mgr()
        meta = mgr.switch(name)
        pdir = mgr.profile_dir(name)
        return json.dumps({
            "status": "switched",
            "name": name,
            "profile_dir": str(pdir),
            "user_data_dir": str(pdir / "chromium"),
            "type": meta.profile_type,
            "last_used": meta.last_used,
        })
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def browser_profile_backup(name: str = "default", label: str = "") -> str:
    """Back up a browser profile."""
    try:
        mgr = _mgr()
        backup_path = mgr.backup(name=name, label=label)
        return json.dumps({"status": "backed_up", "name": name, "backup_path": str(backup_path)})
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def browser_profile_restore(
    name: str = "default",
    backup_path: str = "",
) -> str:
    """Restore a browser profile from backup."""
    try:
        mgr = _mgr()
        bp = Path(backup_path) if backup_path else None
        ok = mgr.restore(name=name, backup_path=bp)
        return json.dumps({"status": "restored" if ok else "failed", "name": name})
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def browser_profile_clear(name: str = "default", keep_backups: bool = True) -> str:
    """Clear all session data from a profile."""
    try:
        mgr = _mgr()
        mgr.clear(name=name, keep_backups=keep_backups)
        return json.dumps({"status": "cleared", "name": name})
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def browser_profile_get_user_data_dir(name: str = "default") -> str:
    """Return the user-data-dir path for a named profile."""
    try:
        mgr = _mgr()
        mgr.load(name)  # ensure it exists
        udd = mgr.profile_dir(name) / "chromium"
        udd.mkdir(parents=True, exist_ok=True)
        return json.dumps({
            "name": name,
            "user_data_dir": str(udd),
            "usage": f'BrowserSession(user_data_dir="{udd}")',
        })
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


# ------------------------------------------------------------------
# Snapshots
# ------------------------------------------------------------------


def browser_snapshot_save(
    profile: str = "default",
    label: str = "",
    cookies: list[dict] | None = None,
    origins: list[dict] | None = None,
    open_tabs: list[str] | None = None,
) -> str:
    """Save a browser state snapshot."""
    try:
        mgr = _mgr()
        sm = mgr.snapshots(profile)
        path = sm.save(
            cookies=cookies,
            origins=origins,
            open_tabs=open_tabs,
            label=label,
        )
        return json.dumps({"status": "saved", "profile": profile, "snapshot_path": str(path)})
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def browser_snapshot_restore(
    profile: str = "default",
    snapshot_path: str = "",
) -> str:
    """Restore browser state from a snapshot."""
    try:
        mgr = _mgr()
        sm = mgr.snapshots(profile)
        sp = Path(snapshot_path) if snapshot_path else None
        snap = sm.restore(sp)
        if snap is None:
            return json.dumps({"status": "no_snapshot_found", "profile": profile})
        return json.dumps({
            "status": "restored",
            "profile": profile,
            "label": snap.label,
            "timestamp": snap.timestamp,
            "cookies_count": len(snap.cookies),
            "open_tabs": snap.open_tabs,
        })
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


# ------------------------------------------------------------------
# Session / login memory
# ------------------------------------------------------------------


def browser_session_status(url: str, profile: str = "default") -> str:
    """Check login status for a URL under the given profile."""
    try:
        lm = _login_memory()
        logged_in = lm.is_logged_in(url, profile)
        needs_approval = lm.needs_approval(url)
        sessions = lm.get_all_sessions(profile=profile, valid_only=True)
        # Filter to this site
        from agent.browser.login_memory import _extract_site
        site = _extract_site(url)
        site_sessions = [s for s in sessions if s["site"] == site]
        return json.dumps({
            "url": url,
            "site": site,
            "profile": profile,
            "logged_in": logged_in,
            "sensitive": needs_approval,
            "sessions": site_sessions,
        })
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def browser_session_record_login(
    url: str,
    profile: str = "default",
    approved: bool = False,
) -> str:
    """Record a successful login for future session reuse."""
    try:
        lm = _login_memory()
        needs_approval = lm.needs_approval(url)

        if needs_approval and not approved:
            return json.dumps({
                "status": "approval_required",
                "url": url,
                "message": (
                    f"This is a sensitive site ({url}). "
                    "Set approved=true to store the session."
                ),
            })

        lm.record_login(url, profile=profile, approved=approved or not needs_approval)
        return json.dumps({"status": "recorded", "url": url, "profile": profile})
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------

BROWSER_PROFILE_TOOLS: dict[str, Any] = {
    "browser_profile_create": browser_profile_create,
    "browser_profile_list": browser_profile_list,
    "browser_profile_switch": browser_profile_switch,
    "browser_profile_backup": browser_profile_backup,
    "browser_profile_restore": browser_profile_restore,
    "browser_profile_clear": browser_profile_clear,
    "browser_profile_get_user_data_dir": browser_profile_get_user_data_dir,
    "browser_snapshot_save": browser_snapshot_save,
    "browser_snapshot_restore": browser_snapshot_restore,
    "browser_session_status": browser_session_status,
    "browser_session_record_login": browser_session_record_login,
}
