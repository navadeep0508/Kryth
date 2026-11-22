"""KRYTH Browser — a unified browser engine backed by the vendored anomalyco/browse relay.

Replaces the KUBA in-process CDP browser engine.  A Rust relay binary (vendored
from anomalyco/browse, MIT licensed) drives a real Chrome/Edge instance through
the browser extension or CDP.  This Python package talks to the relay over HTTP
and exposes KRYTH tool-shaped browser actions.
"""

from .manager import BrowserManager
from .tool import KRYTHBrowserTool
from .connect import BrowserConnectTool
from .disconnect import BrowserDisconnectTool
from .status import BrowserStatusTool

__all__ = [
    "BrowserManager",
    "KRYTHBrowserTool",
    "BrowserConnectTool",
    "BrowserDisconnectTool",
    "BrowserStatusTool",
]
