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
        version="kryth %(prog)s 2.4.0",
        help="Show version information and exit"
    )

    parser.add_argument(
        "--validate",
        action="store_true",
        default=False,
        help="Run model health check for all configured roles and exit",
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

    # 1. Stored config (~/.kryth/config.json; legacy paths migrated on first load)
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

    # 4. If no NVIDIA key set, prompt once — everything else defaults.
    if not os.environ.get("NVIDIA_API_KEY", "").strip():
        try:
            from kryth.config import _load, _save
            cfg = _load()
            if not cfg.get("nvidia_api_key", "").strip():
                print("\n  Welcome to KRYTH!\n")
                print("  KRYTH runs on NVIDIA NIM models (main · planner · summarizer · vision).")
                print("  Get a free API key at: https://build.nvidia.com\n")
                print("  NVIDIA API key: ", end="", flush=True)
                try:
                    nvidia_key = input().strip()
                except (EOFError, KeyboardInterrupt):
                    nvidia_key = ""
                if nvidia_key:
                    cfg["nvidia_api_key"] = nvidia_key
                    _save(cfg)
                    os.environ["NVIDIA_API_KEY"] = nvidia_key
                    print("  API key saved. You're ready to go!\n")
                else:
                    print("  No key entered — set it anytime with /config\n")
            else:
                os.environ["NVIDIA_API_KEY"] = cfg["nvidia_api_key"]
        except Exception:
            pass

    # Wire NVIDIA NIM as the LLM backend.
    # llm.py uses OPENAI_API_KEY + KRYTH_BASE_URL + KRYTH_*_MODEL env vars.
    # When NVIDIA_API_KEY is set and no overrides exist, point everything at NIM.
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if nvidia_key:
        # Mirror key so OpenAI-compatible client authenticates against NIM
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            os.environ["OPENAI_API_KEY"] = nvidia_key

        # Set NIM base URL (overrides openai.com default)
        if not os.environ.get("KRYTH_BASE_URL", "").strip():
            os.environ["KRYTH_BASE_URL"] = "https://integrate.api.nvidia.com/v1"

        # Set NIM model defaults for each role (from nim_router/config.py chains)
        _NIM_DEFAULTS = {
            "KRYTH_MAIN_MODEL":       "moonshotai/kimi-k2.6",
            "KRYTH_PLANNER_MODEL":    "moonshotai/kimi-k2.6",
            "KRYTH_SUMMARIZER_MODEL": "stepfun-ai/step-3.5-flash",
            "KRYTH_VISION_MODEL":     "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        }
        for env_var, model in _NIM_DEFAULTS.items():
            # Only set if not already overridden by user in config or env
            if not os.environ.get(env_var, "").strip():
                os.environ[env_var] = model
    else:
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            os.environ["OPENAI_API_KEY"] = "not-configured"

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

    # Initialize model_config system (loads ~/.kryth/config.yaml if present)
    try:
        from agent.model_config.loader import load_config
        load_config()
    except Exception:
        pass  # Fails silently — env-var fallback takes over

    # --validate flag: run startup health check and exit
    validate = getattr(args, "validate", False) or os.environ.get("KRYTH_VALIDATE_MODELS", "").lower() in ("1", "true")
    if validate:
        try:
            from agent.model_config.validator import validate_and_report
            validate_and_report(verbose=True)
        except Exception as e:
            print(f"  Validation failed: {e}", file=sys.stderr)
        return  # Exit after validation

    # Delegate to the bundled REPL loop.
    # Pass any positional arguments as the initial prompt for non-interactive mode.
    initial_prompt = " ".join(remaining).strip() if remaining else ""
    from kryth._repl_main import main as _repl_main
    _repl_main(initial_prompt=initial_prompt)


if __name__ == "__main__":
    main()
