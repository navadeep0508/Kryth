import fnmatch
import hashlib
import sys

from agent import ui
from agent.env import getenv_bool
from agent.session import get_session
from agent.settings import load_user_settings


# Scalar string values longer than this are hashed in the signature so
# huge ``content`` payloads don't bloat the remembered-permissions
# cache key. The hash is still stable per identical value so the cache
# still functions for repeated calls.
_MAX_SCALAR_CHARS = 80


def _short_value(v) -> str:
    """Render a scalar value for the signature, hashing long strings to
    keep keys bounded. Mirrors ``str`` for everything else."""
    if isinstance(v, str) and len(v) > _MAX_SCALAR_CHARS:
        digest = hashlib.sha1(v.encode("utf-8", "replace")).hexdigest()[:10]
        return f"sha1:{digest}[{len(v)}c]"
    return str(v)


def _args_signature(tool: str, args: dict) -> str:
    """Return a stable, human-readable signature for ``args``.

    Used both as a glob target (``check_permission``) and as a cache key
    (``remembered_permissions``). The key must capture every scalar arg
    so an "always allow" for ``run_command(timeout=15)`` does NOT
    auto-allow ``run_command(timeout=600)`` — the user only consented
    to the original shape.

    Rules:
      * The dominant arg (``command`` for shell, ``path`` for file ops)
        leads the signature so glob patterns like ``run_command:git *``
        work intuitively.
      * Remaining scalar args are appended as ``key=value`` pairs in
        sorted order. Long strings are summarized as a sha1 fingerprint
        so the key stays bounded.
      * Non-scalar args (lists, dicts) are summarized as a type+length
        stub.
    """
    if not isinstance(args, dict):
        return ""

    primary: str | None = None
    if isinstance(args.get("command"), str):
        primary = args["command"]
    elif isinstance(args.get("path"), str):
        primary = args["path"]

    extras: list[str] = []
    for k in sorted(args.keys()):
        if k in ("command", "path") and primary is not None:
            continue
        v = args[k]
        if isinstance(v, (str, int, float, bool)) or v is None:
            extras.append(f"{k}={_short_value(v)}")
        elif isinstance(v, (list, tuple)):
            extras.append(f"{k}=list[{len(v)}]")
        elif isinstance(v, dict):
            extras.append(f"{k}=dict[{len(v)}]")
        else:
            extras.append(f"{k}={type(v).__name__}")

    parts: list[str] = []
    if primary is not None:
        # Primary is a string by construction; apply the same trim.
        parts.append(_short_value(primary))
    parts.extend(extras)
    return " ".join(parts)


def _rule_matches(rule: str, tool: str, sig: str) -> bool:
    if ":" not in rule:
        return rule == tool or rule == "*"
    rule_tool, pattern = rule.split(":", 1)
    if rule_tool not in (tool, "*"):
        return False
    return pattern == "*" or fnmatch.fnmatchcase(sig, pattern)


def _combined_rules(session) -> dict:
    """Merge the user's ``settings.json`` rules with the active
    profile preset. User rules take precedence (listed first so the
    deny/allow/ask scans hit them before profile rules); the profile
    fills the gaps. The profile ``default`` is used only when the user
    didn't explicitly set one.

    Uses ``load_user_settings`` (not ``load_settings``) so the built-in
    DEFAULTS — which mirror the ``default`` profile — do NOT leak in
    and override more restrictive profiles like ``readonly`` / ``safe``.
    Without this distinction, ``readonly`` would still ask for writes
    because the framework default ``ask`` list matched first.
    """
    from agent.profiles import get as _get_profile

    user_perms = load_user_settings().get("permissions", {}) or {}
    profile = _get_profile(getattr(session, "profile", None))

    return {
        "deny":  list(user_perms.get("deny", [])  or []) + list(profile.deny),
        "allow": list(user_perms.get("allow", []) or []) + list(profile.allow),
        "ask":   list(user_perms.get("ask", [])   or []) + list(profile.ask),
        "default": user_perms.get("default") or profile.default,
    }


def check_permission(tool: str, args: dict) -> str:
    session = get_session()
    sig = _args_signature(tool, args)

    # Wildcard key set by "always" covers all args; exact key is legacy fallback.
    remembered = (
        session.remembered_permissions.get(f"{tool}|*")
        or session.remembered_permissions.get(f"{tool}|{sig}")
    )
    if remembered:
        return remembered

    perms = _combined_rules(session)

    for rule in perms["deny"]:
        if _rule_matches(rule, tool, sig):
            return "deny"

    for rule in perms["allow"]:
        if _rule_matches(rule, tool, sig):
            return "allow"

    for rule in perms["ask"]:
        if _rule_matches(rule, tool, sig):
            return "ask"

    return perms["default"]


def remember(tool: str, args: dict, decision: str) -> None:
    session = get_session()
    # Use wildcard key so "always" covers ALL future calls to this tool,
    # not just this specific argument signature.
    session.remembered_permissions[f"{tool}|*"] = decision


def ask_user(tool: str, args: dict) -> str:
    """Ask the user to allow/deny a tool call.

    Interactive arrow-key selector with three options:
      y = allow once
      a = allow and remember for this session
      n = deny

    Non-TTY / EOF / Ctrl+C → deny.
    Setting ``KRYTH_ASSUME_YES=1`` auto-allows. The previous
    ``XEROCODEAI_ASSUME_YES=1`` name is also accepted.
    """
    import os

    sig = _args_signature(tool, args)

    if getenv_bool("KRYTH_ASSUME_YES"):
        return "allow"

    if not sys.stdin.isatty():
        ui.debug(f"(non-interactive stdin; denying {tool})")
        return "deny"

    try:
        choice = ui.permission_request(tool, sig)
    except (EOFError, KeyboardInterrupt):
        ui.muted("(cancelled)")
        return "deny"

    if choice == "a":
        remember(tool, args, "allow")
        return "allow"
    if choice == "y":
        return "allow"
    return "deny"
