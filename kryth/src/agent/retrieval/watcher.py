"""File system watcher for incremental index updates.

Watches repository changes and propagates them to:
  - repo_index     (AST symbol index)
  - retriever      (semantic embeddings)
  - fts_index      (SQLite FTS5)
  - diskcache      (Graphify + ripgrep result cache)

Backend preference:
  watchfiles  (if installed) — written in Rust, very fast
  watchdog    (if installed) — pure-Python, cross-platform
  disabled    (neither available)

The watcher runs in a daemon thread so it never blocks the main loop.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional, Set

from agent.retrieval import config as cfg

logger = logging.getLogger(__name__)

_HAS_WATCHFILES: bool = False
_HAS_WATCHDOG: bool = False

try:
    import watchfiles as _watchfiles  # type: ignore[import]

    _HAS_WATCHFILES = True
except ImportError:
    pass

try:
    import watchdog.events       # type: ignore[import]
    import watchdog.observers    # type: ignore[import]

    _HAS_WATCHDOG = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Core watcher class
# ---------------------------------------------------------------------------


class RepoWatcher:
    """Watches a directory and fires callbacks on file changes."""

    def __init__(self, directory: str = ".") -> None:
        self._dir = os.path.abspath(directory)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._callbacks: list[Callable[[Set[str]], None]] = []
        self._active: bool = False

    def on_change(self, callback: Callable[[Set[str]], None]) -> None:
        """Register a callback. Called with a set of changed absolute paths."""
        self._callbacks.append(callback)

    def start(self) -> bool:
        """Start watching in a daemon thread. Returns True if started."""
        if not cfg.ENABLE_WATCHER:
            return False
        if self._active:
            return True

        if _HAS_WATCHFILES:
            return self._start_watchfiles()
        if _HAS_WATCHDOG:
            return self._start_watchdog()

        return False

    def stop(self) -> None:
        """Stop watching and join the daemon thread."""
        self._stop_event.set()
        self._active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def _notify(self, paths: Set[str]) -> None:
        for cb in self._callbacks:
            try:
                cb(paths)
            except Exception as exc:
                logger.debug("watcher callback error: %s", exc)

    def _start_watchfiles(self) -> bool:
        stop = self._stop_event

        def _loop() -> None:
            try:
                for changes in _watchfiles.watch(
                    self._dir,
                    stop_event=stop,
                    yield_on_timeout=True,
                ):
                    if stop.is_set():
                        break
                    if changes:
                        self._notify({str(c[1]) for c in changes})
            except Exception as exc:
                logger.debug("watchfiles loop exited: %s", exc)

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=_loop, daemon=True, name="kryth-watcher-wf"
        )
        self._thread.start()
        self._active = True
        return True

    def _start_watchdog(self) -> bool:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            watcher_ref = self

            class _Handler(FileSystemEventHandler):
                def on_any_event(self, event):
                    if event.is_directory:
                        return
                    path = (
                        getattr(event, "dest_path", None)
                        or getattr(event, "src_path", "")
                    )
                    if path:
                        watcher_ref._notify({str(path)})

            observer = Observer()
            observer.schedule(_Handler(), self._dir, recursive=True)

            def _loop() -> None:
                observer.start()
                try:
                    while not self._stop_event.is_set():
                        time.sleep(0.5)
                finally:
                    observer.stop()
                    observer.join()

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=_loop, daemon=True, name="kryth-watcher-wd"
            )
            self._thread.start()
            self._active = True
            return True
        except Exception:
            return False

    @property
    def active(self) -> bool:
        return (
            self._active
            and self._thread is not None
            and self._thread.is_alive()
        )


# ---------------------------------------------------------------------------
# Default change handler — invalidates all affected indexes
# ---------------------------------------------------------------------------


def _on_file_change(paths: Set[str]) -> None:
    """Invalidate repo_index, retriever, and FTS for changed paths."""
    path_list = list(paths)

    try:
        from agent import repo_index
        repo_index.invalidate(*path_list)
    except Exception:
        pass

    try:
        from agent import retriever
        retriever.invalidate()
    except Exception:
        pass

    try:
        from agent.retrieval.fts_index import get_index
        get_index().update(path_list)
    except Exception:
        pass

    # Evict any cached results for changed files
    try:
        from agent.retrieval.cache import get_cache, cache_key
        c = get_cache()
        for p in path_list:
            c.delete(cache_key("ripgrep", p))
            c.delete(cache_key("fts", p))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_watchers: dict[str, RepoWatcher] = {}


def get_watcher(directory: str = ".") -> RepoWatcher:
    """Return the RepoWatcher for *directory*, creating it if needed."""
    key = os.path.abspath(directory)
    if key not in _watchers:
        w = RepoWatcher(directory)
        w.on_change(_on_file_change)
        _watchers[key] = w
    return _watchers[key]


def start_watching(directory: str = ".") -> bool:
    """Start watching *directory*. Returns True if a backend started."""
    return get_watcher(directory).start()


def available() -> bool:
    """Return True if any watcher backend is installed."""
    return _HAS_WATCHFILES or _HAS_WATCHDOG
