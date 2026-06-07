"""KRYTH visual theme: color, glyphs, and spacing.

The CLI identity is intentionally narrow:
acid gold core, white text, muted gray structure, green success, red errors.
Every primary state is anchored by the KRYTH core symbol: ◈.
"""

from __future__ import annotations

from rich.theme import Theme


# ---------------------------------------------------------------------------
# Glyphs
# ---------------------------------------------------------------------------

CORE = "◈"
WAITING = "◇"
ERROR = "◆"

BULLET = CORE
BULLET_DIM = WAITING
TEE = "│"
ARROW = ">"
CHECK = CORE
CROSS = ERROR
DOT = "·"
SEP = "  ·  "
SPINNER = "dots"
DIVIDER = "─"
HUD_DIVIDER = "─"
NEURAL = CORE
ROUTE = CORE
MEMORY = WAITING
SNAPSHOT = CORE
TOOL = CORE
WARN = ERROR


# ---------------------------------------------------------------------------
# Semantic styles
# ---------------------------------------------------------------------------

THEME = Theme({
    # Brand -----------------------------------------------------------
    "kryth.core": "bold #E8FF3A",
    "kryth.core.dim": "#9CA81F",
    "kryth.white": "#FFFFFF",
    "kryth.muted": "#888888",
    "kryth.faint": "#4B4B4B",
    "kryth.error": "#FF5A5A",
    "kryth.success": "#4ADE80",

    # Surfaces --------------------------------------------------------
    "surface.base": "#0B0B0B",
    "surface.panel": "#111111",
    "surface.raised": "#171717",
    "hud.blue": "#E8FF3A",
    "hud.cyan": "#E8FF3A",
    "hud.aqua": "#4ADE80",
    "hud.violet": "#E8FF3A",
    "hud.pink": "#E8FF3A",
    "hud.amber": "#E8FF3A",
    "hud.glow": "bold #FFFFFF",
    "hud.scan": "#4B4B4B",
    "hud.border": "#E8FF3A",
    "hud.border.dim": "#333333",
    "hud.border.warn": "#E8FF3A",

    # Severities ------------------------------------------------------
    "log.debug": "#666666",
    "log.info": "#E8FF3A",
    "log.warn": "#E8FF3A",
    "log.error": "#FF5A5A",
    "log.success": "#4ADE80",

    # Tool lifecycle --------------------------------------------------
    "tool.bullet": "bold #E8FF3A",
    "tool.name": "bold #FFFFFF",
    "tool.arg": "#CFCFCF",
    "tool.tee": "#4B4B4B",
    "tool.result": "#CFCFCF",
    "tool.error": "#FF5A5A",
    "tool.ok": "#4ADE80",
    "tool.retry": "#E8FF3A",

    # Sections --------------------------------------------------------
    "section.plan": "bold #E8FF3A",
    "section.exec": "bold #E8FF3A",
    "section.retry": "bold #E8FF3A",
    "section.diff": "bold #E8FF3A",
    "section.shell": "bold #E8FF3A",
    "section.test": "bold #E8FF3A",
    "section.summary": "bold #4ADE80",
    "section.subagent": "bold #E8FF3A",

    # Status ----------------------------------------------------------
    "status.model": "#FFFFFF",
    "status.mode": "#E8FF3A",
    "status.tokens": "#888888",
    "status.elapsed": "#888888",
    "status.label": "#888888",

    # Roles -----------------------------------------------------------
    "role.user": "bold #FFFFFF",
    "role.assistant": "bold #E8FF3A",
    "role.system": "#888888",
    "role.tool": "#E8FF3A",
    "role.reasoning": "italic #CFCFCF",

    # Diff ------------------------------------------------------------
    "diff.add": "#4ADE80",
    "diff.del": "#FF5A5A",
    "diff.gutter": "#666666",
    "diff.hunk": "#E8FF3A",

    # Agent states ----------------------------------------------------
    "ai.ready": "bold #E8FF3A",
    "ai.thinking": "bold #E8FF3A",
    "ai.planning": "bold #E8FF3A",
    "ai.executing": "bold #E8FF3A",
    "ai.waiting": "#888888",
    "ai.confident": "bold #FFFFFF",
    "ai.idle": "#888888",
    "motion.hot": "bold #0B0B0B on #E8FF3A",
    "motion.cool": "#E8FF3A",

    # Misc ------------------------------------------------------------
    "muted": "#888888",
    "accent": "#E8FF3A",
    "hint": "italic #888888",
    "kbd": "bold #FFFFFF on #222222",
    "title": "bold #FFFFFF",
    "divider": "#333333",

    # ── Next-Gen UI ──────────────────────────────────────────────────

    # UI Layers
    "layer.executive": "bold #E8FF3A",
    "layer.engineering": "bold #FFFFFF",
    "layer.terminal": "bold #CFCFCF",
    "layer.debug": "bold #888888",
    "layer.badge": "bold #0B0B0B on #E8FF3A",
    "layer.badge.dim": "#888888 on #1A1A1A",

    # Mission
    "mission.title": "bold #FFFFFF",
    "mission.goal": "bold #E8FF3A",
    "mission.progress": "#4ADE80",
    "mission.stage": "#CFCFCF",
    "mission.eta": "#888888",
    "mission.border": "#E8FF3A",
    "mission.complete": "bold #4ADE80",
    "mission.failed": "bold #FF5A5A",

    # Engineering actions
    "eng.running": "bold #E8FF3A",
    "eng.done": "#4ADE80",
    "eng.failed": "#FF5A5A",
    "eng.pending": "#888888",
    "eng.section": "bold #FFFFFF",
    "eng.detail": "#888888",

    # Agent dashboard
    "agent.idle": "#888888",
    "agent.running": "bold #E8FF3A",
    "agent.done": "#4ADE80",
    "agent.failed": "#FF5A5A",
    "agent.bar": "#E8FF3A",
    "agent.bar.bg": "#333333",
    "agent.name": "bold #FFFFFF",

    # Timeline
    "timeline.time": "#4B4B4B",
    "timeline.info": "#CFCFCF",
    "timeline.success": "#4ADE80",
    "timeline.warn": "#E8FF3A",
    "timeline.error": "#FF5A5A",
    "timeline.marker": "#E8FF3A",

    # DAG
    "dag.active": "bold #E8FF3A on #1A1A00",
    "dag.done": "#4ADE80",
    "dag.pending": "#888888",
    "dag.failed": "#FF5A5A",
    "dag.arrow": "#4B4B4B",

    # Terminal dashboard
    "term.success": "#4ADE80",
    "term.failed": "#FF5A5A",
    "term.metric.label": "#888888",
    "term.metric.value": "bold #FFFFFF",
    "term.expand": "italic #888888",

    # Approval UI
    "approval.border": "#E8FF3A",
    "approval.action": "#FFFFFF",
    "approval.risk.low": "#4ADE80",
    "approval.risk.medium": "#E8FF3A",
    "approval.risk.high": "#FF5A5A",

    # Reflection UI
    "reflect.worked": "#4ADE80",
    "reflect.failed": "#FF5A5A",
    "reflect.learned": "#E8FF3A",
    "reflect.border": "#9B59B6",

    # Memory UI
    "memory.count": "bold #9B59B6",
    "memory.rate": "#4ADE80",
    "memory.workflow": "#E8FF3A",
    "memory.border": "#9B59B6",

    # Browser automation dashboard
    "browser.step.done": "#4ADE80",
    "browser.step.running": "#E8FF3A",
    "browser.step.failed": "#FF5A5A",
    "browser.border": "#00BFFF",
})


INDENT_1 = "  "
INDENT_2 = "    "


__all__ = [
    "ARROW",
    "BULLET",
    "BULLET_DIM",
    "CHECK",
    "CORE",
    "CROSS",
    "DIVIDER",
    "DOT",
    "ERROR",
    "HUD_DIVIDER",
    "INDENT_1",
    "INDENT_2",
    "MEMORY",
    "NEURAL",
    "ROUTE",
    "SEP",
    "SNAPSHOT",
    "SPINNER",
    "TEE",
    "THEME",
    "TOOL",
    "WAITING",
    "WARN",
]
