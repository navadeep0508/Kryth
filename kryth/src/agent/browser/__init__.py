"""KRYTH Browser Profile Manager.

Provides persistent browser profiles so KRYTH remembers logins, cookies,
localStorage, and session state across automation runs.

Quick start::

    from agent.browser import BrowserProfileManager

    mgr = BrowserProfileManager()
    mgr.create("github")                # first time only
    profile = mgr.get_browser_profile("github")   # → BrowserProfile
    # Use profile with BrowserSession(...)
"""

from agent.browser.profile_manager import BrowserProfileManager, ProfileMetadata
from agent.browser.cookie_manager import CookieManager
from agent.browser.storage_manager import StorageManager
from agent.browser.snapshot import SnapshotManager, BrowserSnapshot
from agent.browser.login_memory import LoginMemory
from agent.browser.recovery import ProfileRecovery
from agent.browser.encryption import ProfileEncryption, NOOP_ENCRYPTION
from agent.browser.memory_bridge import BrowserMemoryBridge
from agent.browser.persistent_context import make_persistent_profile, make_temp_profile

__all__ = [
    "BrowserProfileManager",
    "ProfileMetadata",
    "CookieManager",
    "StorageManager",
    "SnapshotManager",
    "BrowserSnapshot",
    "LoginMemory",
    "ProfileRecovery",
    "ProfileEncryption",
    "NOOP_ENCRYPTION",
    "BrowserMemoryBridge",
    "make_persistent_profile",
    "make_temp_profile",
]
