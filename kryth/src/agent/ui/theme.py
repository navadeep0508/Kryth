"""KRYTH visual theme — UI v3 "Signature Lime".

A premium, minimal terminal identity built around a single signature accent:

    RGB(223,255,0)  ·  #DFFF00

The palette is deliberately narrow: one bright accent with a short gradient,
three near-black surfaces, three tiers of neutral text, and three semantic
states (success / warning / error). No rainbow, no noise — sharp and confident.
Every primary brand state is anchored by the KRYTH core symbol: ◈.
"""

from __future__ import annotations

from rich.theme import Theme


# ---------------------------------------------------------------------------
# Signature palette
# ---------------------------------------------------------------------------

# Accent gradient (bright → soft → glow) — UI v4 six-stop ramp. Used for the
# brand mark, gradient rules, and active states. Ordered light-to-deep.
ACCENT_PRIMARY   = "#DFFF00"   # signature
ACCENT_SECONDARY = "#D7FF10"
ACCENT_MID       = "#CAFB22"
ACCENT_SOFT      = "#BAF03B"
ACCENT_GLOW      = "#A6E256"
ACCENT_DEEP      = "#91D473"   # deepest gradient stop / dimmed accent
ACCENT_BORDER    = "#6E8C12"   # dim accent for de-emphasized borders

GRADIENT = (ACCENT_PRIMARY, ACCENT_SECONDARY, ACCENT_MID,
            ACCENT_SOFT, ACCENT_GLOW, ACCENT_DEEP)

# Surfaces — UI v4 darker palette
BG_BASE   = "#050505"
BG_PANEL  = "#090909"
BG_RAISED = "#101010"
BG_ELEV   = "#161616"

# Text tiers — UI v4
TEXT_PRIMARY   = "#FFFFFF"
TEXT_SECONDARY = "#D8D8D8"
TEXT_MUTED     = "#8A8A8A"
TEXT_FAINT     = "#4B4B4B"

# Semantic states — UI v4
STATE_ERROR   = "#FF5A5A"
STATE_WARNING = "#FFB84D"
STATE_SUCCESS = "#6EFF7B"


# ---------------------------------------------------------------------------
# Glyphs
# ---------------------------------------------------------------------------

CORE = "◈"
WAITING = "◇"
ERROR = "◆"

BULLET = "●"          # filled bullet for active timeline steps
BULLET_DIM = "○"      # hollow bullet for pending / muted steps
TEE = "│"
ARROW = "›"
CHECK = "✓"
CROSS = "✗"
DOT = "·"
SEP = "  ·  "
SPINNER = "dots"      # rich braille spinner (⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏)
DIVIDER = "─"
HUD_DIVIDER = "━"     # heavier rule for premium section breaks
GRADIENT_RULE_CHAR = "━"
NEURAL = CORE
ROUTE = CORE
MEMORY = WAITING
SNAPSHOT = CORE
TOOL = CORE
WARN = "▲"

# Elegant spinner frame sets (consumed by activity / motion layers).
BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
CIRCLE_FRAMES = ["◐", "◓", "◑", "◒"]


# ---------------------------------------------------------------------------
# Semantic styles
# ---------------------------------------------------------------------------

