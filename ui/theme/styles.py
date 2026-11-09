from rich.theme import Theme

from ui.theme.palette import PALETTE, TOOL_COLOURS

AGENT_THEME = Theme(
    {
        # Core semantic styles
        "info": PALETTE["bright"],
        "primary": PALETTE["primary"],
        "secondary": PALETTE["secondary"],
        "warning": PALETTE["amber"],
        "error": f"bold {PALETTE['red']}",
        "success": PALETTE["green"],
        "dim": "dim",
        "muted": PALETTE["muted"],
        "subtitle": PALETTE["silver"],
        "border": PALETTE["graphite"],
        "highlight": f"bold {PALETTE['accent']}",

        "user": f"bold {PALETTE['bright']}",
        "assistant": PALETTE["bright"],

        "reasoning": f"italic {PALETTE['silver']}",
        "reasoning.mark": PALETTE["violet"],

        "tool": PALETTE["silver"],
        **{f"tool.{kind}": colour for kind, colour in TOOL_COLOURS.items()},

        "code": PALETTE["silver"],

        "diff.plus": PALETTE["green"],
        "diff.minus": PALETTE["red"],

        # Markdown
        "md.h1": f"bold {PALETTE['bright']}",
        "md.h2": f"bold {PALETTE['bright']}",
        "md.h3": f"bold {PALETTE['silver']}",
        "md.bold": f"bold {PALETTE['bright']}",
        "md.bolditalic": f"bold italic {PALETTE['bright']}",
        "md.italic": f"italic {PALETTE['silver']}",
        "md.strike": f"strike {PALETTE['graphite']}",
        "md.code": PALETTE["silver"],
        "md.link": f"underline {PALETTE['silver']}",
        "md.bullet": PALETTE["silver"],
        "md.quote": f"italic {PALETTE['silver']}",
        "md.rule": PALETTE["graphite"],

        # Status bar / approval
        "status": PALETTE["silver"],
        "status.warn": PALETTE["amber"],
        "status.danger": f"bold {PALETTE['red']}",
        "approval": PALETTE["silver"],
        "approval.warn": PALETTE["amber"],
        "approval.danger": f"bold {PALETTE['red']}",
    }
)
