"""Skill executor — compose and run skill workflows.

Independent skills are installed in parallel (ThreadPoolExecutor),
cutting install time from O(N*download) to O(max_download).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from agent.ecosystem.local_registry import get_local_registry
from agent.ecosystem.installer import get_installer, SkillPackage
from agent import ui


def _resolve_chain(skill_ids: List[str]) -> List[str]:
    """Expand chains: if skill A chains B and C, add them too.
    Returns a deduplicated, dependency-ordered list.
    """
    registry = get_local_registry()
    result: list[str] = []
    seen: set[str] = set()

    def visit(sid: str) -> None:
        if sid in seen:
            return
        seen.add(sid)
        pkg = registry.get(sid)
        if pkg:
            for dep in pkg.chains:
                visit(dep)
        result.append(sid)

    for sid in skill_ids:
        visit(sid)
    return result


def _compose_prompts(skill_ids: List[str]) -> str:
    registry = get_local_registry()
    parts: list[str] = []
    for sid in skill_ids:
        text = registry.read_prompt(sid)
        if text:
            parts.append(text.strip())
        registry.usage_bump(sid)
    return "\n\n".join(parts)


def run_skill_workflow(
    skill_ids: List[str],
    user_input: str,
    *,
    show_progress: bool = True,
) -> Optional[str]:
    """Install missing skills, compose their prompts, return extra_system text.

    Returns the composed prompt string to inject as extra_system, or None
    if no skills resolved. The caller (run_agent) injects this into the
    agent's system context.
    """
    if not skill_ids:
        return None

    statuses: dict[str, str] = {}

    def _on_progress(skill_id: str, status: str) -> None:
        statuses[skill_id] = status
        if show_progress:
            icon = {
                "cached":      "✓",
                "installing":  "↓",
                "downloading": "↓",
                "installed":   "✓",
                "not_found":   "✗",
                "failed":      "✗",
            }.get(status, "·")
            ui.muted(f"  {icon} {skill_id}")

    if show_progress:
        ui.info(f"Resolving {len(skill_ids)} skill(s) in parallel...")

    # Install all skills concurrently — each download is independent
    def _install_one(skill_id: str) -> tuple[str, Optional[SkillPackage]]:
        inst = get_installer(progress=_on_progress)
        return skill_id, inst.ensure_installed(skill_id)

    results: dict[str, Optional[SkillPackage]] = {}
    with ThreadPoolExecutor(max_workers=min(len(skill_ids), 6)) as pool:
        futures = {pool.submit(_install_one, sid): sid for sid in skill_ids}
        for future in as_completed(futures):
            try:
                sid, pkg = future.result()
                results[sid] = pkg
            except Exception as exc:
                results[futures[future]] = None

    installed_ids = [sid for sid, pkg in results.items() if pkg is not None]
    if not installed_ids:
        return None

    # Expand chains then compose
    ordered = _resolve_chain(installed_ids)
    composed = _compose_prompts(ordered)

    if show_progress and ordered:
        ui.muted(f"  → {', '.join(ordered)}")

    return composed or None
