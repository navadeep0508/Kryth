"""KRYTH Desktop entry point.

Starts the Desktop Runtime Bridge server that the Tauri frontend connects
to. Prints "READY" to stdout once the socket is bound so Tauri's sidecar
management knows when to open the app window.

Usage (development):
    python -m kryth.desktop_main [--host 127.0.0.1] [--port 7765]

Usage (Tauri sidecar in production):
    <sidecar binary> --host 127.0.0.1 --port 7765
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _ensure_paths() -> None:
    src = Path(__file__).resolve().parent.parent
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> None:
    _ensure_paths()

    parser = argparse.ArgumentParser(description="KRYTH Desktop Bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7765)
    args = parser.parse_args()

    # Load KRYTH config before agent modules import (llm.py reads env at
    # module-import time, so env must be populated first).
    try:
        from kryth.config import apply_to_env
        apply_to_env()
    except Exception:
        pass

    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError:
        pass

    # Desktop mode: use Ponytail execution profile (minimal, efficient)
    # unless user explicitly set a different profile
    import os
    if not os.environ.get("KRYTH_EXEC_PROFILE"):
        os.environ["KRYTH_EXEC_PROFILE"] = "ponytail"

    # Desktop mode: use 'auto' permission profile — auto-approves writes/edits
    # to avoid token-wasting approval pauses on every file write
    if not os.environ.get("KRYTH_PROFILE"):
        os.environ["KRYTH_PROFILE"] = "auto"

    # Set timeouts to prevent infinite hangs
    if not os.environ.get("KRYTH_TTFT_TIMEOUT"):
        os.environ["KRYTH_TTFT_TIMEOUT"] = "60"  # 60s max wait for first token
    if not os.environ.get("KRYTH_READ_TIMEOUT"):
        os.environ["KRYTH_READ_TIMEOUT"] = "60"  # 60s max for API response

    from kryth_desktop_runtime import start
    start(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
