"""RUN handler — detect stack, execute command, capture output."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal


def detect_stack(directory: str = ".") -> dict:
    """Detect project stack and suggest run commands."""
    root = Path(directory).resolve()
    stack = {"language": None, "framework": None, "build_tool": None, "test_cmd": None, "run_cmd": None}

    if (root / "package.json").exists():
        stack["language"] = "node"
        try:
            import json
            pkg = json.loads((root / "package.json").read_text())
            scripts = pkg.get("scripts", {})
            if "dev" in scripts:
                stack["run_cmd"] = "npm run dev"
            elif "start" in scripts:
                stack["run_cmd"] = "npm start"
            if "test" in scripts:
                stack["test_cmd"] = "npm test"
            stack["framework"] = _detect_node_framework(pkg)
        except Exception:
            pass

    if (root / "pyproject.toml").exists():
        stack["language"] = "python"
        stack["build_tool"] = "poetry" if "poetry" in (root / "pyproject.toml").read_text() else "pip"
        if not stack["test_cmd"]:
            stack["test_cmd"] = "pytest"

    if (root / "requirements.txt").exists():
        stack["language"] = "python"
        stack["build_tool"] = "pip"
        if not stack["test_cmd"]:
            stack["test_cmd"] = "pytest"

    if (root / "Cargo.toml").exists():
        stack["language"] = "rust"
        stack["build_tool"] = "cargo"
        stack["run_cmd"] = "cargo run"
        stack["test_cmd"] = "cargo test"

    if (root / "go.mod").exists():
        stack["language"] = "go"
        stack["build_tool"] = "go"
        stack["run_cmd"] = "go run ."
        stack["test_cmd"] = "go test ./..."

    if not stack["language"]:
        stack["language"] = "unknown"

    return stack


def _detect_node_framework(pkg: dict) -> str | None:
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    if "next" in deps:
        return "next.js"
    if "vite" in deps:
        return "vite"
    if "react-scripts" in deps:
        return "react"
    return None


def execute(command: str, timeout: int = 30, workdir: str | None = None) -> dict:
    """Execute a command and capture output."""
    cwd = workdir or os.getcwd()
    try:
        result = subprocess.run(
            command if sys.platform == "win32" else command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timeout": False,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": "", "timeout": True, "error": f"timed out after {timeout}s"}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": "", "timeout": False, "error": str(e)}


def _check_port_open(port: int, host: str = "localhost", timeout: float = 2.0) -> bool:
    """Check if a TCP port is open."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _extract_port_from_output(output: str) -> int | None:
    """Extract port number from common server output patterns."""
    patterns = [
        r"listening on.*?(\d+)",
        r"port\s+(\d+)",
        r":(\d{2,5})",
        r"http://localhost:(\d+)",
        r"http://127\.0\.0\.1:(\d+)",
        r"Running on.*?(\d+)",
        r"Server.*?port.*?(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
    return None


RunStatus = Literal["success", "failure", "timeout", "error"]


def verify_run(command: str, workdir: str | None = None, timeout: int = 30, check_port: bool = True) -> dict:
    """
    Execute command and return structured verification status.

    Returns:
        {
            "status": "success" | "failure" | "timeout" | "error",
            "command": "...",
            "exit_code": 0,
            "server_running": true,
            "port": 3000,
            "stdout": "...",
            "stderr": "...",
            "error": None
        }
    """
    result = execute(command, timeout=timeout, workdir=workdir)

    # Determine status
    if result.get("timeout"):
        status: RunStatus = "timeout"
    elif result.get("error"):
        status = "error"
    elif result["exit_code"] != 0:
        status = "failure"
    else:
        status = "success"

    # Check for server running
    server_running = False
    port = None

    if status == "success" and check_port:
        # Extract port from output
        combined_output = result.get("stdout", "") + result.get("stderr", "")
        port = _extract_port_from_output(combined_output)

        if port:
            # Give server a moment to start
            time.sleep(0.5)
            server_running = _check_port_open(port)
        else:
            # If no port detected but exit code 0, might be a foreground process
            # Check if it's still running by looking for indicators
            server_running = "listening" in combined_output.lower() or "ready" in combined_output.lower()

    return {
        "status": status,
        "command": command,
        "exit_code": result.get("exit_code", -1),
        "server_running": server_running,
        "port": port,
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "error": result.get("error"),
    }


def run_and_verify(workdir: str = ".", timeout: int = 30) -> dict:
    """
    Detect stack, run appropriate command, and verify.

    This is the main entry point for 'run this project' tasks.
    """
    stack = detect_stack(workdir)

    if not stack.get("run_cmd"):
        return {
            "status": "failure",
            "command": "none",
            "exit_code": -1,
            "server_running": False,
            "port": None,
            "stdout": "",
            "stderr": "",
            "error": f"No run command detected for language: {stack.get('language')}",
        }

    return verify_run(stack["run_cmd"], workdir=workdir, timeout=timeout, check_port=True)
