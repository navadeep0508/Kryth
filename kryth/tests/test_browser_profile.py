"""Tests for the KRYTH Browser Profile Manager.

Covers:
- BrowserProfileManager CRUD
- CookieManager persistence
- StorageManager persistence
- SnapshotManager save/restore
- LoginMemory recording and querying
- ProfileRecovery backup/restore/health
- ProfileEncryption encrypt/decrypt round-trip
- BrowserMemoryBridge (no real MemoryManager required)
- Tool handler functions (JSON round-trip)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_profile_dir(tmp_path: Path) -> Path:
    """Return a fresh temp directory to use as a profile root."""
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    return pdir


@pytest.fixture()
def mgr(tmp_profile_dir: Path):
    from agent.browser.profile_manager import BrowserProfileManager
    return BrowserProfileManager(base_dir=tmp_profile_dir)


@pytest.fixture()
def profile_dir(mgr, tmp_profile_dir: Path) -> Path:
    mgr.create("test-profile")
    return tmp_profile_dir / "test-profile"


# ---------------------------------------------------------------------------
# BrowserProfileManager — create / load / list
# ---------------------------------------------------------------------------


class TestBrowserProfileManager:
    def test_create_returns_path(self, mgr, tmp_profile_dir):
        pdir = mgr.create("default")
        assert pdir.exists()
        assert (pdir / "chromium").exists()
        assert (pdir / "kryth_profile.json").exists()

    def test_create_writes_metadata(self, mgr):
        mgr.create("work", profile_type="work", description="My work profile")
        meta = mgr.load("work")
        assert meta.name == "work"
        assert meta.profile_type == "work"
        assert meta.description == "My work profile"

    def test_load_auto_creates_missing_profile(self, mgr):
        meta = mgr.load("auto-created")
        assert meta.name == "auto-created"
        assert mgr.profile_dir("auto-created").exists()

    def test_list_profiles(self, mgr):
        mgr.create("alpha")
        mgr.create("beta")
        names = mgr.list_profiles()
        assert "alpha" in names
        assert "beta" in names

    def test_switch_loads_profile(self, mgr):
        mgr.create("github")
        meta = mgr.switch("github")
        assert meta.name == "github"

    def test_clear_removes_session_data(self, mgr):
        mgr.create("clear-me")
        mgr.cookies("clear-me").save([{"name": "session", "value": "abc", "domain": "example.com"}])
        mgr.clear("clear-me", keep_backups=False)
        cookies = mgr.cookies("clear-me").load()
        assert cookies == []

    def test_clear_keeps_backups_when_requested(self, mgr):
        mgr.create("backup-profile")
        mgr.backup("backup-profile", label="pre-clear")
        mgr.clear("backup-profile", keep_backups=True)
        backups = mgr.recovery("backup-profile").list_backups()
        assert len(backups) >= 1

    def test_delete_removes_directory(self, mgr):
        mgr.create("to-delete")
        mgr.delete("to-delete")
        assert not mgr.profile_dir("to-delete").exists()

    def test_get_storage_state_empty(self, mgr):
        mgr.create("empty")
        state = mgr.get_storage_state("empty")
        assert state == {"cookies": [], "origins": []}

    def test_save_and_reload_cookies_via_manager(self, mgr):
        mgr.create("cookie-test")
        cookies = [{"name": "auth", "value": "tok123", "domain": "github.com"}]
        mgr.save("cookie-test", cookies=cookies)
        loaded = mgr.cookies("cookie-test").load()
        assert loaded == cookies


# ---------------------------------------------------------------------------
# CookieManager
# ---------------------------------------------------------------------------


class TestCookieManager:
    def test_save_and_load(self, profile_dir):
        from agent.browser.cookie_manager import CookieManager
        cm = CookieManager(profile_dir)
        cookies = [{"name": "sid", "value": "xyz", "domain": "example.com", "expires": -1}]
        cm.save(cookies)
        assert cm.load() == cookies

    def test_load_returns_empty_when_no_file(self, tmp_path):
        from agent.browser.cookie_manager import CookieManager
        cm = CookieManager(tmp_path / "nonexistent")
        assert cm.load() == []

    def test_clear_removes_file(self, profile_dir):
        from agent.browser.cookie_manager import CookieManager
        cm = CookieManager(profile_dir)
        cm.save([{"name": "x", "value": "1", "domain": "a.com"}])
        cm.clear()
        assert cm.load() == []

    def test_cookies_for_domain(self, profile_dir):
        from agent.browser.cookie_manager import CookieManager
        cm = CookieManager(profile_dir)
        cm.save([
            {"name": "a", "value": "1", "domain": ".github.com"},
            {"name": "b", "value": "2", "domain": ".example.com"},
        ])
        result = cm.cookies_for_domain("github.com")
        assert len(result) == 1
        assert result[0]["name"] == "a"

    def test_remove_expired(self, profile_dir):
        from agent.browser.cookie_manager import CookieManager
        cm = CookieManager(profile_dir)
        past = time.time() - 3600
        future = time.time() + 3600
        cm.save([
            {"name": "old", "value": "1", "domain": "a.com", "expires": past},
            {"name": "new", "value": "2", "domain": "b.com", "expires": future},
        ])
        removed = cm.remove_expired()
        assert removed == 1
        remaining = cm.load()
        assert len(remaining) == 1
        assert remaining[0]["name"] == "new"

    def test_has_session_cookie_true(self, profile_dir):
        from agent.browser.cookie_manager import CookieManager
        cm = CookieManager(profile_dir)
        cm.save([{"name": "session", "value": "abc", "domain": ".github.com", "expires": time.time() + 3600}])
        assert cm.has_session_cookie("github.com") is True

    def test_has_session_cookie_false_when_expired(self, profile_dir):
        from agent.browser.cookie_manager import CookieManager
        cm = CookieManager(profile_dir)
        cm.save([{"name": "old", "value": "abc", "domain": ".github.com", "expires": time.time() - 1}])
        assert cm.has_session_cookie("github.com") is False

    def test_to_storage_state(self, profile_dir):
        from agent.browser.cookie_manager import CookieManager
        cm = CookieManager(profile_dir)
        cookies = [{"name": "tok", "value": "val", "domain": "x.com"}]
        cm.save(cookies)
        state = cm.to_storage_state()
        assert state["cookies"] == cookies
        assert state["origins"] == []

    def test_import_storage_state(self, profile_dir):
        from agent.browser.cookie_manager import CookieManager
        cm = CookieManager(profile_dir)
        state = {"cookies": [{"name": "k", "value": "v", "domain": "d.com"}], "origins": []}
        cm.import_storage_state(state)
        assert cm.load()[0]["name"] == "k"


# ---------------------------------------------------------------------------
# StorageManager
# ---------------------------------------------------------------------------


class TestStorageManager:
    def test_save_and_load_local_storage(self, profile_dir):
        from agent.browser.storage_manager import StorageManager
        sm = StorageManager(profile_dir)
        origins = [{"origin": "https://example.com", "localStorage": [{"name": "key", "value": "val"}]}]
        sm.save_local_storage(origins)
        assert sm.load_local_storage() == origins

    def test_save_and_load_session_storage(self, profile_dir):
        from agent.browser.storage_manager import StorageManager
        sm = StorageManager(profile_dir)
        origins = [{"origin": "https://example.com", "sessionStorage": [{"name": "k", "value": "v"}]}]
        sm.save_session_storage(origins)
        assert sm.load_session_storage() == origins

    def test_save_and_load_indexeddb(self, profile_dir):
        from agent.browser.storage_manager import StorageManager
        sm = StorageManager(profile_dir)
        data = {"db": "test", "store": {"key": "value"}}
        sm.save_indexeddb(data)
        assert sm.load_indexeddb() == data

    def test_clear_removes_all(self, profile_dir):
        from agent.browser.storage_manager import StorageManager
        sm = StorageManager(profile_dir)
        sm.save_local_storage([{"origin": "x"}])
        sm.save_session_storage([{"origin": "y"}])
        sm.clear()
        assert sm.load_local_storage() == []
        assert sm.load_session_storage() == []

    def test_to_storage_state(self, profile_dir):
        from agent.browser.storage_manager import StorageManager
        sm = StorageManager(profile_dir)
        origins = [{"origin": "https://a.com"}]
        sm.save_local_storage(origins)
        state = sm.to_storage_state(cookies=[{"name": "c"}])
        assert state["cookies"] == [{"name": "c"}]
        assert state["origins"] == origins

    def test_import_storage_state(self, profile_dir):
        from agent.browser.storage_manager import StorageManager
        sm = StorageManager(profile_dir)
        state = {"cookies": [], "origins": [{"origin": "https://b.com"}]}
        sm.import_storage_state(state)
        assert sm.load_local_storage() == [{"origin": "https://b.com"}]


# ---------------------------------------------------------------------------
# SnapshotManager
# ---------------------------------------------------------------------------


class TestSnapshotManager:
    def test_save_creates_file(self, profile_dir):
        from agent.browser.snapshot import SnapshotManager
        sm = SnapshotManager(profile_dir)
        path = sm.save(cookies=[{"name": "c"}], label="test")
        assert path.exists()
        assert "test" in path.name

    def test_restore_latest(self, profile_dir):
        from agent.browser.snapshot import SnapshotManager
        sm = SnapshotManager(profile_dir)
        sm.save(cookies=[{"name": "c1"}], open_tabs=["https://a.com"], label="snap1")
        snap = sm.restore()
        assert snap is not None
        assert snap.cookies == [{"name": "c1"}]
        assert "https://a.com" in snap.open_tabs

    def test_restore_returns_none_when_empty(self, profile_dir):
        from agent.browser.snapshot import SnapshotManager
        sm = SnapshotManager(profile_dir)
        assert sm.restore() is None

    def test_list_snapshots_sorted(self, profile_dir):
        from agent.browser.snapshot import SnapshotManager
        sm = SnapshotManager(profile_dir)
        sm.save(label="first")
        sm.save(label="second")
        snaps = sm.list_snapshots()
        assert len(snaps) == 2

    def test_clear_all(self, profile_dir):
        from agent.browser.snapshot import SnapshotManager
        sm = SnapshotManager(profile_dir)
        sm.save(label="a")
        sm.save(label="b")
        count = sm.clear_all()
        assert count == 2
        assert sm.list_snapshots() == []

    def test_restore_specific_path(self, profile_dir):
        from agent.browser.snapshot import SnapshotManager
        sm = SnapshotManager(profile_dir)
        path = sm.save(cookies=[{"name": "specific"}], label="target")
        sm.save(cookies=[{"name": "other"}], label="other")
        snap = sm.restore(snapshot_path=path)
        assert snap is not None
        assert snap.cookies == [{"name": "specific"}]


# ---------------------------------------------------------------------------
# LoginMemory
# ---------------------------------------------------------------------------


class TestLoginMemory:
    def test_record_and_is_logged_in(self, tmp_path):
        from agent.browser.login_memory import LoginMemory
        lm = LoginMemory(db_path=tmp_path / "sessions.db")
        lm.record_login("https://github.com", profile="default", approved=True)
        assert lm.is_logged_in("github.com", "default") is True

    def test_not_logged_in_when_no_record(self, tmp_path):
        from agent.browser.login_memory import LoginMemory
        lm = LoginMemory(db_path=tmp_path / "sessions.db")
        assert lm.is_logged_in("github.com", "default") is False

    def test_not_logged_in_when_invalid(self, tmp_path):
        from agent.browser.login_memory import LoginMemory
        lm = LoginMemory(db_path=tmp_path / "sessions.db")
        lm.record_login("https://github.com", profile="default", approved=True)
        lm.mark_invalid("github.com", "default")
        assert lm.is_logged_in("github.com", "default") is False

    def test_unapproved_session_not_returned(self, tmp_path):
        from agent.browser.login_memory import LoginMemory
        lm = LoginMemory(db_path=tmp_path / "sessions.db")
        lm.record_login("https://github.com", profile="default", approved=False)
        assert lm.is_logged_in("github.com", "default") is False

    def test_get_all_sessions(self, tmp_path):
        from agent.browser.login_memory import LoginMemory
        lm = LoginMemory(db_path=tmp_path / "sessions.db")
        lm.record_login("https://github.com", profile="work", approved=True)
        lm.record_login("https://stripe.com", profile="work", approved=True)
        sessions = lm.get_all_sessions(profile="work")
        assert len(sessions) == 2

    def test_needs_approval_sensitive_domain(self, tmp_path):
        from agent.browser.login_memory import LoginMemory
        lm = LoginMemory(db_path=tmp_path / "sessions.db")
        assert lm.needs_approval("https://github.com") is True
        assert lm.needs_approval("https://example.com") is False

    def test_approve_session(self, tmp_path):
        from agent.browser.login_memory import LoginMemory
        lm = LoginMemory(db_path=tmp_path / "sessions.db")
        lm.record_login("https://github.com", profile="default", approved=False)
        lm.approve_session("github.com", profile="default")
        assert lm.is_logged_in("github.com", "default") is True

    def test_touch_updates_last_seen(self, tmp_path):
        from agent.browser.login_memory import LoginMemory
        lm = LoginMemory(db_path=tmp_path / "sessions.db")
        lm.record_login("https://github.com", profile="default", approved=True)
        before = lm.get_all_sessions(profile="default")[0]["last_seen"]
        time.sleep(0.01)
        lm.touch("github.com", profile="default")
        after = lm.get_all_sessions(profile="default")[0]["last_seen"]
        assert after >= before

    def test_project_isolation(self, tmp_path):
        from agent.browser.login_memory import LoginMemory
        lm = LoginMemory(db_path=tmp_path / "sessions.db")
        lm.record_login("https://github.com", profile="default", project="proj-a", approved=True)
        assert lm.is_logged_in("github.com", "default", project="proj-a") is True
        assert lm.is_logged_in("github.com", "default", project="proj-b") is False


# ---------------------------------------------------------------------------
# ProfileRecovery
# ---------------------------------------------------------------------------


class TestProfileRecovery:
    def test_backup_creates_directory(self, profile_dir):
        from agent.browser.recovery import ProfileRecovery
        rec = ProfileRecovery(profile_dir)
        backup = rec.backup(label="test")
        assert backup.exists()
        assert "test" in backup.name

    def test_list_backups(self, profile_dir):
        from agent.browser.recovery import ProfileRecovery
        rec = ProfileRecovery(profile_dir)
        rec.backup(label="a")
        rec.backup(label="b")
        backups = rec.list_backups()
        assert len(backups) == 2

    def test_restore_from_backup(self, tmp_path):
        from agent.browser.recovery import ProfileRecovery
        from agent.browser.cookie_manager import CookieManager
        pdir = tmp_path / "restore-test"
        pdir.mkdir()
        (pdir / "chromium").mkdir()

        # Write metadata so health check passes after restore
        meta_file = pdir / "kryth_profile.json"
        meta_file.write_text('{"name": "restore-test", "profile_type": "default"}', encoding="utf-8")

        cm = CookieManager(pdir)
        cm.save([{"name": "tok", "value": "orig", "domain": "a.com"}])

        rec = ProfileRecovery(pdir)
        backup = rec.backup(label="orig")

        # Corrupt the cookies
        cm.save([{"name": "tok", "value": "corrupted", "domain": "a.com"}])

        ok = rec.restore(backup_path=backup)
        assert ok is True
        restored = CookieManager(pdir).load()
        assert restored[0]["value"] == "orig"

    def test_prune_keeps_max_backups(self, profile_dir):
        from agent.browser.recovery import ProfileRecovery
        rec = ProfileRecovery(profile_dir, max_backups=2)
        for i in range(4):
            time.sleep(0.01)
            rec.backup(label=str(i))
        assert len(rec.list_backups()) <= 2

    def test_is_healthy_true_with_valid_metadata(self, tmp_path):
        from agent.browser.recovery import ProfileRecovery
        pdir = tmp_path / "healthy"
        pdir.mkdir()
        (pdir / "kryth_profile.json").write_text('{"name": "healthy"}', encoding="utf-8")
        rec = ProfileRecovery(pdir)
        assert rec.is_healthy() is True

    def test_is_healthy_false_without_metadata(self, tmp_path):
        from agent.browser.recovery import ProfileRecovery
        pdir = tmp_path / "no-meta"
        pdir.mkdir()
        rec = ProfileRecovery(pdir)
        assert rec.is_healthy() is False


# ---------------------------------------------------------------------------
# ProfileEncryption
# ---------------------------------------------------------------------------


class TestProfileEncryption:
    def test_noop_when_no_password(self):
        from agent.browser.encryption import ProfileEncryption
        enc = ProfileEncryption(password=None)
        assert enc.enabled is False
        data = b"plain text"
        assert enc.encrypt_bytes(data) == data
        assert enc.decrypt_bytes(data) == data

    def test_encrypt_decrypt_roundtrip(self, tmp_path):
        try:
            from cryptography.fernet import Fernet  # noqa: F401
        except ImportError:
            pytest.skip("cryptography not installed")

        from agent.browser.encryption import ProfileEncryption
        salt_file = tmp_path / ".salt"
        enc = ProfileEncryption(password="secret123", salt_file=salt_file)
        assert enc.enabled is True

        original = b"sensitive data"
        ciphertext = enc.encrypt_bytes(original)
        assert ciphertext != original
        assert enc.decrypt_bytes(ciphertext) == original

    def test_json_roundtrip(self, tmp_path):
        try:
            from cryptography.fernet import Fernet  # noqa: F401
        except ImportError:
            pytest.skip("cryptography not installed")

        from agent.browser.encryption import ProfileEncryption
        enc = ProfileEncryption(password="pw", salt_file=tmp_path / ".salt")
        obj = {"cookies": [{"name": "tok", "value": "secret"}]}
        blob = enc.encrypt_json(obj)
        assert enc.decrypt_json(blob) == obj

    def test_wrong_password_raises(self, tmp_path):
        try:
            from cryptography.fernet import Fernet  # noqa: F401
        except ImportError:
            pytest.skip("cryptography not installed")

        from agent.browser.encryption import ProfileEncryption
        enc1 = ProfileEncryption(password="correct", salt_file=tmp_path / ".salt1")
        enc2 = ProfileEncryption(password="wrong", salt_file=tmp_path / ".salt2")
        cipher = enc1.encrypt_bytes(b"data")
        with pytest.raises(ValueError, match="Decryption failed"):
            enc2.decrypt_bytes(cipher)


# ---------------------------------------------------------------------------
# BrowserMemoryBridge
# ---------------------------------------------------------------------------


class TestBrowserMemoryBridge:
    def test_no_op_without_memory_manager(self):
        from agent.browser.memory_bridge import BrowserMemoryBridge
        bridge = BrowserMemoryBridge(memory_manager=None)
        # All calls should be silent no-ops
        bridge.record_login("https://github.com")
        bridge.record_navigation("https://github.com", title="GitHub")
        bridge.record_automation("submit PR", "https://github.com", success=True)
        bridge.record_profile_switch("default", "work")

    def test_get_recent_logins_without_mm(self):
        from agent.browser.memory_bridge import BrowserMemoryBridge
        bridge = BrowserMemoryBridge(memory_manager=None)
        assert bridge.get_recent_logins("github.com") == []

    def test_profile_switch_updates_internal_name(self):
        from agent.browser.memory_bridge import BrowserMemoryBridge
        bridge = BrowserMemoryBridge(memory_manager=None, profile_name="default")
        bridge.record_profile_switch("default", "work")
        assert bridge._profile == "work"


# ---------------------------------------------------------------------------
# Tool handler functions (JSON round-trip)
# ---------------------------------------------------------------------------


class TestToolHandlers:
    def _patch_mgr(self, monkeypatch, tmp_path):
        """Patch BrowserProfileManager to use a tmp dir."""
        from agent.browser import profile_manager as pm_mod

        class _FakeMgr:
            def __init__(self, *a, **kw):
                self._inner = pm_mod.BrowserProfileManager(base_dir=tmp_path / "profiles")

            def __getattr__(self, name):
                return getattr(self._inner, name)

        monkeypatch.setattr(
            "agent.tools._browser_profile._mgr",
            lambda project_hash="": pm_mod.BrowserProfileManager(base_dir=tmp_path / "profiles"),
        )

    def _patch_login(self, monkeypatch, tmp_path):
        from agent.browser import login_memory as lm_mod

        monkeypatch.setattr(
            "agent.tools._browser_profile._login_memory",
            lambda: lm_mod.LoginMemory(db_path=tmp_path / "sessions.db"),
        )

    def test_browser_profile_create(self, monkeypatch, tmp_path):
        self._patch_mgr(monkeypatch, tmp_path)
        from agent.tools._browser_profile import browser_profile_create
        result = json.loads(browser_profile_create(name="gh"))
        assert result["status"] == "created"
        assert result["name"] == "gh"

    def test_browser_profile_list(self, monkeypatch, tmp_path):
        self._patch_mgr(monkeypatch, tmp_path)
        from agent.tools._browser_profile import browser_profile_create, browser_profile_list
        browser_profile_create(name="p1")
        browser_profile_create(name="p2")
        result = json.loads(browser_profile_list())
        names = [p["name"] for p in result["profiles"]]
        assert "p1" in names
        assert "p2" in names

    def test_browser_profile_switch(self, monkeypatch, tmp_path):
        self._patch_mgr(monkeypatch, tmp_path)
        from agent.tools._browser_profile import browser_profile_create, browser_profile_switch
        browser_profile_create(name="sw")
        result = json.loads(browser_profile_switch("sw"))
        assert result["status"] == "switched"
        assert result["name"] == "sw"

    def test_browser_profile_backup_and_restore(self, monkeypatch, tmp_path):
        self._patch_mgr(monkeypatch, tmp_path)
        from agent.tools._browser_profile import (
            browser_profile_create,
            browser_profile_backup,
            browser_profile_restore,
        )
        browser_profile_create(name="bk")
        backup_result = json.loads(browser_profile_backup(name="bk", label="test"))
        assert backup_result["status"] == "backed_up"

        restore_result = json.loads(browser_profile_restore(name="bk"))
        assert restore_result["status"] == "restored"

    def test_browser_profile_clear(self, monkeypatch, tmp_path):
        self._patch_mgr(monkeypatch, tmp_path)
        from agent.tools._browser_profile import browser_profile_create, browser_profile_clear
        browser_profile_create(name="cl")
        result = json.loads(browser_profile_clear(name="cl"))
        assert result["status"] == "cleared"

    def test_browser_snapshot_save_restore(self, monkeypatch, tmp_path):
        self._patch_mgr(monkeypatch, tmp_path)
        from agent.tools._browser_profile import (
            browser_profile_create,
            browser_snapshot_save,
            browser_snapshot_restore,
        )
        browser_profile_create(name="snap")
        save_r = json.loads(browser_snapshot_save(profile="snap", label="mysnap", open_tabs=["https://x.com"]))
        assert save_r["status"] == "saved"

        restore_r = json.loads(browser_snapshot_restore(profile="snap"))
        assert restore_r["status"] == "restored"
        assert "https://x.com" in restore_r["open_tabs"]

    def test_browser_session_record_login_sensitive_requires_approval(self, monkeypatch, tmp_path):
        self._patch_login(monkeypatch, tmp_path)
        from agent.tools._browser_profile import browser_session_record_login
        result = json.loads(browser_session_record_login("https://github.com", approved=False))
        assert result["status"] == "approval_required"

    def test_browser_session_record_login_approved(self, monkeypatch, tmp_path):
        self._patch_login(monkeypatch, tmp_path)
        from agent.tools._browser_profile import browser_session_record_login
        result = json.loads(browser_session_record_login("https://github.com", approved=True))
        assert result["status"] == "recorded"

    def test_browser_session_status_not_logged_in(self, monkeypatch, tmp_path):
        self._patch_login(monkeypatch, tmp_path)
        from agent.tools._browser_profile import browser_session_status
        result = json.loads(browser_session_status("https://github.com"))
        assert result["logged_in"] is False
        assert result["sensitive"] is True

    def test_browser_profile_get_user_data_dir(self, monkeypatch, tmp_path):
        self._patch_mgr(monkeypatch, tmp_path)
        from agent.tools._browser_profile import browser_profile_create, browser_profile_get_user_data_dir
        browser_profile_create(name="udd")
        result = json.loads(browser_profile_get_user_data_dir(name="udd"))
        assert "user_data_dir" in result
        assert Path(result["user_data_dir"]).exists()


# ---------------------------------------------------------------------------
# Tool spec structure
# ---------------------------------------------------------------------------


class TestToolSpecs:
    def test_all_specs_have_required_fields(self):
        from agent.tools._browser_profile_specs import BROWSER_PROFILE_TOOL_SPECS
        for spec in BROWSER_PROFILE_TOOL_SPECS:
            assert spec["type"] == "function"
            fn = spec["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_spec_names_match_tool_handlers(self):
        from agent.tools._browser_profile_specs import BROWSER_PROFILE_TOOL_SPECS
        from agent.tools._browser_profile import BROWSER_PROFILE_TOOLS
        spec_names = {s["function"]["name"] for s in BROWSER_PROFILE_TOOL_SPECS}
        handler_names = set(BROWSER_PROFILE_TOOLS.keys())
        assert spec_names == handler_names

    def test_specs_registered_in_tool_specs(self):
        from agent.tools import TOOL_SPECS
        spec_names = {s["function"]["name"] for s in TOOL_SPECS}
        assert "browser_profile_create" in spec_names
        assert "browser_snapshot_save" in spec_names
        assert "browser_session_status" in spec_names

    def test_tools_registered_in_tools_dict(self):
        from agent.tools import TOOLS
        assert "browser_profile_create" in TOOLS
        assert "browser_session_record_login" in TOOLS
        assert "browser_profile_get_user_data_dir" in TOOLS
