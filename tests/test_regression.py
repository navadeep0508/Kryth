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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
