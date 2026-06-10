"""Structured logging pipeline.

Logs are a SECOND output stream — separate from the user-facing UI.
Two destinations:

- **Console** — only WARNING+ (or whatever ``KRYTH_LOG_LEVEL`` sets).
  This is what the user sees mixed with the REPL. Default is WARNING
  so library logs don't leak into the calm UI.
- **File** — ``~/.kryth/debug.log`` — everything from DEBUG up,
  rotated at 1 MiB × 3 backups. This is where ``get_logger(...).debug(...)``
  diagnostics land regardless of the console level.

Library logs (httpx, openai, urllib3, httpcore) are forced quiet on the
console but ALL go to the file so we can investigate after the fact.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

from agent.env import getenv, home_dir
from agent.ui.console import console


class _WindowsSafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that silently skips rotation on Windows when
    the log file is locked by another process (WinError 32).

    On Windows, ``os.rename`` fails with PermissionError if the file is
    open elsewhere. Rather than spewing a ``--- Logging error ---``
    traceback to stderr on every request, we catch the error and keep
    writing to the current file. The log grows slightly beyond maxBytes
    until the lock is released, which is acceptable for a debug log.
    """

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except (PermissionError, OSError):
            # Can't rotate right now — keep writing to the current file.
            # Re-open the stream if it was closed by the base class.
            if self.stream is None:
                try:
                    self.stream = self._open()
                except Exception:
                    pass

    def handleError(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        # Suppress the default "--- Logging error ---" stderr dump.
        # Rotation failures are non-fatal; silently swallow them.
        pass


_LEVEL_RAW = getenv("KRYTH_LOG_LEVEL", "WARNING").upper()
_VALID = {"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"}
if _LEVEL_RAW == "WARN":
    _LEVEL_RAW = "WARNING"
if _LEVEL_RAW not in _VALID:
    _LEVEL_RAW = "WARNING"

_CONSOLE_LEVEL = getattr(logging, _LEVEL_RAW)
_FILE_LEVEL = logging.DEBUG


# --- file handler ----------------------------------------------------

_env_dir = getenv("KRYTH_LOG_DIR").strip()
_LOG_DIR = Path(_env_dir) if _env_dir else home_dir()
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_FILE = _LOG_DIR / "debug.log"
    _file_handler: logging.Handler | None = _WindowsSafeRotatingFileHandler(
        _LOG_FILE,
        maxBytes=1_048_576,   # 1 MiB
        backupCount=3,
        encoding="utf-8",
        delay=True,           # don't hold the file open between writes
    )                         # (required on Windows to allow log rotation)
    _file_handler.setLevel(_FILE_LEVEL)
    _file_handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    ))
except Exception:
    # If we can't open the file (read-only FS, sandboxed env), drop the
    # file handler silently. Console logging still works.
    _file_handler = None


# --- console handler -------------------------------------------------

_console_handler = RichHandler(
    console=console,
    show_time=True,
    show_path=False,
    show_level=True,
    rich_tracebacks=True,
    markup=False,
    log_time_format="[%H:%M:%S]",
    omit_repeated_times=True,
)
_console_handler.setLevel(_CONSOLE_LEVEL)


# --- wiring ----------------------------------------------------------
# Root receives everything; both handlers filter by level. Library logs
# inherit through the root → file handler captures them; the console
# handler only shows our explicit aicoder.* warnings + their own
# WARNING+ messages (which is what users want to see).

_root = logging.getLogger()
_root.setLevel(min(_CONSOLE_LEVEL, _FILE_LEVEL))
# Idempotent attach so re-imports during tests don't double-handler.
_handler_marker = "_aicoder_attached"
if not getattr(_console_handler, _handler_marker, False):
    _root.addHandler(_console_handler)
    setattr(_console_handler, _handler_marker, True)
if _file_handler is not None and not getattr(_file_handler, _handler_marker, False):
    _root.addHandler(_file_handler)
    setattr(_file_handler, _handler_marker, True)


# Library loggers: WARNING on console (so we still see auth/quota
# errors) but DEBUG+ flows to the file via inheritance.
for noisy in ("httpx", "httpcore", "openai", "urllib3"):
    lg = logging.getLogger(noisy)
    lg.setLevel(logging.DEBUG)  # so they reach the root → file handler
    # Console suppression is handled by ``_console_handler.setLevel``
    # which is already WARNING+ by default; nothing more to do.


# Our own logger keeps its dedicated namespace so callers can target
# verbosity per-module via the standard ``logging`` API
# (e.g. ``logging.getLogger('aicoder.llm').setLevel('DEBUG')``).
_aicoder = logging.getLogger("aicoder")
_aicoder.setLevel(logging.DEBUG)
_aicoder.propagate = True


def get_logger(name: str) -> logging.Logger:
    """Return a child of the aicoder logger.

    Use this in implementation modules (``agent.llm``, ``agent.tools.*``)
    when you want a hidden diagnostic that's visible in the debug log
    but not the user-facing console.
    """
    return logging.getLogger(f"aicoder.{name}")


def log_file_path() -> Path | None:
    """Path of the active debug log file, or ``None`` if file logging
    couldn't be initialized. Useful for ``/log`` slash commands and
    diagnostic dumps."""
    return _LOG_FILE if _file_handler is not None else None
