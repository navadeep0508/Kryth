"""Factory helpers for creating Playwright persistent browser contexts.

KRYTH uses ``launch_persistent_context()`` (via ``BrowserProfile``) so that
cookies, localStorage, IndexedDB, installed extensions, and browser history
survive across automation runs.

Usage (async)::

    from agent.browser.persistent_context import make_persistent_profile

    profile = make_persistent_profile("github", project="my-project")
    # Pass to BrowserSession:
    #   BrowserSession(**profile.model_dump())
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def make_persistent_profile(
    profile_name: str,
    *,
    project_hash: str = "",
    headless: Optional[bool] = None,
    extra_args: list[str] | None = None,
) -> "BrowserProfile":  # type: ignore[name-defined]  # noqa: F821
    """Return a ``BrowserProfile`` configured to use a persistent user-data dir.

    Parameters
    ----------
    profile_name:
        Human-readable profile name (e.g. ``"default"``, ``"github"``).
    project_hash:
        If supplied the profile lives under the project-scoped directory;
        otherwise in the global ``~/.kryth/browser_profiles/`` tree.
    headless:
        Override headless mode.  *None* lets ``BrowserProfile`` auto-detect.
    extra_args:
        Additional Chrome CLI args to inject.
    """
    from agent.browser.config import BROWSER_PROFILES_DIR
    from agent.browser.profile_manager import BrowserProfileManager

    mgr = BrowserProfileManager(project_hash=project_hash)
    user_data_dir = mgr.profile_dir(profile_name)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    try:
        from browser_agent.browser.profile import BrowserProfile
    except ImportError:
        raise ImportError(
            "browser-use is not installed. "
            "Install with: pip install browser-use playwright"
        )

    kwargs: dict = {
        "user_data_dir": str(user_data_dir),
        "keep_alive": True,
    }
    if headless is not None:
        kwargs["headless"] = headless
    if extra_args:
        kwargs["args"] = extra_args

    return BrowserProfile(**kwargs)


def make_temp_profile() -> "BrowserProfile":  # type: ignore[name-defined]  # noqa: F821
    """Return a ``BrowserProfile`` backed by a temporary (incognito-like) directory.

    The temp dir is created fresh each call.  Use this when isolation is
    explicitly requested (e.g. the ``testing`` profile type).
    """
    import tempfile

    try:
        from browser_agent.browser.profile import BrowserProfile
    except ImportError:
        raise ImportError("browser-use is not installed.")

    tmp = tempfile.mkdtemp(prefix="kryth-temp-browser-")
    return BrowserProfile(user_data_dir=tmp, keep_alive=False)