THEME = Theme({
    # Brand -----------------------------------------------------------
    "kryth.core": f"bold {ACCENT_PRIMARY}",
    "kryth.core.dim": ACCENT_BORDER,
    "kryth.white": TEXT_PRIMARY,
    "kryth.muted": TEXT_MUTED,
    "kryth.faint": TEXT_FAINT,
    "kryth.error": STATE_ERROR,
    "kryth.success": STATE_SUCCESS,

    # Gradient ramp (for gradient rules / brand wordmark) -------------
    "grad.0": ACCENT_PRIMARY,
    "grad.1": ACCENT_SECONDARY,
    "grad.2": ACCENT_MID,
    "grad.3": ACCENT_SOFT,
    "grad.4": ACCENT_GLOW,

    # Surfaces --------------------------------------------------------
    "surface.base": BG_BASE,
    "surface.panel": BG_PANEL,
    "surface.raised": BG_RAISED,
    "hud.blue": ACCENT_PRIMARY,
    "hud.cyan": ACCENT_SECONDARY,
    "hud.aqua": STATE_SUCCESS,
    "hud.violet": ACCENT_SOFT,
    "hud.pink": ACCENT_MID,
    "hud.amber": STATE_WARNING,
    "hud.glow": f"bold {TEXT_PRIMARY}",
    "hud.scan": TEXT_FAINT,
    "hud.border": ACCENT_PRIMARY,
    "hud.border.dim": "#2A2A2A",
    "hud.border.warn": STATE_WARNING,

    # Severities ------------------------------------------------------
    "log.debug": "#666666",
    "log.info": ACCENT_PRIMARY,
    "log.warn": STATE_WARNING,
    "log.error": STATE_ERROR,
    "log.success": STATE_SUCCESS,

    # Tool lifecycle --------------------------------------------------
    "tool.bullet": f"bold {ACCENT_PRIMARY}",
    "tool.name": f"bold {TEXT_PRIMARY}",
    "tool.arg": TEXT_SECONDARY,
    "tool.tee": TEXT_FAINT,
    "tool.result": TEXT_SECONDARY,
    "tool.error": STATE_ERROR,
    "tool.ok": STATE_SUCCESS,
    "tool.retry": STATE_WARNING,

    # Sections --------------------------------------------------------
    "section.plan": f"bold {ACCENT_PRIMARY}",
    "section.exec": f"bold {ACCENT_PRIMARY}",
    "section.retry": f"bold {STATE_WARNING}",
    "section.diff": f"bold {ACCENT_PRIMARY}",
    "section.shell": f"bold {ACCENT_PRIMARY}",
    "section.test": f"bold {ACCENT_PRIMARY}",
    "section.summary": f"bold {STATE_SUCCESS}",
    "section.subagent": f"bold {ACCENT_PRIMARY}",

    # Status ----------------------------------------------------------
    "status.model": TEXT_PRIMARY,
    "status.mode": ACCENT_PRIMARY,
    "status.tokens": TEXT_MUTED,
    "status.elapsed": TEXT_MUTED,
    "status.label": TEXT_MUTED,

    # Roles -----------------------------------------------------------
    "role.user": f"bold {TEXT_PRIMARY}",
    "role.assistant": f"bold {ACCENT_PRIMARY}",
    "role.system": TEXT_MUTED,
    "role.tool": ACCENT_PRIMARY,
    "role.reasoning": f"italic {TEXT_SECONDARY}",

    # Diff ------------------------------------------------------------
    "diff.add": STATE_SUCCESS,
    "diff.del": STATE_ERROR,
    "diff.gutter": "#666666",
    "diff.hunk": ACCENT_PRIMARY,

    # Agent states ----------------------------------------------------
    "ai.ready": f"bold {ACCENT_PRIMARY}",
    "ai.thinking": f"bold {ACCENT_PRIMARY}",
    "ai.planning": f"bold {ACCENT_PRIMARY}",
    "ai.executing": f"bold {ACCENT_PRIMARY}",
    "ai.waiting": TEXT_MUTED,
    "ai.confident": f"bold {TEXT_PRIMARY}",
    "ai.idle": TEXT_MUTED,
    "motion.hot": f"bold {BG_BASE} on {ACCENT_PRIMARY}",
    "motion.cool": ACCENT_PRIMARY,

    # Misc ------------------------------------------------------------
    "muted": TEXT_MUTED,
    "accent": ACCENT_PRIMARY,
    "accent.soft": ACCENT_SOFT,
    "hint": f"italic {TEXT_MUTED}",
    "kbd": f"bold {TEXT_PRIMARY} on #222222",
    "title": f"bold {TEXT_PRIMARY}",
    "divider": "#2A2A2A",

    # ── Next-Gen UI ──────────────────────────────────────────────────

    # UI Layers
    "layer.executive": f"bold {ACCENT_PRIMARY}",
    "layer.engineering": f"bold {TEXT_PRIMARY}",
    "layer.terminal": f"bold {TEXT_SECONDARY}",
    "layer.debug": f"bold {TEXT_MUTED}",
    "layer.badge": f"bold {BG_BASE} on {ACCENT_PRIMARY}",
    "layer.badge.dim": f"{TEXT_MUTED} on #1A1A1A",

    # Mission
    "mission.title": f"bold {TEXT_PRIMARY}",
    "mission.goal": f"bold {ACCENT_PRIMARY}",
    "mission.progress": STATE_SUCCESS,
    "mission.stage": TEXT_SECONDARY,
    "mission.eta": TEXT_MUTED,
    "mission.border": ACCENT_BORDER,
    "mission.complete": f"bold {STATE_SUCCESS}",
    "mission.failed": f"bold {STATE_ERROR}",

    # Engineering actions
    "eng.running": f"bold {ACCENT_PRIMARY}",
    "eng.done": STATE_SUCCESS,
    "eng.failed": STATE_ERROR,
    "eng.pending": TEXT_MUTED,
    "eng.section": f"bold {TEXT_PRIMARY}",
    "eng.detail": TEXT_MUTED,

    # Agent dashboard
    "agent.idle": TEXT_MUTED,
    "agent.running": f"bold {ACCENT_PRIMARY}",
    "agent.done": STATE_SUCCESS,
    "agent.failed": STATE_ERROR,
    "agent.bar": ACCENT_PRIMARY,
    "agent.bar.bg": "#2A2A2A",
    "agent.name": f"bold {TEXT_PRIMARY}",

    # Timeline
    "timeline.time": TEXT_FAINT,
    "timeline.info": TEXT_SECONDARY,
    "timeline.success": STATE_SUCCESS,
    "timeline.warn": STATE_WARNING,
    "timeline.error": STATE_ERROR,
    "timeline.marker": ACCENT_PRIMARY,

    # DAG
    "dag.active": f"bold {ACCENT_PRIMARY} on #1A1A00",
    "dag.done": STATE_SUCCESS,
    "dag.pending": TEXT_MUTED,
    "dag.failed": STATE_ERROR,
    "dag.arrow": TEXT_FAINT,

    # Terminal dashboard
    "term.success": STATE_SUCCESS,
    "term.failed": STATE_ERROR,
    "term.metric.label": TEXT_MUTED,
    "term.metric.value": f"bold {TEXT_PRIMARY}",
    "term.expand": f"italic {TEXT_MUTED}",

    # Approval UI
    "approval.border": ACCENT_BORDER,
    "approval.action": TEXT_PRIMARY,
    "approval.risk.low": STATE_SUCCESS,
    "approval.risk.medium": STATE_WARNING,
    "approval.risk.high": STATE_ERROR,

    # Reflection UI
    "reflect.worked": STATE_SUCCESS,
    "reflect.failed": STATE_ERROR,
    "reflect.learned": ACCENT_PRIMARY,
    "reflect.border": ACCENT_BORDER,

    # Memory UI
    "memory.count": f"bold {ACCENT_SOFT}",
    "memory.rate": STATE_SUCCESS,
    "memory.workflow": ACCENT_PRIMARY,
    "memory.border": ACCENT_BORDER,

    # Browser automation dashboard
    "browser.step.done": STATE_SUCCESS,
    "browser.step.running": ACCENT_PRIMARY,
    "browser.step.failed": STATE_ERROR,
    "browser.border": ACCENT_BORDER,

    # Section hierarchy
    "section.header": f"bold {ACCENT_PRIMARY}",
    "section.icon":   ACCENT_PRIMARY,
    "tool.indent":    TEXT_FAINT,

    # ── UI v3 premium surfaces ───────────────────────────────────────
    "v3.brand":     f"bold {ACCENT_PRIMARY}",
    "v3.subtitle":  TEXT_MUTED,
    "v3.meta.key":  TEXT_MUTED,
    "v3.meta.val":  TEXT_SECONDARY,
    "v3.meta.accent": ACCENT_PRIMARY,
    "v3.card.title": f"bold {TEXT_PRIMARY}",
    "v3.card.path":  ACCENT_SOFT,
    "v3.card.border": ACCENT_BORDER,
    "v3.statusbar":  TEXT_MUTED,
    "v3.statusbar.accent": ACCENT_PRIMARY,
    "v3.step.active": f"bold {ACCENT_PRIMARY}",
    "v3.step.done":  STATE_SUCCESS,
    "v3.step.pending": TEXT_MUTED,
    "v3.duration":   TEXT_FAINT,
})


