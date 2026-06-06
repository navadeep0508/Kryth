"""Startup health check — validate each role's model is reachable.

Called when KRYTH_VALIDATE_MODELS=1 or --validate CLI flag.
Not blocking by default (opt-in).

Output example:
  ✓ Main Model        (openai/gpt-4o-mini via openai)
  ✓ Planner Model     (openai/gpt-4o-mini via openai)
  ✗ Vision Model      (401 — invalid API key)
  ✓ Summary Model     (openai/gpt-4o-mini via openai)
  ~ Extraction Model  (not configured — using main)
  ~ Reasoning Model   (not configured — using main)

  System Ready (4/6 dedicated, 2/6 fallback)
"""

from __future__ import annotations

from agent.model_config.schema import ROLES, ROLE_DESCRIPTIONS


def validate_and_report(verbose: bool = True) -> dict[str, str]:
    """Ping every role's model and return a status dict.

    Returns: {role: "ok" | "fallback" | error_message}
    """
    from agent.model_config.router import get_llm
    from agent.model_config.loader import get_config

    cfg = get_config()
    results: dict[str, str] = {}

    for role in ROLES:
        # Check if role has a dedicated model in specialized mode
        spec = cfg.models.get(role) if cfg.mode == "specialized" else None
        is_fallback = not (spec and spec.model)

        try:
            client, model = get_llm(role)
            # Minimal 1-token ping
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
            )
            results[role] = "fallback" if is_fallback else "ok"
        except KeyboardInterrupt:
            raise
        except Exception as e:
            error = str(e)[:120]
            results[role] = f"error: {error}"

    if verbose:
        _print_report(results, cfg)

    return results


def _print_report(results: dict[str, str], cfg) -> None:
    from agent.model_config.router import get_llm

    ok_count = sum(1 for v in results.values() if v == "ok")
    fb_count = sum(1 for v in results.values() if v == "fallback")
    err_count = sum(1 for v in results.values() if v.startswith("error"))

    print()
    print("  Model Configuration Health Check")
    print("  " + "─" * 50)

    for role in ROLES:
        status = results.get(role, "unknown")
        try:
            _, model = get_llm(role)
        except Exception:
            model = "?"

        if status == "ok":
            icon = "✓"
        elif status == "fallback":
            icon = "~"
        else:
            icon = "✗"

        role_label = f"{role.title()} Model".ljust(20)
        if status == "ok":
            print(f"  {icon} {role_label} {model}")
        elif status == "fallback":
            print(f"  {icon} {role_label} {model}  (using main)")
        else:
            print(f"  {icon} {role_label} {status}")

    print()
    mode_label = "unified" if cfg.mode == "unified" else "specialized"
    print(f"  Mode: {mode_label}")

    if err_count == 0:
        print(f"  System Ready  ({ok_count} dedicated · {fb_count} via main)")
    else:
        print(f"  ⚠  {err_count} model(s) unreachable")
    print()


def quick_check(role: str = "main") -> bool:
    """Lightweight check that returns True if the main model is reachable."""
    try:
        from agent.model_config.router import get_llm
        client, model = get_llm(role)
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
        )
        return True
    except Exception:
        return False
