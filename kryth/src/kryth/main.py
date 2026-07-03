"""KRYTH CLI entry point.

Bootstraps the path so the bundled ``agent`` package is importable,
then either runs a management command or delegates to the REPL.

Sub-commands
------------
  ai-coder                          # start the REPL
  kryth                             # start the REPL
  xerocodeai                        # legacy command alias

Management commands
-------------------
  kryth add skill <id>              # install a skill
  kryth add mcp <name> [--command <cmd>] [--args ...] [--url <url>]
  kryth list mcps                   # list configured MCP servers
  kryth list skills                 # list installed skills
  kryth remove mcp <name>           # remove an MCP server config
  kryth remove skill <name>         # uninstall a skill
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


def _cmd_add_skill(name: str) -> None:
    """Install a skill from the ecosystem registry."""
    try:
        from agent.ecosystem.installer import get_installer
        installer = get_installer()
        pkg = installer.ensure_installed(name)
        if pkg:
            print(f"  ✓ Installed skill: {pkg.name} v{pkg.version}")
        else:
            print(f"  ✗ Skill '{name}' not found. Use 'kryth list skills' to see available skills.")
    except Exception as exc:
        print(f"  ✗ Failed to install skill: {exc}")


def _cmd_add_mcp(name: str, command: str | None, args: list[str] | None, url: str | None) -> None:
    """Configure an MCP server."""
    try:
        from agent.mcp import get_mcp_manager
        mgr = get_mcp_manager()
        if url:
            mgr.add_sse(name, url)
            print(f"  ✓ Added MCP server (SSE): {name} -> {url}")
        elif command:
            mgr.add_stdio(name, command, args or [])
            cmd_str = " ".join([command] + (args or []))
            print(f"  ✓ Added MCP server (stdio): {name} -> {cmd_str}")
        else:
            print("  ✗ Specify --command <cmd> for stdio or --url <url> for SSE")
    except Exception as exc:
        print(f"  ✗ Failed to add MCP server: {exc}")


def _cmd_list_mcps() -> None:
    """List configured MCP servers."""
    try:
        from agent.mcp import get_mcp_manager, MCP_AVAILABLE
        mgr = get_mcp_manager()
        servers = mgr.list_servers()
        if not servers:
            print("  No MCP servers configured. Use 'kryth add mcp <name> --command ...'")
            return
        print(f"  MCP servers ({len(servers)}):")
        if not MCP_AVAILABLE:
            print("  ⚠  MCP SDK not available (pip install mcp)")
        for name, config in sorted(servers.items()):
            stype = config.get("type", "stdio")
            if stype == "sse":
                detail = config.get("url", "")
            else:
                parts = [config.get("command", "")]
                parts.extend(config.get("args", []))
                detail = " ".join(parts)
            print(f"    {name:<20}  {stype:<6}  {detail}")
    except Exception as exc:
        print(f"  ✗ Failed to list MCP servers: {exc}")


def _cmd_list_skills() -> None:
    """List installed and available skills."""
    try:
        from agent.ecosystem.local_registry import get_local_registry
        from agent.ecosystem.remote_registry import get_remote_registry
        local = get_local_registry()
        remote = get_remote_registry()

        installed = local.list_all()
        if installed:
            print(f"  Installed skills ({len(installed)}):")
            for p in sorted(installed, key=lambda x: x.id):
                print(f"    ✓ {p.id:<24} v{p.version:<8} {p.author}")
        else:
            print("  No skills installed. Use 'kryth add skill <id>' to install one.")

        available = remote.list_skills()
        if available:
            print(f"\n  Available skills ({len(available)}):")
            for s in sorted(available, key=lambda x: x.id):
                mark = " [installed]" if local.has(s.id) else ""
                print(f"    {s.id:<24} v{s.version:<8} {s.tags[0] if s.tags else ''}{mark}")
        print("\n  Install: kryth add skill <id>")
    except Exception as exc:
        print(f"  ✗ Failed to list skills: {exc}")


def _cmd_remove_mcp(name: str) -> None:
    """Remove an MCP server configuration."""
    try:
        from agent.mcp import get_mcp_manager
        mgr = get_mcp_manager()
        if mgr.remove_server(name):
            print(f"  ✓ Removed MCP server: {name}")
        else:
            print(f"  ✗ MCP server '{name}' not found. Use 'kryth list mcps' to see configured servers.")
    except Exception as exc:
        print(f"  ✗ Failed to remove MCP server: {exc}")


def _cmd_remove_skill(name: str) -> None:
    """Uninstall a skill."""
    try:
        from agent.ecosystem.local_registry import get_local_registry
        registry = get_local_registry()
        if registry.has(name):
            registry.unregister(name)
            print(f"  ✓ Uninstalled skill: {name}")
        else:
            print(f"  ✗ Skill '{name}' not installed. Use 'kryth list skills' to see installed skills.")
    except Exception as exc:
        print(f"  ✗ Failed to uninstall skill: {exc}")


def main() -> None:
    """CLI entry point registered in pyproject.toml."""

    # ── Handle management subcommands before full init ──────────────────────
    if len(sys.argv) >= 2:
        cmd = sys.argv[1]
        if cmd in ("add", "list", "remove"):
            _ensure_agent_on_path()
            # Minimal path bootstrap — agent package must be reachable
            try:
                import agent  # noqa: F401
            except ModuleNotFoundError as exc:
                print(
                    f"[KRYTH] Could not import the agent package: {exc}\n"
                    "Make sure KRYTH was installed from the source tree.",
                    file=sys.stderr,
                )
                sys.exit(1)

            if cmd == "add" and len(sys.argv) >= 4:
                add_type = sys.argv[2]
                if add_type == "skill":
                    _cmd_add_skill(sys.argv[3])
                    return
                elif add_type == "mcp" and len(sys.argv) >= 4:
                    name = sys.argv[3]
                    _command, _args, _url = None, None, None
                    # Simple manual parsing for --command, --args, --url
                    i = 4
                    while i < len(sys.argv):
                        if sys.argv[i] == "--command" and i + 1 < len(sys.argv):
                            _command = sys.argv[i + 1]
                            i += 2
                        elif sys.argv[i] == "--args":
                            collected = []
                            i += 1
                            while i < len(sys.argv) and not sys.argv[i].startswith("--"):
                                collected.append(sys.argv[i])
                                i += 1
                            _args = collected
                        elif sys.argv[i] == "--url" and i + 1 < len(sys.argv):
                            _url = sys.argv[i + 1]
                            i += 2
                        else:
                            i += 1
                    _cmd_add_mcp(name, _command, _args, _url)
                    return

            elif cmd == "list":
                list_type = sys.argv[2] if len(sys.argv) >= 3 else ""
                if list_type == "mcps":
                    _cmd_list_mcps()
                elif list_type == "skills":
                    _cmd_list_skills()
                else:
                    print("Usage: kryth list mcps|skills")
                return

            elif cmd == "remove" and len(sys.argv) >= 4:
                remove_type = sys.argv[2]
                name = sys.argv[3]
                if remove_type == "mcp":
                    _cmd_remove_mcp(name)
                elif remove_type == "skill":
                    _cmd_remove_skill(name)
                else:
                    print("Usage: kryth remove mcp <name> | kryth remove skill <name>")
                return

            else:
                _print_management_help()
                return

    # ── Standard CLI flow: --version, --validate, or REPL ──────────────────
    import argparse

    parser = argparse.ArgumentParser(
        prog="kryth",
        description="KRYTH - Autonomous AI Coding Agent. Build, debug, and deploy applications with AI.",
        epilog="""