INDENT_1 = "  "
INDENT_2 = "    "

# ── Global layout constants ───────────────────────────────────────────────────
# All panels and indented lines should respect these values so the terminal
# has consistent breathing room instead of hugging the left edge.

LEFT_MARGIN    = 2   # spaces of left padding applied via rich.padding.Padding
SECTION_MARGIN = 1   # vertical spacing between sections
PANEL_MARGIN   = 2   # horizontal padding inside panels
CONTENT_MARGIN = 4   # indent for sub-items under a section header
MAX_PANEL_WIDTH = 100 # panels never stretch wider than this on wide terminals


def gradient_rule(width: int = 0, char: str = GRADIENT_RULE_CHAR):
    """Return a rich Text horizontal rule shaded across the signature gradient.

    Premium accent line for headers / section breaks. Width 0 → caller-sized.
    """
    from rich.text import Text
    try:
        from agent.ui.console import console
        w = width or min(console.size.width, MAX_PANEL_WIDTH)
    except Exception:
        w = width or 72
    w = max(8, int(w))
    ramp = GRADIENT
    t = Text()
    seg = max(1, w // len(ramp))
    for i in range(w):
        color = ramp[min(i // seg, len(ramp) - 1)]
        t.append(char, style=color)
    return t


__all__ = [
    "ACCENT_PRIMARY",
    "ACCENT_SECONDARY",
    "ACCENT_MID",
    "ACCENT_SOFT",
    "ACCENT_GLOW",
    "ACCENT_DEEP",
    "ACCENT_BORDER",
    "BG_ELEV",
    "ARROW",
    "BG_BASE",
    "BG_PANEL",
    "BG_RAISED",
    "BRAILLE_FRAMES",
    "BULLET",
    "BULLET_DIM",
    "CHECK",
    "CIRCLE_FRAMES",
    "CONTENT_MARGIN",
    "CORE",
    "CROSS",
    "DIVIDER",
    "DOT",
    "ERROR",
    "GRADIENT",
    "GRADIENT_RULE_CHAR",
    "gradient_rule",
    "HUD_DIVIDER",
    "INDENT_1",
    "INDENT_2",
    "LEFT_MARGIN",
    "MAX_PANEL_WIDTH",
    "MEMORY",
    "NEURAL",
    "PANEL_MARGIN",
    "ROUTE",
    "SEP",
    "SNAPSHOT",
    "SPINNER",
    "STATE_ERROR",
    "STATE_SUCCESS",
    "STATE_WARNING",
    "TEE",
    "TEXT_FAINT",
    "TEXT_MUTED",
    "TEXT_PRIMARY",
    "TEXT_SECONDARY",
    "THEME",
    "TOOL",
    "WAITING",
    "WARN",
]
