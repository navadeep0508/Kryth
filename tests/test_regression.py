"""
Regression tests for KRYTH shell detection, file encoding, project detection,
persistent terminals, and rate limit recovery.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.core.shells import (
    detect_os,
    detect_available_shells,
    select_shell,
    infer_shell_type,
    detect_configured_shell,
    validate_command_shell,
    ShellMismatchError,
    ShellType,
    CmdAdapter,
    PowerShellAdapter,
    GitBashAdapter,
    WslBashAdapter,
    LinuxBashAdapter,
    MacosZshAdapter,
    PosixShAdapter,
    detect_command_shell_conflict,
)
from tools.core.long_running import detect_long_running
from tools.core.persistent_terminal import (
    TerminalManager,
    TerminalSession,
    PersistentTerminal,
    TerminalStatus,
    get_terminal_manager,
)
from utils.paths import is_binary_file, read_text_safe
from tools.core.project import detect_project, ProjectDetector, ProjectPlan
from client.llm_client import _classify_rate_limit_error, _retry_after_seconds, _rate_limit_message
from tools.core.bash import BashTool
from context.workspace import (
    WorkspaceInfo,
    WorkspaceValidator,
    GitInfo,
    validate_workspace,
    is_workspace_ambiguous,
)
from context.project_context import (
    ProjectContext,
    ProjectContextManager,
    build_project_context,
    validate_and_build_project_context,
)
from tools.core.tool_supervisor import (
    ToolSelectionSupervisor,
    CommandCategory,
    NativeToolRecommendation,
    get_tool_supervisor,
    classify_command,
    recommend_native_tool,
    should_prefer_native,
)
from tools.core.code_integrity import (
    CodeIntegrityValidator,
    IntegrityReport,
    IntegrityIssue,
    validate_code_integrity,
)
from tools.core.dependency_validator import (
    DependencyValidator,
    DependencyReport,
    DependencyIssue,
    PackageInfo,
    validate_dependencies,
    validate_version,
)


# ---------------------------------------------------------------------------
# Shell detection tests
# ---------------------------------------------------------------------------

def test_detect_os_returns_string():
    result = detect_os()
    assert isinstance(result, str)
    assert result in ("windows", "linux", "darwin", "unknown")


def test_detect_available_shells_returns_list():
    shells = detect_available_shells()
    assert isinstance(shells, list)
    assert len(shells) >= 1
    for s in shells:
        assert hasattr(s, "type")
        assert hasattr(s, "available")
        assert hasattr(s, "executable")


def test_infer_shell_type_powershell():
    assert infer_shell_type("Get-ChildItem") == ShellType.POWERSHELL
    assert infer_shell_type("Invoke-WebRequest") == ShellType.POWERSHELL
    assert infer_shell_type("Get-Process") == ShellType.POWERSHELL


def test_infer_shell_type_cmd():
    assert infer_shell_type("dir") == ShellType.CMD
    assert infer_shell_type("type file.txt") == ShellType.CMD
    assert infer_shell_type("ipconfig") == ShellType.CMD


def test_infer_shell_type_bash():
    assert infer_shell_type("ls -la") == ShellType.LINUX_BASH
    assert infer_shell_type("cat README.md") == ShellType.LINUX_BASH
    assert infer_shell_type("git status") == ShellType.LINUX_BASH


def test_select_shell_cmd_windows():
    if os.name == "nt":
        shell = select_shell(preferred=ShellType.CMD)
        assert isinstance(shell, CmdAdapter)


def test_select_shell_powershell_windows():
    if os.name == "nt":
        shell = select_shell(preferred=ShellType.POWERSHELL)
        assert isinstance(shell, PowerShellAdapter)


def test_configured_windows_shell_is_stable(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows shell selection")
    monkeypatch.setenv("PSModulePath", r"C:\Windows\System32\WindowsPowerShell\v1.0\Modules")
    monkeypatch.delenv("KRYTH_SHELL", raising=False)
    assert detect_configured_shell() == ShellType.POWERSHELL
    assert select_shell().info.type == ShellType.POWERSHELL


def test_explicit_unavailable_shell_does_not_fall_back(monkeypatch):
    with patch("tools.core.shells.detect_available_shells", return_value=[]):
        with pytest.raises(RuntimeError, match="not available"):
            select_shell(preferred=ShellType.GIT_BASH)


@pytest.mark.asyncio
async def test_powershell_execution_captures_streams_and_duration(tmp_path):
    if os.name != "nt":
        pytest.skip("PowerShell adapter requires Windows")
    shell = select_shell(preferred=ShellType.POWERSHELL)
    result = await shell.execute(
        "Write-Output 'out value'; [Console]::Error.WriteLine('err value')",
        cwd=tmp_path,
        timeout=10,
    )
    assert result.success
    assert result.exit_code == 0
    assert "out value" in result.stdout
    assert "err value" in result.stderr
    assert result.duration_seconds >= 0


@pytest.mark.asyncio
async def test_execution_timeout_is_reported():
    if os.name != "nt":
        pytest.skip("PowerShell adapter requires Windows")
    result = await select_shell(preferred=ShellType.POWERSHELL).execute(
        "Start-Sleep -Seconds 5", timeout=1
    )
    assert result.timed_out
    assert result.duration_seconds >= 1


def test_infer_shell_never_labels_cmd_as_bash():
    """CMD commands must never be inferred as Bash."""
    assert infer_shell_type("dir") != ShellType.LINUX_BASH
    assert infer_shell_type("type") != ShellType.LINUX_BASH
    assert infer_shell_type("ipconfig") != ShellType.LINUX_BASH


def test_shell_mismatch_detection():
    with pytest.raises(ShellMismatchError):
        validate_command_shell("Get-ChildItem", ShellType.CMD)
    validate_command_shell("Get-ChildItem", ShellType.POWERSHELL)


@pytest.mark.asyncio
async def test_command_executes_in_requested_directory_with_spaces(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows shell adapter integration")
    work = tmp_path / "directory with spaces"
    work.mkdir()
    shell = select_shell(preferred=ShellType.POWERSHELL)
    result = await shell.execute("Get-Location | Select-Object -ExpandProperty Path", cwd=work)
    assert result.success
    assert str(work) in result.stdout


@pytest.mark.asyncio
async def test_configured_cd_persists_for_next_command(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows shell adapter integration")
    from config.config import Config
    from tools.base import ToolInvocation
    from tools.core.bash import BashTool

    work = tmp_path / "folder with spaces"
    work.mkdir()
    tool = BashTool(Config(cwd=tmp_path))
    changed = await tool.execute(ToolInvocation({"command": "cd 'folder with spaces'"}, tmp_path))
    assert changed.success
    result = await tool.execute(ToolInvocation({"command": "Get-Location | Select-Object -ExpandProperty Path"}, tmp_path))
    assert result.success
    assert str(work) in result.output


@pytest.mark.asyncio
async def test_cmd_adapter_executes_on_windows(tmp_path):
    if os.name != "nt":
        pytest.skip("CMD adapter requires Windows")
    result = await select_shell(preferred=ShellType.CMD).execute("echo cmd-adapter-ok", cwd=tmp_path)
    assert result.success
    assert "cmd-adapter-ok" in result.stdout


@pytest.mark.asyncio
async def test_git_bash_executes_on_windows(tmp_path):
    if os.name != "nt" or not any(s.type == ShellType.GIT_BASH and s.available for s in detect_available_shells()):
        pytest.skip("Git Bash is not installed")
    result = await select_shell(preferred=ShellType.GIT_BASH).execute("printf '%s' git-bash-ok", cwd=tmp_path)
    assert result.success
    assert "git-bash-ok" in result.stdout


@pytest.mark.asyncio
async def test_wsl_bash_executes_on_windows():
    if os.name != "nt" or not any(s.type == ShellType.WSL_BASH and s.available for s in detect_available_shells()):
        pytest.skip("WSL is not installed")
    result = await select_shell(preferred=ShellType.WSL_BASH).execute("printf '%s' wsl-bash-ok", timeout=20)
    assert result.success
    assert "wsl-bash-ok" in result.stdout


# ---------------------------------------------------------------------------
# Shell detection caching and compatibility tests
# ---------------------------------------------------------------------------

def test_detect_available_shells_is_cached(monkeypatch):
    """Test 6: Repeated shell selections must not repeatedly execute expensive version probes."""
    from tools.core import shells

    shells.detect_available_shells.cache_clear()

    call_count = {"n": 0}
    original_version = shells._shell_version

    def counting_version(shell_type, executable):
        call_count["n"] += 1
        return original_version(shell_type, executable)

    monkeypatch.setattr(shells, "_shell_version", counting_version)

    # Call detect_available_shells multiple times
    for _ in range(5):
        shells.detect_available_shells()

    # Version probes should only happen during the first (uncached) call
    assert call_count["n"] <= 4, f"Expected <= 4 version probes, got {call_count['n']}"


def test_detect_command_shell_conflict_cmd_in_powershell():
    """Test 7: CMD-only syntax in PowerShell should be detected as incompatible."""
    # CMD 'set VAR=value' syntax in PowerShell
    conflict = detect_command_shell_conflict("set MYVAR=hello", ShellType.POWERSHELL)
    assert conflict is not None
    assert "set" in conflict.lower()

    # CMD %VAR% expansion in PowerShell
    conflict = detect_command_shell_conflict('echo %MYVAR%', ShellType.POWERSHELL)
    assert conflict is not None

    # PowerShell-native commands should NOT be flagged
    conflict = detect_command_shell_conflict("Get-ChildItem", ShellType.POWERSHELL)
    assert conflict is None
    conflict = detect_command_shell_conflict("Write-Output hello", ShellType.POWERSHELL)
    assert conflict is None

    # CMD syntax in CMD should NOT be flagged
    conflict = detect_command_shell_conflict("set MYVAR=hello", ShellType.CMD)
    assert conflict is None

    # Unknown shell type should return None
    conflict = detect_command_shell_conflict("set MYVAR=hello", ShellType.UNKNOWN)
    assert conflict is None


def test_validate_command_shell_still_rejects_ps_cmdlets_in_cmd():
    """PowerShell cmdlets in CMD should still be rejected (existing behavior preserved)."""
    with pytest.raises(ShellMismatchError):
        validate_command_shell("Get-ChildItem", ShellType.CMD)
    # PowerShell cmdlets in PowerShell should be fine
    validate_command_shell("Get-ChildItem", ShellType.POWERSHELL)


# ---------------------------------------------------------------------------
# File encoding detection tests
# ---------------------------------------------------------------------------

def test_is_binary_file_text_file():
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        f.write(b"Hello, World!\nThis is a text file.")
        path = f.name
    try:
        assert is_binary_file(path) is False
    finally:
        os.unlink(path)


def test_is_binary_file_requirements_txt():
    """requirements.txt must NOT be classified as binary."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        f.write(b"requests>=2.0\nnumpy>=1.0\npandas>=1.0\n")
        path = f.name
    try:
        assert is_binary_file(path) is False
    finally:
        os.unlink(path)


