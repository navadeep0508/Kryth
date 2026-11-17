from __future__ import annotations

import asyncio

from click.testing import CliRunner

import main as cli
from config.config import Config


class _Engine:
    async def connect(self):
        return {"connected": True, "tab_count": 1, "browser_kind": "kuba-cdp"}


class _Manager:
    def __init__(self, engine):
        self.engine = engine
        self.tools = [object()]

    def discover(self):
        return self.tools


def test_browser_group_exposes_setup_and_doctor_commands():
    result = CliRunner().invoke(cli.main, ["browser", "--help"])

    assert result.exit_code == 0
    assert "setup" in result.output
    assert "doctor" in result.output


def test_browser_doctor_reports_browser_prerequisites(monkeypatch):
    config = Config(browser_plugin={"enabled": True})
    monkeypatch.setattr(cli, "load_config", lambda cwd: config)
    monkeypatch.setattr(cli, "_create_browser_manager", lambda _config: _Manager(_Engine()), raising=False)
    monkeypatch.setattr(
        cli,
        "_browser_probe",
        lambda _manager: {
            "engine": "KUBABrowserEngine",
            "browser": "Chrome",
            "browser_path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "daemon_running": False,
            "cdp_ready": False,
            "remote_debugging_enabled": False,
        },
        raising=False,
    )

    result = CliRunner().invoke(cli.main, ["browser", "doctor"])

    assert result.exit_code == 0
    assert "KUBA browser setup check" in result.output
    assert "Chrome" in result.output
    assert "remote debugging" in result.output.lower()
    assert "kryth browser setup" in result.output


def test_browser_setup_connects_without_provider_model(monkeypatch):
    config = Config(browser_plugin={"enabled": True})
    manager = _Manager(_Engine())
    monkeypatch.setattr(cli, "load_config", lambda cwd: config)
    monkeypatch.setattr(cli, "_create_browser_manager", lambda _config: manager, raising=False)

    result = CliRunner().invoke(cli.main, ["browser", "setup"])

    assert result.exit_code == 0
    assert "KUBA browser is ready" in result.output
    assert "tabs=1" in result.output


def test_browser_setup_shows_remote_debugging_action_without_traceback(monkeypatch):
    class FailingEngine:
        async def connect(self):
            raise RuntimeError(
                "remote-debugging-setup: opened chrome://inspect/#remote-debugging; enable remote debugging and approve Chrome."
            )

    config = Config(browser_plugin={"enabled": True})
    monkeypatch.setattr(cli, "load_config", lambda cwd: config)
    monkeypatch.setattr(cli, "_create_browser_manager", lambda _config: _Manager(FailingEngine()), raising=False)

    result = CliRunner().invoke(cli.main, ["browser", "setup"])

    assert result.exit_code != 0
    assert "chrome://inspect/#remote-debugging" in result.output
    assert "Traceback" not in result.output
