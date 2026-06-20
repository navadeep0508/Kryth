"""Terminal Manager — PTY process lifecycle for xterm.js integration.

The terminal is backend-owned. The frontend xterm.js is a dumb display
that sends keystrokes and receives bytes. All shell logic (process start,
kill, resize, history) lives here.

On Windows: uses pywinpty if available, else falls back to subprocess pipe.
On Unix:    uses ptyprocess.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import threading
from typing import Callable

ByteCallback = Callable[[bytes], None]


class TerminalSession:
    """One PTY process bound to one WebSocket client."""

    def __init__(self, on_output: ByteCallback, cols: int = 220, rows: int = 50):
        self._on_output = on_output
        self._cols = cols
        self._rows = rows
        self._proc = None
        self._thread: threading.Thread | None = None
        self._alive = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._alive:
            return
        self._alive = True

        if sys.platform == "win32":
            self._start_windows()
        else:
            self._start_unix()

    # ------------------------------------------------------------------
    def _start_windows(self) -> None:
        try:
            import winpty
            self._pty = winpty.PTY(self._cols, self._rows)
            shell = os.environ.get("ComSpec", "cmd.exe")
            pwsh  = shutil.which("pwsh") or shutil.which("powershell")
            if pwsh:
                shell = pwsh
            self._pty.spawn(shell)
            self._thread = threading.Thread(target=self._read_winpty, daemon=True)
            self._thread.start()
            return
        except ImportError:
            pass

        # Fallback: subprocess + pipe (no pty, limited)
        import subprocess
        shell = shutil.which("pwsh") or shutil.which("powershell") or "cmd.exe"
        self._subproc = subprocess.Popen(
            [shell],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._thread = threading.Thread(target=self._read_subprocess, daemon=True)
        self._thread.start()

    def _read_winpty(self) -> None:
        while self._alive:
            try:
                chunk = self._pty.read(4096, blocking=True)
                if chunk:
                    self._on_output(chunk.encode("utf-8", "replace")
                                    if isinstance(chunk, str) else chunk)
            except Exception:
                break
        self._alive = False

    def _read_subprocess(self) -> None:
        while self._alive:
            try:
                chunk = self._subproc.stdout.read(4096)
                if not chunk:
                    break
                self._on_output(chunk)
            except Exception:
                break
        self._alive = False

    # ------------------------------------------------------------------
    def _start_unix(self) -> None:
        try:
            import ptyprocess
            shell = os.environ.get("SHELL", "/bin/bash")
            self._pty = ptyprocess.PtyProcess.spawn([shell])
            self._thread = threading.Thread(target=self._read_ptyprocess, daemon=True)
            self._thread.start()
        except ImportError:
            # Fallback
            import subprocess
            self._subproc = subprocess.Popen(
                [os.environ.get("SHELL", "/bin/sh")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self._thread = threading.Thread(target=self._read_subprocess, daemon=True)
            self._thread.start()

    def _read_ptyprocess(self) -> None:
        while self._alive:
            try:
                chunk = self._pty.read(4096)
                if chunk:
                    self._on_output(chunk if isinstance(chunk, bytes)
                                    else chunk.encode("utf-8", "replace"))
            except Exception:
                break
        self._alive = False

    # ------------------------------------------------------------------
    def write(self, data: bytes) -> None:
        if not self._alive:
            return
        try:
            if hasattr(self, "_pty"):
                if sys.platform == "win32":
                    self._pty.write(data.decode("utf-8", "replace"))
                else:
                    self._pty.write(data)
            elif hasattr(self, "_subproc") and self._subproc.stdin:
                self._subproc.stdin.write(data)
                self._subproc.stdin.flush()
        except Exception:
            pass

    def resize(self, cols: int, rows: int) -> None:
        self._cols, self._rows = cols, rows
        try:
            if hasattr(self, "_pty"):
                if sys.platform == "win32":
                    self._pty.set_size(cols, rows)
                else:
                    self._pty.setwinsize(rows, cols)
        except Exception:
            pass

    def kill(self) -> None:
        self._alive = False
        try:
            if hasattr(self, "_pty"):
                if sys.platform == "win32":
                    self._pty.close()
                else:
                    self._pty.terminate()
            elif hasattr(self, "_subproc"):
                self._subproc.kill()
        except Exception:
            pass