Examples:
  kryth                       Start the interactive REPL
  kryth "create a flask api"  Execute a single prompt
  kryth add skill <id>        Install an ecosystem skill
  kryth add mcp <name> ...    Configure an MCP server
  kryth list mcps|skills      List configured MCPs or skills
  kryth remove mcp|skill <n>  Remove an MCP or skill config

For more information, visit: https://kryth.vercel.app/
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )

    parser.add_argument(
        "--version",
        action="version",
        version="kryth %(prog)s 2.4.0",
        help="Show version information and exit",
    )

    parser.add_argument(
        "--validate",
        action="store_true",
        default=False,
        help="Run model health check for all configured roles and exit",
    )

    args, remaining = parser.parse_known_args()
    _ensure_agent_on_path()

    # ── Config + env bootstrap ─────────────────────────────────────────────
    try:
        from kryth.config import apply_to_env
        apply_to_env()
    except Exception:
        pass

    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    except ImportError:
        pass

    try:
        from dotenv import load_dotenv
        here = Path(__file__).resolve().parent
        repo_env = here.parent.parent.parent / ".env"
        if repo_env.exists():
            load_dotenv(dotenv_path=repo_env, override=False)
    except Exception:
        pass

    # API key prompt
    if not os.environ.get("API_KEY", "").strip() and not os.environ.get("NVIDIA_API_KEY", "").strip():
        try:
            from kryth.config import _load, _save
            cfg = _load()
            if not cfg.get("api_key", "").strip():
                print("\n  Welcome to KRYTH!\n")
                print("  Get a free API key at: https://build.nvidia.com\n")
                print("  API key: ", end="", flush=True)
                try:
                    api_key = input().strip()
                except (EOFError, KeyboardInterrupt):
                    api_key = ""
                if api_key:
                    cfg["api_key"] = api_key
                    _save(cfg)
                    os.environ["API_KEY"] = api_key
                    os.environ["NVIDIA_API_KEY"] = api_key
                    os.environ["OPENAI_API_KEY"] = api_key
                    print("  API key saved. You're ready to go!\n")
                else:
                    print("  No key entered — set it anytime with /config\n")
            else:
                os.environ["API_KEY"] = cfg["api_key"]
                os.environ["NVIDIA_API_KEY"] = cfg["api_key"]
                os.environ["OPENAI_API_KEY"] = cfg["api_key"]
        except Exception:
            pass

    # NVIDIA NIM wiring
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if nvidia_key:
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            os.environ["OPENAI_API_KEY"] = nvidia_key
        if not os.environ.get("KRYTH_BASE_URL", "").strip():
            os.environ["KRYTH_BASE_URL"] = "https://integrate.api.nvidia.com/v1"
        _NIM_DEFAULTS = {
            "KRYTH_MAIN_MODEL":       "moonshotai/kimi-k2.6",
            "KRYTH_PLANNER_MODEL":    "moonshotai/kimi-k2.6",
            "KRYTH_SUMMARIZER_MODEL": "stepfun-ai/step-3.5-flash",
            "KRYTH_VISION_MODEL":     "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        }
        for env_var, model in _NIM_DEFAULTS.items():
            if not os.environ.get(env_var, "").strip():
                os.environ[env_var] = model
    else:
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            os.environ["OPENAI_API_KEY"] = "not-configured"

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

    try:
        from agent.model_config.loader import load_config
        load_config()
    except Exception:
        pass

    validate = getattr(args, "validate", False) or os.environ.get("KRYTH_VALIDATE_MODELS", "").lower() in ("1", "true")
    if validate:
        try:
            from agent.model_config.validator import validate_and_report
            validate_and_report(verbose=True)
        except Exception as e:
            print(f"  Validation failed: {e}", file=sys.stderr)
        return

    initial_prompt = " ".join(remaining).strip() if remaining else ""
    from kryth._repl_main import main as _repl_main
    _repl_main(initial_prompt=initial_prompt)


def _print_management_help() -> None:
    print("""KRYTH management commands:

  kryth add skill <id>              install a skill from the ecosystem
  kryth add mcp <name> --command <cmd> [--args ...]
                                    configure a stdio MCP server
  kryth add mcp <name> --url <url>  configure an SSE MCP server
  kryth list mcps                   show configured MCP servers
  kryth list skills                 show installed & available skills
  kryth remove mcp <name>           remove an MCP server config
  kryth remove skill <name>         uninstall a skill

Run 'kryth' without arguments to start the interactive REPL.""")


if __name__ == "__main__":
    main()
