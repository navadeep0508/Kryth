"""Permission profile presets.

A profile is a curated bundle of ``(allow, ask, deny, default)`` glob
rules that the user can switch between with one command. It composes
with the user's ``settings.json`` — the user's explicit rules always
take precedence; the profile fills the gaps.

Available profiles
------------------

    readonly    Total observation mode. Reads allowed; everything else
                denied. Good for safe agent exploration of unfamiliar
                code.

    safe        Cautious. Read-only tools auto-allowed; mutating tools
                always prompt; truly destructive shell patterns hard-
                denied. Recommended for first runs on a new project.

    default     The historical default. Read-only auto-allowed; writes /
                edits / installers prompt; rm -rf and friends denied.

    auto        Productive middle. Writes + edits + non-destructive
                shell auto-allowed; installers, git push, docker, sudo
                prompt; catastrophic patterns denied. Best for trusted
                agent runs where you want to stay in the flow.

    yolo        Maximum autonomy. Everything allowed except a tight
                hard-deny list of catastrophic patterns. No prompts.
                Pair with /resume so the agent can recover if something
                goes sideways.

Each profile defines glob rules in the same syntax the rest of
``permissions.py`` understands (``tool:signature_glob``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.env import getenv, getenv_bool
from typing import Dict, List


# Catastrophic patterns that ALL profiles (except a future explicit
# "true-yolo") refuse to run. Centralised so we can tighten in one
# place when a new "oh no" pattern surfaces in the wild.
_HARD_DENY: tuple[str, ...] = (
    "run_command:rm -rf /*",
    "run_command:rm -rf ~*",
    "run_command:rm -rf $HOME*",
    "run_command:rm -rf .*",
    "run_command:shutdown*",
    "run_command:reboot*",
    "run_command:halt*",
    "run_command:mkfs*",
    "run_command:dd if=*",
    "run_command:: > *",     # truncation via `: > file`
    "run_command:format c:*",
)


_READ_ONLY_ALLOW: tuple[str, ...] = (
    "read_file:*",
    "list_files:*",
    "search_code:*",
    "grep:*",
    "glob:*",
    "semantic_search:*",
    "lookup_symbol:*",
    "todo_read:*",
    "task_output:*",
)


_SAFE_ALSO_ALLOW: tuple[str, ...] = (
    "todo_write:*",
    "exit_plan_mode:*",
)


_DEFAULT_ASK: tuple[str, ...] = (
    "write_file:*",
    "edit_file:*",
    "multi_edit:*",
    "add_memory:*",
    "run_command:pip install*",
    "run_command:npm install*",
    "run_command:yarn add*",
    "run_command:cargo install*",
    "run_command:apt*",
    "run_command:brew install*",
    "run_command:git push*",
    "run_command:git reset --hard*",
    "run_command:git clean*",
    "run_command:docker*",
    "run_command:sudo*",
)


_DEFAULT_DENY: tuple[str, ...] = (
    *_HARD_DENY,
    "run_command:rm -rf*",
    "delete_file:*",
)


_AUTO_ASK: tuple[str, ...] = (
    "run_command:rm *",
    "run_command:pip install*",
    "run_command:npm install*",
    "run_command:yarn add*",
    "run_command:cargo install*",
    "run_command:apt*",
    "run_command:brew install*",
    "run_command:git push*",
    "run_command:git reset --hard*",
    "run_command:git clean*",
    "run_command:docker*",
    "run_command:sudo*",
    "delete_file:*",
)


@dataclass(frozen=True)
class ProfileSpec:
    """The shape ``permissions.check_permission`` consumes."""
    name: str
    description: str
    default: str       # 'allow' | 'ask' | 'deny'
    allow: List[str] = field(default_factory=list)
    ask: List[str] = field(default_factory=list)
    deny: List[str] = field(default_factory=list)
    # UI severity — drives the banner colour. 'info' / 'warn' / 'danger'.
    severity: str = "info"


PROFILES: Dict[str, ProfileSpec] = {
    "readonly": ProfileSpec(
        name="readonly",
        description="Read-only. Everything mutating is denied without asking.",
        default="deny",
        allow=list(_READ_ONLY_ALLOW),
        ask=[],
        deny=list(_HARD_DENY),
        severity="info",
    ),
    "safe": ProfileSpec(
        name="safe",
        description="Cautious. Reads run; every mutation asks; catastrophic patterns hard-denied.",
        default="ask",
        allow=list(_READ_ONLY_ALLOW) + list(_SAFE_ALSO_ALLOW),
        ask=[],  # default='ask' covers everything else
        deny=list(_HARD_DENY) + ["run_command:rm -rf*"],
        severity="info",
    ),
    "default": ProfileSpec(
        name="default",
        description="Reads auto-allowed; writes / installers ask; rm -rf denied.",
        default="allow",
        allow=list(_READ_ONLY_ALLOW) + list(_SAFE_ALSO_ALLOW),
        ask=list(_DEFAULT_ASK),
        deny=list(_DEFAULT_DENY),
        severity="info",
    ),
    "auto": ProfileSpec(
        name="auto",
        description=(
            "Writes / edits / safe shell auto-allowed; installers + dangerous "
            "shell + delete prompt; catastrophic patterns denied."
        ),
        default="allow",
        allow=list(_READ_ONLY_ALLOW) + list(_SAFE_ALSO_ALLOW) + [
            "write_file:*", "edit_file:*", "multi_edit:*", "add_memory:*",
        ],
        ask=list(_AUTO_ASK),
        deny=list(_HARD_DENY),
        severity="warn",
    ),
    "yolo": ProfileSpec(
        name="yolo",
        description=(
            "Maximum autonomy. Everything allowed except catastrophic patterns. "
            "No prompts. Pair with /resume so you can recover if something breaks."
        ),
        default="allow",
        allow=["*"],
        ask=[],
        deny=list(_HARD_DENY),
        severity="danger",
    ),
}


DEFAULT_PROFILE: str = "default"


def names() -> list[str]:
    """Ordered list of available profile names (least-permissive first)."""
    return ["readonly", "safe", "default", "auto", "yolo"]


def get(name: str | None) -> ProfileSpec:
    """Return the named profile; falls back to ``default`` when the name
    is missing or unknown. Never raises."""
    if not name:
        return PROFILES[DEFAULT_PROFILE]
    return PROFILES.get(name.strip().lower(), PROFILES[DEFAULT_PROFILE])


def is_known(name: str | None) -> bool:
    return bool(name) and name.strip().lower() in PROFILES


def from_environment() -> str:
    """Pick the initial profile from environment variables.

    ``KRYTH_PROFILE`` is the explicit knob. The legacy
    ``KRYTH_ASSUME_YES=1`` is treated as a request for ``yolo`` so
    existing scripts keep working without code changes.
    """
    explicit = getenv("KRYTH_PROFILE").strip().lower()
    if explicit and explicit in PROFILES:
        return explicit
    if getenv_bool("KRYTH_ASSUME_YES"):
        return "yolo"
    return DEFAULT_PROFILE


__all__ = [
    "ProfileSpec",
    "PROFILES",
    "DEFAULT_PROFILE",
    "names",
    "get",
    "is_known",
    "from_environment",
]