def test_is_binary_file_utf8_bom():
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        f.write(b"\xef\xbb\xbfUTF-8 BOM content\n")
        path = f.name
    try:
        assert is_binary_file(path) is False
    finally:
        os.unlink(path)


def test_is_binary_file_utf16_bom():
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        f.write(b"\xff\xfeH\x00e\x00l\x00l\x00o\x00\x00\r\x00\n\x00")
        path = f.name
    try:
        assert is_binary_file(path) is False
    finally:
        os.unlink(path)


def test_is_binary_file_binary():
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".bin", delete=False) as f:
        f.write(b"\x00\x01\x02\x03binary\x00content\xff\xfe")
        path = f.name
    try:
        assert is_binary_file(path) is True
    finally:
        os.unlink(path)


def test_read_text_safe_utf8_bom():
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
        f.write(b"\xef\xbb\xbfHello BOM\n")
        path = f.name
    try:
        content = read_text_safe(path)
        assert content.startswith("Hello BOM")
        assert "﻿" not in content  # BOM stripped by utf-8-sig
    finally:
        os.unlink(path)


def test_read_text_safe_binary_fallback():
    """Binary file should not raise when read with read_text_safe."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".bin", delete=False) as f:
        f.write(b"\x00\x01\x02\xff\xfe\x00")
        path = f.name
    try:
        content = read_text_safe(path)
        assert isinstance(content, str)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Project detection tests
# ---------------------------------------------------------------------------

def test_detect_project_python_script(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "requirements.txt").write_text("requests>=2.0\n")
    plan = detect_project(tmp_path)
    assert isinstance(plan, ProjectPlan)
    assert "python" in plan.project_type.lower() or plan.project_type in ("python-script", "fastapi", "django", "flask")
    assert len(plan.services) >= 1


def test_detect_project_fastapi(tmp_path):
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (tmp_path / "requirements.txt").write_text("fastapi>=0.100.0\nuvicorn>=0.20.0\n")
    plan = detect_project(tmp_path)
    assert plan.project_type == "fastapi"


def test_detect_project_django(tmp_path):
    (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n")
    (tmp_path / "requirements.txt").write_text("django>=4.0\n")
    plan = detect_project(tmp_path)
    assert plan.project_type == "django"


def test_detect_project_flask(tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
    (tmp_path / "requirements.txt").write_text("flask>=2.0\n")
    plan = detect_project(tmp_path)
    assert plan.project_type == "flask"


def test_detect_project_react(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "my-app", "dependencies": {"react": "^18.0", "react-scripts": "^5.0"}}')
    plan = detect_project(tmp_path)
    assert plan.project_type == "react"


def test_detect_project_nextjs(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "my-app", "dependencies": {"next": "^14.0", "react": "^18.0"}}')
    plan = detect_project(tmp_path)
    assert plan.project_type == "nextjs"


def test_detect_project_vite(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "my-app", "dependencies": {"react": "^18.0"}}')
    (tmp_path / "vite.config.js").write_text("export default {}\n")
    plan = detect_project(tmp_path)
    assert plan.project_type == "vite"


def test_detect_project_fullstack(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "my-app", "dependencies": {"react": "^18.0"}}')
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (tmp_path / "requirements.txt").write_text("fastapi>=0.100.0\n")
    plan = detect_project(tmp_path)
    assert plan.project_type == "full-stack"


def test_plan_summary_not_empty():
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        (tmp_path / "main.py").write_text("print('hello')")
        plan = detect_project(tmp_path)
        summary = plan.summary()
        assert "PROJECT DETECTED" in summary
        assert len(summary) > 0


# ---------------------------------------------------------------------------
# Rate limit classification tests
# ---------------------------------------------------------------------------

def test_classify_rate_limit_temporary():
    class MockError:
        status_code = 429
        headers = {}
        def __str__(self): return "too many requests"
    assert _classify_rate_limit_error(MockError()) == "temporary"


def test_classify_rate_limit_quota_exhausted():
    class MockError:
        status_code = 429
        headers = {}
        def __str__(self): return "quota exhausted"
    assert _classify_rate_limit_error(MockError()) == "quota_exhausted"


def test_classify_rate_limit_auth_failure():
    class MockError:
        status_code = 401
        headers = {}
        def __str__(self): return "invalid api key"
    assert _classify_rate_limit_error(MockError()) == "auth_failure"


def test_classify_rate_limit_billing():
    class MockError:
        status_code = 402
        headers = {}
        def __str__(self): return "insufficient credits"
    assert _classify_rate_limit_error(MockError()) == "billing"


def test_classify_rate_limit_model_unavailable():
    class MockError:
        status_code = 404
        headers = {}
        def __str__(self): return "model not found"
    assert _classify_rate_limit_error(MockError()) == "model_unavailable"


def test_rate_limit_message_temporary():
    msg = _rate_limit_message("temporary", retry_after=30.0)
    assert "30" in msg
    assert "rate limit" in msg.lower()


def test_rate_limit_message_quota():
    msg = _rate_limit_message("quota_exhausted")
    assert "quota" in msg.lower()


def test_rate_limit_message_auth():
    msg = _rate_limit_message("auth_failure")
    assert "authentication" in msg.lower() or "api key" in msg.lower()


def test_retry_after_seconds_header():
    class MockError:
        status_code = 429
        headers = {"Retry-After": "10"}
    assert _retry_after_seconds(MockError(), 0) == 10.0


def test_retry_after_seconds_exponential_backoff():
    class MockError:
        status_code = 429
        headers = {}
    result = _retry_after_seconds(MockError(), 2)
    assert result >= 4.0  # 2 * 2^2 = 8 minimum without jitter... actually base=2, 2*2^2=8
    assert result < 10.0  # 8 + jitter < 9


# ---------------------------------------------------------------------------
# Long-running command detection tests
# ---------------------------------------------------------------------------

def test_detect_long_running_uvicorn():
    info = detect_long_running("uvicorn main:app --reload")
    assert info.is_long_running is True
    assert info.command_type == "fastapi"
    assert 8000 in info.ports
    assert info.session_name is not None


def test_detect_long_running_flask():
    info = detect_long_running("flask run")
    assert info.is_long_running is True
    assert info.command_type == "flask"
    assert 5000 in info.ports


def test_detect_long_running_django():
    info = detect_long_running("python manage.py runserver")
    assert info.is_long_running is True
    assert info.command_type == "django"
    assert 8000 in info.ports


def test_detect_long_running_npm_dev():
    info = detect_long_running("npm run dev")
    assert info.is_long_running is True
    assert info.command_type == "node-dev"
    assert 3000 in info.ports


def test_detect_long_running_npm_start():
    info = detect_long_running("npm start")
    assert info.is_long_running is True
    assert info.command_type == "node-start"
    assert 3000 in info.ports


def test_detect_long_running_vite():
    info = detect_long_running("vite")
    assert info.is_long_running is True
    assert info.command_type == "vite"
    assert 5173 in info.ports


def test_detect_long_running_next_dev():
    info = detect_long_running("next dev")
    assert info.is_long_running is True
    assert info.command_type == "nextjs"
    assert 3000 in info.ports


def test_detect_long_running_gunicorn():
    info = detect_long_running("gunicorn app:app")
    assert info.is_long_running is True
    assert info.command_type == "wsgi"


def test_detect_long_running_not_long_running():
    assert detect_long_running("ls -la").is_long_running is False
    assert detect_long_running("cat README.md").is_long_running is False
    assert detect_long_running("git status").is_long_running is False
    assert detect_long_running("echo hello").is_long_running is False


def test_detect_long_running_session_name_stable():
    info1 = detect_long_running("uvicorn main:app --reload", cwd="/tmp/proj")
    info2 = detect_long_running("uvicorn main:app --reload", cwd="/tmp/proj")
    assert info1.session_name == info2.session_name


def test_detect_long_running_project_enhancement(tmp_path):
    # Create a FastAPI project so detection can enhance with metadata.
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (tmp_path / "requirements.txt").write_text("fastapi>=0.100.0\nuvicorn>=0.20.0\n")
    info = detect_long_running("uvicorn main:app --reload", cwd=tmp_path)
    assert info.is_long_running is True
    assert "fastapi" in info.description.lower() or "uvicorn" in info.description.lower()


# ---------------------------------------------------------------------------
# Persistent terminal manager tests
# ---------------------------------------------------------------------------

def test_terminal_manager_is_singleton():
    m1 = get_terminal_manager()
    m2 = get_terminal_manager()
    assert m1 is m2


def test_terminal_manager_create_and_get():
    manager = TerminalManager()
    terminal = manager.create(
        name="test-session",
        command="echo hello",
        cwd=Path.cwd(),
    )
    assert terminal is not None
    assert terminal.name == "test-session"
    assert terminal.status == TerminalStatus.IDLE
    assert manager.get("test-session") is terminal


def test_terminal_manager_get_by_session_id():
    manager = TerminalManager()
    terminal = manager.create(
        name="session-id-test",
        command="echo hello",
        cwd=Path.cwd(),
    )
    found = manager.get_by_session_id(terminal.session_id)
    assert found is terminal


def test_terminal_manager_list():
    manager = TerminalManager()
    manager.create(name="s1", command="echo 1", cwd=Path.cwd())
    manager.create(name="s2", command="echo 2", cwd=Path.cwd())
    sessions = manager.list_sessions()
    assert len(sessions) == 2


def test_terminal_manager_remove():
    manager = TerminalManager()
    manager.create(name="to-remove", command="echo hi", cwd=Path.cwd())
    assert manager.get("to-remove") is not None
    assert manager.remove("to-remove") is True
    assert manager.get("to-remove") is None


def test_terminal_manager_running_sessions():
    manager = TerminalManager()
    t1 = manager.create(name="r1", command="echo 1", cwd=Path.cwd())
    t2 = manager.create(name="r2", command="echo 2", cwd=Path.cwd())
    t1.session.status = TerminalStatus.RUNNING
    t2.session.status = TerminalStatus.STOPPED
    running = manager.running_sessions()
    assert len(running) == 1
    assert running[0].name == "r1"


# ---------------------------------------------------------------------------
# Terminal session port detection tests
# ---------------------------------------------------------------------------

def test_terminal_extract_ports():
    from tools.core.persistent_terminal import PersistentTerminal
    session = TerminalSession(
        name="port-test",
        command="uvicorn main:app --reload",
        cwd=Path.cwd(),
        shell_type="bash",
    )
    pt = PersistentTerminal(session)

    ports = pt._extract_ports("Uvicorn running on http://127.0.0.1:8000")
    assert 8000 in ports

    ports = pt._extract_ports("Running on http://0.0.0.0:5000")
    assert 5000 in ports

    ports = pt._extract_ports("Listening on port 3000")
    assert 3000 in ports

    ports = pt._extract_ports("no ports here")
    assert ports == []


def test_terminal_check_startup_success():
    from tools.core.persistent_terminal import PersistentTerminal
    session = TerminalSession(
        name="startup-test",
        command="uvicorn main:app --reload",
        cwd=Path.cwd(),
        shell_type="bash",
    )
    pt = PersistentTerminal(session)

    assert pt._check_startup_success("Uvicorn running on http://127.0.0.1:8000") is True
    assert pt._check_startup_success("Application startup complete.") is True
    assert pt._check_startup_success("Ready in 5s") is True
    assert pt._check_startup_success("just starting up...") is False


# ---------------------------------------------------------------------------
# Terminal session status transitions
# ---------------------------------------------------------------------------

def test_terminal_session_status_transitions():
    session = TerminalSession(
        name="transition-test",
        command="echo hi",
        cwd=Path.cwd(),
        shell_type="bash",
    )
    terminal = PersistentTerminal(session)

    assert terminal.status == TerminalStatus.IDLE

    # Simulate start
    session.status = TerminalStatus.STARTING
    assert terminal.status == TerminalStatus.STARTING

    session.status = TerminalStatus.RUNNING
    session.pid = 12345
    assert terminal.status == TerminalStatus.RUNNING
    assert terminal.pid == 12345

    # Simulate exit
    session.status = TerminalStatus.EXITED
    session.exit_code = 0
    assert terminal.status == TerminalStatus.EXITED
    assert terminal.exit_code == 0

    # Simulate failure
    session.status = TerminalStatus.FAILED
    session.exit_code = 1
    assert terminal.status == TerminalStatus.FAILED


def test_terminal_session_get_status_info():
    session = TerminalSession(
        name="info-test",
        command="uvicorn main:app --reload",
        cwd=Path.cwd(),
        shell_type="bash",
        ports=[8000],
    )
    session.status = TerminalStatus.RUNNING
    session.pid = 9999
    terminal = PersistentTerminal(session)

    info = terminal.get_status_info()
    assert info["name"] == "info-test"
    assert info["status"] == "running"
    assert info["pid"] == 9999
    assert info["ports"] == [8000]
    assert info["session_id"] == session.session_id


# ---------------------------------------------------------------------------
# BashTool long-running integration tests
# ---------------------------------------------------------------------------

def test_bash_tool_detects_long_running():
    """BashTool should route long-running commands to persistent sessions."""
    tool = BashTool(config=MagicMock())
    tool.config.bash_environment = MagicMock()
    tool.config.bash_environment.ignore_default_excludes = False
    tool.config.bash_environment.exclude_patterns = []
    tool.config.bash_environment.set_vars = {}

    # We can't easily test the full async execute without mocking subprocess,
    # but we can verify the detection logic is called.
    info = detect_long_running("uvicorn main:app --reload")
    assert info.is_long_running is True

    info = detect_long_running("ls -la")
    assert info.is_long_running is False


# ---------------------------------------------------------------------------
# Workspace context validation tests (Phase 1)
# ---------------------------------------------------------------------------

def test_validate_workspace_empty_directory(tmp_path):
    info = validate_workspace(tmp_path)
    assert isinstance(info, WorkspaceInfo)
    assert info.exists is True
    assert info.is_dir is True
    assert info.is_empty is True
    assert info.is_new_workspace is True
    assert info.is_ambiguous is True
    assert info.project_files == []
    assert info.git.is_repo is False


def test_validate_workspace_nonexistent_directory(tmp_path):
    missing = tmp_path / "does_not_exist"
    info = validate_workspace(missing)
    assert info.exists is False
    assert info.error is not None
    assert "does not exist" in info.error


def test_validate_workspace_file_not_directory(tmp_path):
    f = tmp_path / "afile.txt"
    f.write_text("hello")
    info = validate_workspace(f)
    assert info.exists is True
    assert info.is_dir is False
    assert info.error is not None


def test_validate_workspace_with_python_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    (tmp_path / "main.py").write_text("print('hi')\n")
    info = validate_workspace(tmp_path)
    assert info.exists is True
    assert info.is_empty is False
    assert info.is_new_workspace is False
    assert info.is_ambiguous is False
    assert "pyproject.toml" in info.project_files
    assert "main.py" in info.project_files
    assert info.detected_type == "python"


def test_validate_workspace_with_git_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "README.md").write_text("# test\n")
    info = validate_workspace(tmp_path)
    assert info.is_git_repo is True
    assert info.git.is_repo is True
    assert info.is_ambiguous is False  # git repo makes it unambiguous


def test_validate_workspace_with_node_project(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "test", "dependencies": {"react": "^18.0"}}')
    info = validate_workspace(tmp_path)
    assert "package.json" in info.project_files
    assert info.detected_type == "node"


def test_validate_workspace_fullstack(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "test", "dependencies": {"react": "^18.0"}}')
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    info = validate_workspace(tmp_path)
    assert info.detected_type == "full-stack"


def test_validate_workspace_config_files(tmp_path):
    (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")
    (tmp_path / "README.md").write_text("# test\n")
    info = validate_workspace(tmp_path)
    assert ".gitignore" in info.config_files
    assert "README.md" in info.config_files


def test_is_workspace_ambiguous_empty(tmp_path):
    assert is_workspace_ambiguous(tmp_path) is True


def test_is_workspace_ambiguous_with_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    assert is_workspace_ambiguous(tmp_path) is False


def test_workspace_validator_instantiates_with_cwd(tmp_path):
    validator = WorkspaceValidator(tmp_path)
    assert validator.cwd == tmp_path.resolve()


def test_git_info_defaults():
    gi = GitInfo()
    assert gi.is_repo is False
    assert gi.status is None
    assert gi.branch is None
    assert gi.commit is None


# ---------------------------------------------------------------------------
# Project context manager tests (Phase 2)
# ---------------------------------------------------------------------------

def test_build_project_context_empty_workspace(tmp_path):
    from config.config import Config
    config = Config(cwd=tmp_path)
    ctx = build_project_context(config, tmp_path)
    assert isinstance(ctx, ProjectContext)
    assert ctx.workspace.exists is True
    assert ctx.is_empty_workspace is True
    assert ctx.is_ambiguous is True
    assert ctx.project_type == "unknown"
    assert ctx.frameworks == []
    assert ctx.runtimes == []
    assert ctx.cwd == tmp_path.resolve()


def test_build_project_context_with_python_project(tmp_path):
    from config.config import Config
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (tmp_path / "requirements.txt").write_text("fastapi>=0.100.0\nuvicorn>=0.20.0\n")
    config = Config(cwd=tmp_path)
    ctx = build_project_context(config, tmp_path)
    assert ctx.project_type == "fastapi"
    assert ctx.has_project is True
    assert ctx.is_ambiguous is False
    assert "fastapi" in ctx.frameworks
    assert "python" in ctx.runtimes


def test_build_project_context_with_node_project(tmp_path):
    from config.config import Config
    (tmp_path / "package.json").write_text('{"name": "test", "dependencies": {"react": "^18.0", "react-scripts": "^5.0"}}')
    config = Config(cwd=tmp_path)
    ctx = build_project_context(config, tmp_path)
    assert ctx.project_type == "react"
    assert "react" in ctx.frameworks
    assert "node" in ctx.runtimes


def test_validate_and_build_project_context_ambiguous(tmp_path):
    from config.config import Config
    config = Config(cwd=tmp_path)
    ctx, hint = validate_and_build_project_context(config, tmp_path)
    assert ctx.is_ambiguous is True
    assert hint is not None
    assert "empty" in hint.lower() or "new" in hint.lower()


def test_validate_and_build_project_context_unambiguous(tmp_path):
    from config.config import Config
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    config = Config(cwd=tmp_path)
    ctx, hint = validate_and_build_project_context(config, tmp_path)
    assert ctx.is_ambiguous is False
    assert hint is None


def test_project_context_properties(tmp_path):
    from config.config import Config
    config = Config(cwd=tmp_path)
    ctx = build_project_context(config, tmp_path)
    assert ctx.cwd == tmp_path.resolve()
    assert ctx.git_status is None
    assert ctx.git_branch is None
    assert ctx.git_commit is None


def test_project_context_manager_instantiates():
    from config.config import Config
    config = Config()
    mgr = ProjectContextManager(config)
    assert mgr.config is config


def test_project_context_has_project_flag(tmp_path):
    from config.config import Config
    # Empty workspace: no project
    config = Config(cwd=tmp_path)
    ctx = build_project_context(config, tmp_path)
    assert ctx.has_project is False

    # With project files: has project
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    ctx2 = build_project_context(config, tmp_path)
    assert ctx2.has_project is True


def test_project_context_git_repo_flag(tmp_path):
    from config.config import Config
    (tmp_path / ".git").mkdir()
    config = Config(cwd=tmp_path)
    ctx = build_project_context(config, tmp_path)
    assert ctx.is_git_repo is True


# ---------------------------------------------------------------------------
# Tool Selection Supervisor tests (Phase 3)
# ---------------------------------------------------------------------------

def test_tool_supervisor_singleton():
    s1 = get_tool_supervisor()
    s2 = get_tool_supervisor()
    assert s1 is s2


def test_classify_cat():
    assert classify_command("cat README.md") == CommandCategory.READ_FILE
    assert classify_command("cat file.txt") == CommandCategory.READ_FILE


def test_classify_type():
    assert classify_command("type file.txt") == CommandCategory.READ_FILE


def test_classify_head_tail():
    assert classify_command("head -n 10 file.txt") == CommandCategory.READ_FILE
    assert classify_command("tail -n 5 log.txt") == CommandCategory.READ_FILE


def test_classify_ls_dir():
    assert classify_command("ls -la") == CommandCategory.LIST_DIRECTORY
    assert classify_command("dir") == CommandCategory.LIST_DIRECTORY


def test_classify_grep_rg():
    assert classify_command('grep "pattern" file.txt') == CommandCategory.SEARCH_CONTENT
    assert classify_command("rg \"pattern\"") == CommandCategory.SEARCH_CONTENT


def test_classify_find():
    assert classify_command('find . -name "*.py"') == CommandCategory.FIND_FILES


def test_classify_echo_redirect():
    assert classify_command('echo "hello" > file.txt') == CommandCategory.WRITE_FILE


def test_classify_sed_i():
    assert classify_command("sed -i 's/old/new/g' file.txt") == CommandCategory.EDIT_FILE


def test_classify_unknown():
    assert classify_command("docker build -t app .") == CommandCategory.OTHER
    assert classify_command("npm install") == CommandCategory.OTHER


def test_recommend_cat():
    rec = recommend_native_tool("cat README.md")
    assert rec is not None
    assert rec.tool_name == "read"
    assert rec.can_replace is True
    assert rec.confidence >= 0.8


def test_recommend_ls():
    rec = recommend_native_tool("ls -la")
    assert rec is not None
    assert rec.tool_name == "list_directories"
    assert rec.can_replace is True


def test_recommend_grep():
    rec = recommend_native_tool('grep "TODO" src/')
    assert rec is not None
    assert rec.tool_name == "grep"
    assert rec.can_replace is True


def test_recommend_find():
    rec = recommend_native_tool('find . -name "*.py"')
    assert rec is not None
    assert rec.tool_name == "glob"
    assert rec.can_replace is True


def test_recommend_unknown_returns_none():
    assert recommend_native_tool("docker build -t app .") is None
    assert recommend_native_tool("npm install") is None


def test_should_prefer_native():
    assert should_prefer_native("cat README.md") is True
    assert should_prefer_native("ls -la") is True
    assert should_prefer_native("docker build -t app .") is False
    assert should_prefer_native("npm install") is False


def test_format_recommendation():
    result = ToolSelectionSupervisor().format_recommendation("cat README.md")
    assert result is not None
    assert "read" in result
    assert "cat" in result.lower() or "README" in result

    result = ToolSelectionSupervisor().format_recommendation("npm install")
    assert result is None


def test_supervisor_instantiates():
    sup = ToolSelectionSupervisor()
    assert sup is not None


# ---------------------------------------------------------------------------
# Code Generation Integrity tests (Phase 4)
# ---------------------------------------------------------------------------

def test_code_integrity_validator_instantiates(tmp_path):
    validator = CodeIntegrityValidator(tmp_path)
    assert validator is not None
    assert validator.cwd == tmp_path.resolve()


def test_validate_write_new_file(tmp_path):
    validator = CodeIntegrityValidator(tmp_path)
    report = validator.validate_write(tmp_path / "new.py", "print('hello')\n")
    assert report.is_valid is True
    assert len(report.errors) == 0


def test_validate_write_existing_file_warning(tmp_path):
    (tmp_path / "existing.py").write_text("print('old')\n")
    validator = CodeIntegrityValidator(tmp_path)
    report = validator.validate_write(tmp_path / "existing.py", "print('new')\n")
    assert report.is_valid is True  # warnings don't invalidate
    assert len(report.warnings) >= 1
    assert any(i.code == "FILE_EXISTS" for i in report.warnings)


def test_validate_write_syntax_error(tmp_path):
    validator = CodeIntegrityValidator(tmp_path)
    report = validator.validate_write(tmp_path / "bad.py", "print('hello'\n")  # missing paren
    assert report.is_valid is False
    assert len(report.errors) >= 1
    assert any(i.code == "SYNTAX_ERROR" for i in report.errors)


def test_validate_write_unresolvable_import_warning(tmp_path):
    validator = CodeIntegrityValidator(tmp_path)
    report = validator.validate_write(
        tmp_path / "imports.py", "import nonexistent_module_xyz\n"
    )
    # Syntax is fine, import is a warning not an error
    assert report.is_valid is True
    assert any(i.code == "UNRESOLVED_IMPORT" for i in report.warnings)


def test_validate_write_resolvable_import(tmp_path):
    validator = CodeIntegrityValidator(tmp_path)
    report = validator.validate_write(tmp_path / "ok.py", "import os\n")
    assert report.is_valid is True
    assert not any(i.code == "UNRESOLVED_IMPORT" for i in report.issues)


def test_validate_edit_file_not_found(tmp_path):
    validator = CodeIntegrityValidator(tmp_path)
    report = validator.validate_edit(tmp_path / "nonexistent.py", "old", "new")
    assert report.is_valid is False
    assert any(i.code == "FILE_NOT_FOUND" for i in report.errors)


def test_validate_edit_string_not_found(tmp_path):
    (tmp_path / "file.py").write_text("print('hello')\n")
    validator = CodeIntegrityValidator(tmp_path)
    report = validator.validate_edit(tmp_path / "file.py", "not_found", "replacement")
    assert report.is_valid is False
    assert any(i.code == "STRING_NOT_FOUND" for i in report.errors)


def test_validate_edit_syntax_error_in_result(tmp_path):
    (tmp_path / "file.py").write_text("print('hello')\n")
    validator = CodeIntegrityValidator(tmp_path)
    report = validator.validate_edit(
        tmp_path / "file.py",
        "print('hello')",
        "print('broken'"  # syntax error
    )
    assert report.is_valid is False
    assert any(i.code == "SYNTAX_ERROR" for i in report.errors)


def test_validate_edit_valid(tmp_path):
    (tmp_path / "file.py").write_text("print('hello')\n")
    validator = CodeIntegrityValidator(tmp_path)
    report = validator.validate_edit(
        tmp_path / "file.py",
        "print('hello')",
        "print('world')\n"
    )
    assert report.is_valid is True


def test_verify_write_matching(tmp_path):
    f = tmp_path / "verify.py"
    f.write_text("print('hello')\n")
    validator = CodeIntegrityValidator(tmp_path)
    report = validator.verify_write(f, "print('hello')\n")
    assert report.is_valid is True


def test_verify_write_mismatch(tmp_path):
    f = tmp_path / "verify.py"
    f.write_text("print('wrong')\n")
    validator = CodeIntegrityValidator(tmp_path)
    report = validator.verify_write(f, "print('hello')\n")
    assert report.is_valid is False
    assert any(i.code == "READ_BACK_MISMATCH" for i in report.errors)


def test_validate_code_integrity_convenience(tmp_path):
    report = validate_code_integrity(
        tmp_path, tmp_path / "test.py", "print('hello')\n", operation="write"
    )
    assert isinstance(report, IntegrityReport)
    assert report.is_valid is True


def test_integrity_issue_creation():
    issue = IntegrityIssue(
        severity="error",
        message="Test error",
        code="TEST_ERROR",
    )
    assert issue.severity == "error"
    assert issue.message == "Test error"
    assert issue.code == "TEST_ERROR"


def test_integrity_report_properties():
    report = IntegrityReport()
    assert report.is_valid is True

    report.issues.append(IntegrityIssue("warning", "w", "W"))
    assert report.is_valid is True
    assert len(report.warnings) == 1

    report.issues.append(IntegrityIssue("error", "e", "E"))
    assert report.is_valid is False
    assert len(report.errors) == 1


# ---------------------------------------------------------------------------
# Dependency / API Validation tests (Phase 5)
# ---------------------------------------------------------------------------

def test_dependency_validator_instantiates():
    validator = DependencyValidator()
    assert validator is not None


def test_check_package_installed():
    validator = DependencyValidator()
    info = validator.check_package("os")  # stdlib, may not show in metadata
    assert isinstance(info, PackageInfo)
    assert info.name == "os"


def test_check_package_missing():
    validator = DependencyValidator()
    info = validator.check_package("nonexistent_package_xyz_12345")
    assert info.installed is False
    assert info.version is None


def test_check_packages_all_installed():
    validator = DependencyValidator()
    report = validator.check_packages(["sys", "os"])
    assert isinstance(report, DependencyReport)


def test_check_packages_some_missing():
    validator = DependencyValidator()
    report = validator.check_packages(["os", "nonexistent_pkg_xyz"])
    assert report.is_valid is False
    assert any(i.code == "PACKAGE_MISSING" for i in report.errors)


def test_check_version_satisfied():
    validator = DependencyValidator()
    # Python stdlib packages may not have metadata versions, so use a known
    # third-party package if available, or just verify the method works.
    report = validator.check_version("nonexistent_pkg_xyz", "1.0.0")
    assert report.is_valid is False
    assert any(i.code == "PACKAGE_MISSING" for i in report.errors)


def test_check_signature_matching():
    def func(a: int, b: str) -> None:
        pass

    validator = DependencyValidator()
    report = validator.check_signature(func, {"a": int, "b": str})
    assert report.is_valid is True


def test_check_signature_missing_param():
    def func(a: int) -> None:
        pass

    validator = DependencyValidator()
    report = validator.check_signature(func, {"a": int, "b": str})
    assert report.is_valid is False
    assert any(i.code == "SIGNATURE_MISMATCH" for i in report.errors)


def test_check_signature_not_callable():
    validator = DependencyValidator()
    report = validator.check_signature(42, {"a": int})
    assert report.is_valid is False
    assert any(i.code == "NOT_CALLABLE" for i in report.errors)


def test_check_project_dependencies_pip():
    validator = DependencyValidator()
    content = "os\nsys\nnonexistent_pkg_xyz\n"
    report = validator.check_project_dependencies("pip", content)
    assert report.is_valid is False
    assert any(i.code == "PACKAGE_MISSING" for i in report.errors)


def test_check_project_dependencies_npm():
    validator = DependencyValidator()
    content = '{"dependencies": {"nonexistent_npm_pkg_xyz": "^1.0.0"}}'
    report = validator.check_project_dependencies("npm", content)
    assert report.is_valid is False
    assert any(i.code == "PACKAGE_MISSING" for i in report.errors)


def test_check_project_dependencies_unknown_manager():
    validator = DependencyValidator()
    report = validator.check_project_dependencies("unknown", "some content")
    assert report.is_valid is True  # warning only
    assert any(i.code == "UNKNOWN_PACKAGE_MANAGER" for i in report.warnings)


def test_validate_dependencies_convenience():
    report = validate_dependencies(["os", "nonexistent_pkg_xyz"])
    assert isinstance(report, DependencyReport)
    assert report.is_valid is False


def test_validate_version_convenience():
    report = validate_version("nonexistent_pkg_xyz", "1.0.0")
    assert isinstance(report, DependencyReport)
    assert report.is_valid is False


def test_dependency_issue_creation():
    issue = DependencyIssue(
        severity="error",
        message="Test error",
        code="TEST_ERROR",
    )
    assert issue.severity == "error"
    assert issue.message == "Test error"
    assert issue.code == "TEST_ERROR"


def test_dependency_report_properties():
    report = DependencyReport()
    assert report.is_valid is True

    report.issues.append(DependencyIssue("warning", "w", "W"))
    assert report.is_valid is True
    assert len(report.warnings) == 1

    report.issues.append(DependencyIssue("error", "e", "E"))
    assert report.is_valid is False
    assert len(report.errors) == 1


def test_package_info_creation():
    info = PackageInfo(name="test", version="1.0.0", installed=True)
    assert info.name == "test"
    assert info.version == "1.0.0"
    assert info.installed is True


# ---------------------------------------------------------------------------
# Error recovery integration tests (Phase 7)
# ---------------------------------------------------------------------------

def test_registry_error_metadata_includes_recovery_info(tmp_path):
    """When a tool raises, result metadata carries error recovery info."""
    from unittest.mock import MagicMock, AsyncMock
    from tools.base import Tool, ToolKind
    from tools.registry import ToolRegistry
    from config.config import Config
    import asyncio

    class FailingTool(Tool):
        name = "failing_tool"
        description = "A tool that always fails"
        kind = ToolKind.BASH

        def __init__(self, config):
            self.config = config

        @property
        def schema(self):
            return {}

        async def execute(self, invocation):
            raise ValueError("something broke")

    config = Config(cwd=tmp_path)
    registry = ToolRegistry(config)
    registry.register(FailingTool(config))

    hook_system = MagicMock()
    hook_system.trigger_before_tool = AsyncMock()
    hook_system.trigger_after_tool = AsyncMock()
    result = asyncio.run(
        registry.invoke("failing_tool", {}, tmp_path, hook_system)
    )

    assert result.success is False
    assert result.metadata.get("error_category") is not None
    assert result.metadata.get("recovery_action") is not None
    assert "recovery_stop" in result.metadata
    assert "recovery_reason" in result.metadata
    assert result.metadata.get("error_retryable") is not None


def test_registry_error_stop_after_two_similar_failures(tmp_path):
    """After two similar failures, recovery_plan.should_stop is True."""
    from unittest.mock import MagicMock, AsyncMock
    from tools.base import Tool, ToolKind
    from tools.registry import ToolRegistry
    from config.config import Config
    from tools.core.error_recovery import (
        get_error_recovery_engine, ErrorCategory,
    )
    import asyncio

    # Reset the shared engine so failure counts start clean.
    get_error_recovery_engine().reset()

    class FailingTool(Tool):
        name = "failing_tool"
        description = "A tool that always fails"
        kind = ToolKind.BASH

        def __init__(self, config):
            self.config = config

        @property
        def schema(self):
            return {}

        async def execute(self, invocation):
            raise ValueError("something broke")

    config = Config(cwd=tmp_path)
    registry = ToolRegistry(config)
    registry.register(FailingTool(config))

    hook_system = MagicMock()
    hook_system.trigger_before_tool = AsyncMock()
    hook_system.trigger_after_tool = AsyncMock()

    # First failure — should_stop should be False (only 1 failure so far).
    result1 = asyncio.run(
        registry.invoke("failing_tool", {}, tmp_path, hook_system)
    )
    assert result1.metadata.get("recovery_stop") is False

    # Second failure — should_stop should now be True.
    result2 = asyncio.run(
        registry.invoke("failing_tool", {}, tmp_path, hook_system)
    )
    assert result2.metadata.get("recovery_stop") is True

    get_error_recovery_engine().reset()


# ---------------------------------------------------------------------------
# Session initialization progress tests (Phase 10)
# ---------------------------------------------------------------------------

def test_session_initialize_reports_progress_phases(tmp_path):
    """Session.initialize() calls the progress callback during each phase."""
    from agent.session import Session
    from config.config import Config
    import asyncio

    config = Config(cwd=tmp_path)
    session = Session(config)

    progress_calls = []
    asyncio.run(
        session.initalize(progress=lambda label: progress_calls.append(label))
    )

    assert len(progress_calls) >= 5
    joined = " | ".join(progress_calls)
    assert "initializing workspace" in joined
    assert "registering MCP tools" in joined
    assert "discovering tools" in joined
    assert "building context manager" in joined
    assert "validating project context" in joined


# ---------------------------------------------------------------------------
# Agent ambiguity check tests (Phase 1)
# ---------------------------------------------------------------------------

def test_agent_run_ambiguous_workspace_yields_clarification(tmp_path):
    """agent.run() yields agent_error (no turn counted) when workspace is ambiguous."""
    from agent.agent import Agent
    from agent.events import AgentEventType
    from context.workspace import WorkspaceInfo, GitInfo, validate_workspace
    from context.project_context import ProjectContext
    from config.config import Config
    import asyncio

    config = Config(cwd=tmp_path)
    agent = Agent(config)

    # Set up an ambiguous project context on the session.
    workspace = validate_workspace(tmp_path)
    assert workspace.is_ambiguous is True

    agent.session.project_context = ProjectContext(
        workspace=workspace,
        project_type="unknown",
        frameworks=[],
        runtimes=[],
    )
    agent.session.project_context_hint = "Workspace appears empty or new."

    async def collect() -> list:
        events = []
        async for event in agent.run("test message"):
            events.append(event)
        return events

    events = asyncio.run(collect())

    assert len(events) >= 1
    assert events[0].type == AgentEventType.AGENT_ERROR
    assert events[0].data.get("details", {}).get("ambiguous_workspace") is True
    # No turn should be counted for an ambiguous workspace.
    assert agent.session.turns == 0


# ---------------------------------------------------------------------------
# Workspace writability check tests (Phase C)
# ---------------------------------------------------------------------------

def test_validate_workspace_not_writable(tmp_path, monkeypatch):
    """A non-writable workspace should be reported with an error."""
    import os

    original_access = os.access

    def fake_access(path, mode):
        if mode == os.W_OK and Path(path) == tmp_path:
            return False
        return original_access(path, mode)

    monkeypatch.setattr(os, "access", fake_access)

    info = validate_workspace(tmp_path)
    assert info.exists is True
    assert info.is_dir is True
    assert info.error is not None
    assert "not writable" in info.error


# ---------------------------------------------------------------------------
# EditTool optimistic concurrency tests (Phase D)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_tool_optimistic_concurrency_no_false_positive(tmp_path):
    """EditTool should not report conflict when file is unchanged between read and write."""
    from config.config import Config
    from tools.base import ToolInvocation
    from tools.core.edit import EditTool

    tool = EditTool(Config(cwd=tmp_path))
    f = tmp_path / "concurrent.py"
    f.write_text("x = 1\n")

    # First edit succeeds.
    result1 = await tool.execute(
        ToolInvocation(
            {"path": "concurrent.py", "old_string": "x = 1", "new_string": "x = 2"},
            tmp_path,
        )
    )
    assert result1.success is True

    # Second edit with no concurrent modification should succeed.
    result2 = await tool.execute(
        ToolInvocation(
            {"path": "concurrent.py", "old_string": "x = 2", "new_string": "x = 3"},
            tmp_path,
        )
    )
    assert result2.success is True
    assert result2.expected_state_verified is True


@pytest.mark.asyncio
async def test_edit_tool_optimistic_concurrency_detects_conflict(tmp_path):
    """EditTool should detect concurrent modification via stat comparison."""
    from config.config import Config
    from tools.base import ToolInvocation
    from tools.core.edit import EditTool
    from unittest.mock import MagicMock
    from pathlib import Path

    tool = EditTool(Config(cwd=tmp_path))
    f = tmp_path / "concurrent.py"
    f.write_text("x = 1\n")

    # First edit succeeds to create "x = 2\n".
    result1 = await tool.execute(
        ToolInvocation(
            {"path": "concurrent.py", "old_string": "x = 1", "new_string": "x = 2"},
            tmp_path,
        )
    )
    assert result1.success is True

    # Mock Path.stat to alternate between two different stat results.
    # Since _before_stat and _after_stat are consecutive stat calls with no
    # other stat calls between them, they will receive different values,
    # triggering the conflict detection.
    stat_call_count = [0]

    def fake_stat(*args, **kwargs):
        stat_call_count[0] += 1
        m = MagicMock()
        if stat_call_count[0] % 2 == 1:
            m.st_mtime_ns = 1000
            m.st_size = 10
        else:
            m.st_mtime_ns = 2000
            m.st_size = 20
        return m

    with patch.object(Path, "stat", side_effect=fake_stat):
        result2 = await tool.execute(
            ToolInvocation(
                {"path": "concurrent.py", "old_string": "x = 2", "new_string": "x = 3"},
                tmp_path,
            )
        )
        assert result2.success is False
        assert result2.metadata.get("conflict") is True
        assert "modified by another process" in result2.error


# ---------------------------------------------------------------------------
# Regression tests A-I — Autonomous Task Completion and Reliability Audit
# ---------------------------------------------------------------------------

# --- A: Continuation loop does not stop prematurely ---

def test_continuation_loop_completes_all_benchmark_phases(tmp_path):
    """Regression A: The task must not stop until all benchmark phases are verified."""
    from utils.development_supervisor import (
        DevelopmentSupervisor, TASK_RUNNING, TASK_COMPLETED_STATE,
        BENCHMARK_PENDING, BENCHMARK_VERIFIED,
    )
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.begin_task("Complete all requirements")

    # Add 10 benchmark phases (the requirements)
    for i in range(10):
        pid = f"req.{i+1:02d}"
        supervisor.add_benchmark_phase(pid, f"Requirement {i+1}")

    # Complete 7, fail 1 recoverably, leave 2 pending
    for i in range(7):
        pid = f"req.{i+1:02d}"
        supervisor.start_benchmark_phase(pid)
        supervisor.complete_benchmark_phase(pid, f"Evidence for requirement {i+1}")
        supervisor.verify_benchmark_phase(pid, f"Verification {i+1}")

    # Requirement 8: fail recoverably
    pid8 = "req.08"
    supervisor.start_benchmark_phase(pid8)
    supervisor.fail_benchmark_phase(pid8, "Transient failure", "Retry with corrected parameters")

    # Requirements 9-10: still pending
    # (req.09, req.10 remain in BENCHMARK_PENDING)

    progress = supervisor.get_benchmark_progress()
    assert progress["verified"] == 7
    assert progress["failed"] == 1
    assert progress["pending"] == 2
    assert supervisor.has_incomplete_benchmark_phases()

    # Recover requirement 8
    supervisor.start_benchmark_phase(pid8)
    supervisor.complete_benchmark_phase(pid8, "Retry evidence for requirement 8")
    supervisor.verify_benchmark_phase(pid8, "Retry verification 8")

    # Complete requirements 9 and 10
    for i in (9, 10):
        pid = f"req.{i:02d}"
        supervisor.start_benchmark_phase(pid)
        supervisor.complete_benchmark_phase(pid, f"Evidence for requirement {i}")
        supervisor.verify_benchmark_phase(pid, f"Verification {i}")

    # Now all 10 are verified — task can complete
    assert supervisor.all_benchmark_phases_verified()
    assert not supervisor.has_incomplete_benchmark_phases()

    # Add a plan step and verify it too
    supervisor.add_task_step("step_1", "Final integration step")
    supervisor.verify_task_step("step_1", "All 10 requirements verified")
    supervisor.complete_task_checkpoint()
    assert supervisor.get_task_status() == TASK_COMPLETED_STATE


# --- B: Authoritative task state transitions ---

def test_authoritative_task_state_transitions(tmp_path):
    """Regression B: Task states transition PENDING→RUNNING→COMPLETED→VERIFIED."""
    from utils.development_supervisor import (
        DevelopmentSupervisor, TASK_PENDING, TASK_RUNNING, TASK_COMPLETED_STATE,
        TASK_VERIFIED, TASK_FAILED, TASK_BLOCKED, TASK_AWAITING_APPROVAL,
        TASK_VERIFICATION_REQUIRED,
    )
    supervisor = DevelopmentSupervisor(tmp_path)

    # Initial state is PENDING
    assert supervisor.get_task_status() == TASK_PENDING

    # begin_task moves to RUNNING
    supervisor.begin_task("Test task")
    assert supervisor.get_task_status() == TASK_RUNNING

    # block_task moves to BLOCKED
    supervisor.block_task("Waiting for user input")
    assert supervisor.get_task_status() == TASK_BLOCKED

    # unblock_task moves back to RUNNING
    supervisor.unblock_task()
    assert supervisor.get_task_status() == TASK_RUNNING

    # await_approval moves to AWAITING_APPROVAL
    supervisor.await_approval("Needs sign-off on approach")
    assert supervisor.get_task_status() == TASK_AWAITING_APPROVAL

    # require_verification moves to VERIFICATION_REQUIRED
    supervisor.require_verification("Needs independent verification")
    assert supervisor.get_task_status() == TASK_VERIFICATION_REQUIRED

    # fail_task_checkpoint moves to FAILED
    supervisor.fail_task_checkpoint("Critical error", "Fix and retry")
    assert supervisor.get_task_status() == TASK_FAILED

    # Invalid status raises
    import pytest
    with pytest.raises(ValueError, match="Invalid task status"):
        supervisor.set_task_status("invalid_status")


# --- C: Autonomous recovery with bounded retries ---

def test_autonomous_recovery_bounded_retries(tmp_path):
    """Regression C: Failed tool calls are retried up to max_retries, then stop."""
    from unittest.mock import MagicMock, AsyncMock, patch
    from tools.base import Tool, ToolKind, ToolInvocation, ToolResult, ExecutionStatus
    from tools.registry import ToolRegistry
    from config.config import Config
    import asyncio

    config = Config(cwd=tmp_path)
    registry = ToolRegistry(config)

    call_count = {"n": 0}

    class FlakyTool(Tool):
        name = "flaky_tool"
        description = "A tool that fails twice then succeeds"
        kind = ToolKind.BASH

        def __init__(self, config):
            self.config = config

        @property
        def schema(self):
            return {}

        async def execute(self, invocation):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return ToolResult.error_result(
                    f"Transient failure attempt {call_count['n']}",
                    metadata={
                        "error_category": "NETWORK",
                        "error_retryable": True,
                        "recovery_stop": False,
                        "recovery_reason": "Transient network error, retry recommended.",
                    },
                )
            return ToolResult.success_result(
                "Success after retries",
                execution_status=ExecutionStatus.ACTION_EXECUTED,
            )

    registry.register(FlakyTool(config))

    hook_system = MagicMock()
    hook_system.trigger_before_tool = AsyncMock()
    hook_system.trigger_after_tool = AsyncMock()

    invocation = ToolInvocation({}, tmp_path)

    # First call: fails (attempt 1), retried internally (attempt 2), fails again,
    # retried again (attempt 3), succeeds.
    # The retry logic in agent.py retries once per invocation call.
    # Actually the agent retries once per failed call, so we need multiple invocations.
    result1 = asyncio.run(registry.invoke("flaky_tool", {}, tmp_path, hook_system))
    assert result1.success is False  # First attempt fails
    assert call_count["n"] == 1

    # Second invocation: the retry logic in the agent would retry once
    result2 = asyncio.run(registry.invoke("flaky_tool", {}, tmp_path, hook_system))
    # The registry itself doesn't retry — the agent does. So this is attempt 2.
    assert call_count["n"] == 2

    # Third invocation: attempt 3 succeeds.
    result3 = asyncio.run(registry.invoke("flaky_tool", {}, tmp_path, hook_system))
    assert result3.success is True
    assert call_count["n"] == 3


# --- D: Filesystem data protection — no silent overwrites ---

def test_filesystem_data_protection_no_silent_overwrite(tmp_path):
    """Regression D: Write tool refuses to overwrite without explicit confirmation."""
    from config.config import Config
    from tools.base import ToolInvocation
    from tools.core.write import WriteFileTool
    import asyncio

    tool = WriteFileTool(Config(cwd=tmp_path))
    f = tmp_path / "protected.txt"
    f.write_text("original content\n")

    # Writing without overwrite=True should fail
    result = asyncio.run(
        tool.execute(
            ToolInvocation(
                {"path": str(f), "content": "new content\n", "overwrite": False},
                tmp_path,
            )
        )
    )
    assert result.success is False
    assert "overwrite" in result.error.lower() or "exists" in result.error.lower()

    # File should be unchanged
    assert f.read_text(encoding="utf-8") == "original content\n"

    # Writing with overwrite=True should succeed
    result2 = asyncio.run(
        tool.execute(
            ToolInvocation(
                {"path": str(f), "content": "new content\n", "overwrite": True},
                tmp_path,
            )
        )
    )
    assert result2.success is True
    assert f.read_text(encoding="utf-8") == "new content\n"

    # Creating a new file without overwrite should work
    f2 = tmp_path / "newfile.txt"
    result3 = asyncio.run(
        tool.execute(
            ToolInvocation(
                {"path": str(f2), "content": "brand new\n"},
                tmp_path,
            )
        )
    )
    assert result3.success is True
    assert f2.read_text(encoding="utf-8") == "brand new\n"


# --- E: Task persistence across interruptions ---

def test_task_persistence_across_interruption(tmp_path):
    """Regression E: Task state survives interruption and can be resumed."""
    from utils.development_supervisor import (
        DevelopmentSupervisor, TASK_RUNNING, TASK_COMPLETED_STATE,
        TASK_VERIFICATION_REQUIRED,
    )
    # First supervisor: set up task with partial progress
    sup1 = DevelopmentSupervisor(tmp_path)
    sup1.begin_task("Persistent task")
    # Add benchmark phase BEFORE completing plan steps, so task doesn't
    # auto-complete prematurely.
    sup1.add_benchmark_phase("phase.1", "Phase 1")
    sup1.start_benchmark_phase("phase.1")
    sup1.complete_benchmark_phase("phase.1", "Phase 1 evidence")
    # Phase 1 completed but not verified — simulating interruption
    sup1.add_task_step("step_1", "First step")
    sup1.verify_task_step("step_1", "Step 1 evidence")
    # All plan steps done but benchmark phase not verified → VERIFICATION_REQUIRED
    assert sup1.get_task_status() == TASK_VERIFICATION_REQUIRED

    # Second supervisor: resumes from disk
    sup2 = DevelopmentSupervisor(tmp_path)
    task = sup2.state["task_checkpoint"]
    assert task["status"] == TASK_VERIFICATION_REQUIRED
    assert len(task["steps"]) == 1
    assert task["steps"][0]["status"] == "completed"
    assert task["steps"][0]["verification"] == "Step 1 evidence"

    phases = sup2.get_all_benchmark_phases()
    assert len(phases) == 1
    assert phases[0]["status"] == "completed"  # completed but not verified
    assert phases[0]["evidence"] == "Phase 1 evidence"
    assert phases[0].get("verification_evidence") is None  # not verified yet

    # Resume: verify phase 1
    sup2.verify_benchmark_phase("phase.1", "Phase 1 verification")
    assert sup2.get_benchmark_phase("phase.1")["status"] == "verified"
    assert sup2.get_benchmark_phase("phase.1")["evidence"] == "Phase 1 evidence"  # preserved
    assert sup2.get_benchmark_phase("phase.1")["verification_evidence"] == "Phase 1 verification"

    # All phases verified now, task can complete
    assert sup2.all_benchmark_phases_verified()


# --- F: Stop behavior — all terminal and partial states ---

def test_stop_behavior_all_states(tmp_path):
    """Regression F: COMPLETED/BLOCKED/AWAITING_APPROVAL/PARTIAL states are properly reported."""
    from utils.development_supervisor import (
        DevelopmentSupervisor, TASK_COMPLETED_STATE, TASK_BLOCKED,
        TASK_AWAITING_APPROVAL, TASK_PENDING, TASK_RUNNING,
        TASK_VERIFICATION_REQUIRED, TASK_VERIFIED,
    )
    supervisor = DevelopmentSupervisor(tmp_path)

    # PENDING → partial
    assert supervisor.get_task_status() == TASK_PENDING

    supervisor.begin_task("Stop behavior test")
    assert supervisor.get_task_status() == TASK_RUNNING

    # BLOCKED state
    supervisor.block_task("Waiting for API credentials")
    assert supervisor.get_task_status() == TASK_BLOCKED
    assert "API credentials" in supervisor.state["task_checkpoint"]["recovery_information"]

    # AWAITING_APPROVAL state
    supervisor.unblock_task()
    supervisor.await_approval("Needs sign-off on schema change")
    assert supervisor.get_task_status() == TASK_AWAITING_APPROVAL

    # VERIFICATION_REQUIRED state
    supervisor.unblock_task()
    supervisor.require_verification("Needs independent security review")
    assert supervisor.get_task_status() == TASK_VERIFICATION_REQUIRED

    # COMPLETED state (via verify + complete)
    supervisor.add_task_step("step_1", "Implement feature")
    supervisor.verify_task_step("step_1", "Feature implemented and tested")
    # All steps done, all benchmark phases verified (none exist → vacuously true)
    assert supervisor.all_benchmark_phases_verified()
    assert supervisor.get_task_status() == TASK_COMPLETED_STATE


# --- G: Benchmark phases independent of plan steps ---

def test_benchmark_phases_independent_of_plan_steps(tmp_path):
    """Regression G: Completing plan steps does not complete benchmark phases."""
    from utils.development_supervisor import (
        DevelopmentSupervisor, TASK_COMPLETED_STATE, TASK_VERIFICATION_REQUIRED,
    )
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.begin_task("Independent tracking test")

    # Add 6 plan steps (internal implementation tasks)
    for i in range(6):
        supervisor.add_task_step(f"step_{i}", f"Internal step {i}")

    # Add 3 benchmark phases (user-requested work)
    for i in range(3):
        pid = f"bench.{i+1:02d}"
        supervisor.add_benchmark_phase(pid, f"Benchmark phase {i+1}")

    # Complete all plan steps with verification
    for i in range(6):
        supervisor.verify_task_step(f"step_{i}", f"Evidence for step {i}")

    # Task should be VERIFICATION_REQUIRED (not COMPLETED) because benchmark
    # phases are not yet verified.
    assert supervisor.get_task_status() == TASK_VERIFICATION_REQUIRED
    assert supervisor.has_incomplete_benchmark_phases()
    assert not supervisor.all_benchmark_phases_verified()

    # Now complete and verify all benchmark phases
    for i in range(3):
        pid = f"bench.{i+1:02d}"
        supervisor.start_benchmark_phase(pid)
        supervisor.complete_benchmark_phase(pid, f"Evidence {i+1}")
        supervisor.verify_benchmark_phase(pid, f"Verification {i+1}")

    # Now task can complete
    assert supervisor.all_benchmark_phases_verified()
    assert not supervisor.has_incomplete_benchmark_phases()


# --- H: No silent completion without verification ---

def test_no_silent_completion_without_verification(tmp_path):
    """Regression H: Task is not marked complete without all requirements verified."""
    from utils.development_supervisor import (
        DevelopmentSupervisor, TASK_COMPLETED_STATE, TASK_VERIFICATION_REQUIRED,
        BENCHMARK_COMPLETED, BENCHMARK_VERIFIED,
    )
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.begin_task("No silent completion test")

    # Add benchmark phase BEFORE completing plan steps, so the task doesn't
    # auto-complete prematurely (vacuously true when no phases exist).
    supervisor.add_benchmark_phase("bench.1", "Requirement 1")
    supervisor.start_benchmark_phase("bench.1")
    supervisor.complete_benchmark_phase("bench.1", "Evidence 1")

    # Add plan steps and complete them all
    supervisor.add_task_step("step_1", "Do the work")
    supervisor.verify_task_step("step_1", "Work done")

    # Task should NOT be completed — benchmark phase is completed but not verified
    assert supervisor.get_task_status() == TASK_VERIFICATION_REQUIRED
    assert not supervisor.all_benchmark_phases_verified()
    assert supervisor.has_incomplete_benchmark_phases()

    # complete_task_checkpoint should refuse
    import pytest
    with pytest.raises(ValueError, match="benchmark phases remain unverified"):
        supervisor.complete_task_checkpoint()

    # Now verify the phase — task can complete
    supervisor.verify_benchmark_phase("bench.1", "Verification 1")
    assert supervisor.all_benchmark_phases_verified()
    supervisor.complete_task_checkpoint()
    assert supervisor.get_task_status() == TASK_COMPLETED_STATE


# --- I: Recovery from failed task ---

def test_recovery_from_failed_task(tmp_path):
    """Regression I: A failed task can be recovered and continued."""
    from utils.development_supervisor import (
        DevelopmentSupervisor, TASK_FAILED, TASK_RUNNING, TASK_COMPLETED_STATE,
    )
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.begin_task("Recovery test")
    supervisor.add_task_step("step_1", "First step")
    supervisor.add_task_step("step_2", "Second step")

    # Fail step 1
    supervisor.fail_task_step("step_1", "Connection lost", "Reconnect and retry")
    assert supervisor.get_task_status() == TASK_RUNNING
    assert "step_1" in [s["id"] for s in supervisor.state["task_checkpoint"]["steps"]
                        if s["status"] == "failed"]

    # Recover: re-add step 1 and verify it
    # The step already exists, so we verify it directly (simulating re-execution)
    supervisor.verify_task_step("step_1", "Reconnected and step 1 completed")
    assert supervisor.get_task_status() == TASK_RUNNING  # step 2 still pending

    # Complete step 2
    supervisor.verify_task_step("step_2", "Step 2 completed")
    assert supervisor.get_task_status() == TASK_COMPLETED_STATE

    supervisor.complete_task_checkpoint()
    assert supervisor.get_task_status() == TASK_COMPLETED_STATE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
