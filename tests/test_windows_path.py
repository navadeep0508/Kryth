from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from kryth.platform import windows


class FakeRegistryKey:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_READ = 0x20019
    KEY_SET_VALUE = 0x0002
    REG_EXPAND_SZ = 2
    REG_SZ = 1

    def __init__(self, path="", path_type=REG_EXPAND_SZ):
        self.values = {} if path == "" else {"Path": (path, path_type)}
        self.writes = []

    def OpenKey(self, *_args):
        return FakeRegistryKey(self.values)

    def CreateKeyEx(self, *_args):
        return FakeRegistryKey(self.values)

    def QueryValueEx(self, _key, name):
        try:
            return self.values[name]
        except KeyError:
            raise FileNotFoundError(name)

    def SetValueEx(self, _key, name, _reserved, value_type, value):
        self.values[name] = (value, value_type)
        self.writes.append((name, value_type, value))


def test_detects_sysconfig_scripts_path_before_prefix_guess(monkeypatch, tmp_path):
    expected = tmp_path / "configured" / "Scripts"
    monkeypatch.setattr(windows.sysconfig, "get_path", lambda key, **_kwargs: str(expected) if key == "scripts" else None)
    monkeypatch.setattr(windows.sysconfig, "get_scheme_names", lambda: ("nt", "nt_user"))
    monkeypatch.setattr(windows.sys, "argv", ["python.exe", "-m", "kryth"])

    assert windows.get_scripts_directory() == expected


def test_user_install_uses_actual_user_scripts_when_module_is_in_user_site(monkeypatch, tmp_path):
    user_scripts = tmp_path / "Roaming" / "Python" / "Scripts"
    user_site = tmp_path / "Roaming" / "Python" / "site-packages"
    user_site.mkdir(parents=True)
    (user_scripts).mkdir(parents=True)
    (user_scripts / "kryth.exe").touch()

    def get_path(key, scheme=None):
        if key == "scripts" and scheme == "nt":
            return str(tmp_path / "Python" / "Scripts")
        if key == "scripts" and scheme == "nt_user":
            return str(user_scripts)
        if key == "purelib" and scheme == "nt_user":
            return str(user_site)
        if key == "scripts":
            return str(tmp_path / "Python" / "Scripts")
        return None

    package = SimpleNamespace(__file__=str(user_site / "kryth" / "__init__.py"))
    monkeypatch.setitem(sys.modules, "kryth", package)
    monkeypatch.setattr(windows.sysconfig, "get_path", get_path)
    monkeypatch.setattr(windows.sysconfig, "get_scheme_names", lambda: ("nt", "nt_user"))
    monkeypatch.setattr(windows.sys, "argv", ["python.exe", "-m", "kryth"])

    assert windows.get_scripts_directory() == user_scripts


def test_adds_scripts_once_preserves_user_path_and_broadcasts(monkeypatch, tmp_path):
    scripts = tmp_path / "Python" / "Scripts"
    registry = FakeWinreg(r"%USERPROFILE%\bin", FakeWinreg.REG_SZ)
    broadcast = []
    monkeypatch.setattr(windows, "_is_windows", lambda: True)
    monkeypatch.setattr(windows, "_load_winreg", lambda: registry)
    monkeypatch.setattr(windows, "broadcast_environment_change", lambda: broadcast.append(True) or True)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert windows.add_scripts_directory_to_user_path(scripts)
    stored_path, stored_type = registry.values["Path"]
    assert stored_path == f"%USERPROFILE%\\bin;{scripts}"
    assert stored_type == FakeWinreg.REG_SZ
    assert broadcast == [True]

    assert windows.add_scripts_directory_to_user_path(scripts)
    assert len(registry.writes) == 1
    assert broadcast == [True]


def test_duplicate_detection_is_case_insensitive_and_expands_environment(monkeypatch, tmp_path):
    scripts = tmp_path / "Python" / "Scripts"
    registry = FakeWinreg(f"%USERPROFILE%\\python\\scripts")
    monkeypatch.setattr(windows, "_is_windows", lambda: True)
    monkeypatch.setattr(windows, "_load_winreg", lambda: registry)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert windows.is_scripts_directory_in_user_path(scripts)


def test_add_path_does_not_overwrite_registry_path_when_winreg_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(windows, "_is_windows", lambda: True)
    monkeypatch.setattr(windows, "_load_winreg", lambda: None)

    assert not windows.add_scripts_directory_to_user_path(tmp_path / "Scripts")


def test_adds_path_when_user_registry_value_is_missing(monkeypatch, tmp_path):
    registry = FakeWinreg()
    monkeypatch.setattr(windows, "_is_windows", lambda: True)
    monkeypatch.setattr(windows, "_load_winreg", lambda: registry)
    monkeypatch.setattr(windows, "broadcast_environment_change", lambda: True)
    scripts = tmp_path / "Python" / "Scripts"

    assert windows.add_scripts_directory_to_user_path(scripts)
    assert registry.values["Path"] == (str(scripts), FakeWinreg.REG_EXPAND_SZ)


def test_setup_path_requires_the_installed_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(windows, "_is_windows", lambda: True)
    monkeypatch.setattr(windows, "get_scripts_directory", lambda: tmp_path / "Scripts")

    result = windows.configure_windows_path()

    assert not result.success
    assert "launcher was not found" in result.message


def test_setup_path_command_does_not_load_kryth_configuration(monkeypatch):
    from click.testing import CliRunner
    import main as cli

    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli, "load_config", lambda **_kwargs: pytest.fail("PATH recovery must not load config"))
    monkeypatch.setattr(
        windows,
        "configure_windows_path",
        lambda: windows.WindowsPathResult(True, True, r"C:\Python\Scripts", "already configured"),
    )

    result = CliRunner().invoke(cli.main, ["setup", "--path"])

    assert result.exit_code == 0
    assert "already configured" in result.output


def test_python_module_entrypoint_calls_existing_cli(monkeypatch):
    import kryth.__main__ as module_entry

    called = []
    monkeypatch.setattr(module_entry, "main", lambda: called.append(True))
    module_entry.run()
    assert called == [True]
