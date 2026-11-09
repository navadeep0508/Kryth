"""KRYTH's restrained terminal palette. Backgrounds stay terminal-owned."""

NEUTRALS = {
    "obsidian": "rgb(16,17,20)",
    "graphite": "rgb(29,32,37)",
    "surface": "rgb(37,41,48)",
    "muted": "rgb(133,139,145)",
    "primary": "rgb(236,237,239)",
    "secondary": "rgb(161,165,173)",
    "accent": "rgb(223,255,0)",
    # Compatibility aliases used by existing Rich components.
    "bright": "rgb(236,237,239)",
    "silver": "rgb(161,165,173)",
    "black": "rgb(16,17,20)",
    "slate": "rgb(37,41,48)",
}

HUES = {
    "blue": "rgb(133,139,145)",
    "green": "rgb(139,175,136)",
    "amber": "rgb(214,170,101)",
    "teal": "rgb(133,139,145)",
    "violet": "rgb(133,139,145)",
    "lilac": "rgb(133,139,145)",
    "orange": "rgb(133,139,145)",
    "magenta": "rgb(133,139,145)",
    "red": "rgb(215,122,122)",
}

PALETTE = {**NEUTRALS, **HUES}

# Tool categories share one neutral; success and failure carry the color signal.
TOOL_COLOURS = {
    name: PALETTE["silver"]
    for name in ("read", "write", "bash", "network", "memory", "mcp", "git", "subagent")
}


def rgb_parts(name: str) -> tuple[int, int, int]:
    r, g, b = (int(part) for part in PALETTE[name][4:-1].split(","))
    return r, g, b


def hex_colour(name: str) -> str:
    r, g, b = rgb_parts(name)
    return f"#{r:02x}{g:02x}{b:02x}"


def tool_colour(tool_kind: str | None) -> str:
    return TOOL_COLOURS.get(tool_kind or "", PALETTE["silver"])
