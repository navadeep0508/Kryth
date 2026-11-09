from __future__ import annotations

from pathlib import Path


def status_bar(
    model_name: str,
    approval_label: str,
    context_ratio: float | None,
    cwd: Path | str | None,
    width: int,
    provider: str | None = None,
    agent_state: str | None = None,
    turn_tokens: int | None = None,
    git_branch: str | None = None,
) -> tuple[str, str] | None:
    """Build one compact footer; shorten fields to the actual terminal width."""
    if width < 1:
        return None

    model = model_name.rsplit("/", 1)[-1] if model_name else "No model"
    workspace = Path(cwd).name if cwd else ""
    approval = approval_label.removeprefix("approval:").strip()
    state = "READY" if not agent_state or agent_state == "idle" else agent_state.upper()
    context_label = f"CONTEXT {context_ratio * 100:.0f}%" if context_ratio is not None else None
    parts = [part for part in ("KRYTH", state, model, workspace, git_branch, context_label, provider) if part]
    if approval:
        parts.append(f"APPROVAL {approval.upper()}")

    style = "status"
    if context_ratio is not None:
        if context_ratio >= 0.9:
            style = "status.danger"
        elif context_ratio >= 0.7:
            style = "status.warn"

    text = " | ".join(parts)
    # Preserve model and workspace first; trim secondary metadata as width shrinks.
    for optional in (f"APPROVAL {approval.upper()}" if approval else None, provider, context_label, git_branch, workspace):
        if len(text) <= width:
            break
        if optional and optional in parts and optional not in {"KRYTH", state, model}:
            parts.remove(optional)
            text = " | ".join(parts)
    if len(text) > width:
        text = text[:width] if width <= 3 else text[: width - 3].rstrip() + "..."
    return (style, text) if text else None
