"""KRYTH CLI entry point.

Bootstraps the path so the bundled ``agent`` package is importable,
then delegates to the REPL main loop.

Sub-commands
------------
  ai-coder                          # start the REPL
  kryth                             # start the REPL
  xerocodeai                        # legacy command alias
"""

from __future__ import annotations

import sys
import os
from pathlib import Path


def _ensure_agent_on_path() -> None:
    """Add the directory that contains the ``agent`` package to sys.path.

    Installed layout (pip install):
        site-packages/
            kryth/            ← here
            agent/            ← sibling, added to sys.path

    Source-checkout layout (pip install -e . from kryth/):
        kryth/src/
            kryth/           ← here
            agent/           ← sibling
    """
    here = Path(__file__).resolve().parent   # …/kryth/
    pkg_root = here.parent                   # …/site-packages/ or …/src/

    agent_dir = pkg_root / "agent"
    if agent_dir.is_dir():
        path_str = str(pkg_root)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
        return

    # Fallback: repo root (two levels above src/kryth_cli/)
    repo_root = pkg_root.parent.parent
    fallback = str(repo_root)
    if fallback not in sys.path:
        sys.path.insert(0, fallback)


def main() -> None:
    """CLI entry point registered in pyproject.toml."""
    import argparse

    # Simple argument parsing for --version and --help
    parser = argparse.ArgumentParser(
        prog="kryth",
        description="KRYTH - Autonomous AI Coding Agent. Build, debug, and deploy applications with AI.",
        epilog="""
Examples:
  kryth                    Start the interactive REPL
  kryth "create a flask api"  Execute a single prompt
  kryth --version          Show version information
  kryth --help             Show this help message

For more information, visit: https://kryth.vercel.app/
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--version",
        action="version",
        version="kryth %(prog)s 1.1.0",
        help="Show version information and exit"
    )

    # Parse known args to allow passing through to REPL
    args, remaining = parser.parse_known_args()

    # If --version was called, argparse handles it and exits
    # If not, we continue to the REPL

    _ensure_agent_on_path()

    # ------------------------------------------------------------------ #
    # Load config + .env BEFORE importing the agent package.             #
    # agent/llm.py creates the OpenAI client at module import time, so   #
    # OPENAI_API_KEY must be set before any agent import happens.        #
    # ------------------------------------------------------------------ #

    # 1. Stored config (~/.ai-coder/config.json; legacy ~/.xerocodeai is read)
    try:
        from kryth.config import apply_to_env
        apply_to_env()
    except Exception:
        pass

    # 2. .env in cwd (overrides nothing already set)
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    except ImportError:
        pass

    # 3. Also check the repo root .env (for dev installs)
    try:
        from dotenv import load_dotenv
        here = Path(__file__).resolve().parent
        repo_env = here.parent.parent.parent / ".env"
        if repo_env.exists():
            load_dotenv(dotenv_path=repo_env, override=False)
    except Exception:
        pass

    # 4. If still no key, tell the user and open config TUI right away.
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        os.environ["OPENAI_API_KEY"] = "not-configured"
        _no_key_at_startup = True
    else:
        _no_key_at_startup = False

    # Now safe to import the agent package.
    try:
        import agent  # noqa: F401
    except ModuleNotFoundError as exc:
        print(
            f"[KRYTH] Could not import the agent package: {exc}\n"
            "Make sure KRYTH was installed from the source tree that "
            "includes the 'agent/' directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Delegate to the bundled REPL loop.
    from kryth._repl_main import main as _repl_main
    _repl_main()


if __name__ == "__main__":
    main()
