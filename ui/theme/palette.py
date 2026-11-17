"""KRYTH's restrained terminal palette. Backgrounds stay terminal-owned."""

NEUTRALS = {
    "obsidian": "#090909",
    "surface_primary": "#111111",
    "graphite": "#171717",
    "surface": "#171717",
    "border": "#292929",
    "muted": "#737373",
    "subtle": "#525252",
    "primary": "#F5F5F5",
    "secondary": "#A3A3A3",
    "accent": "#E8FF3A",
    "brand": "#E8FF3A",
    "white": "#FFFFFF",
    # Compatibility aliases used by existing Rich components.
    "bright": "#F5F5F5",
    "silver": "#A3A3A3",
    "black": "#090909",
    "slate": "#171717",
}

HUES = {
    "blue": "#7890A4",
    "green": "#8BAF88",
    "amber": "#D6AA65",
    "teal": "#A3A3A3",
    "violet": "#A3A3A3",
    "lilac": "#A3A3A3",
    "orange": "#A3A3A3",
    "magenta": "#A3A3A3",
    "red": "#D77A7A",
}

PALETTE = {**NEUTRALS, **HUES}

# Tool categories share one neutral; success and failure carry the color signal.
TOOL_COLOURS = {
    name: PALETTE["silver"]
    for name in ("read", "write", "bash", "network", "memory", "mcp", "git", "subagent")
}


def rgb_parts(name: str) -> tuple[int, int, int]:
    value = PALETTE[name]
    if value.startswith("#"):
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]
    return tuple(int(part) for part in value[4:-1].split(","))  # type: ignore[return-value]


def hex_colour(name: str) -> str:
    r, g, b = rgb_parts(name)
    return f"#{r:02x}{g:02x}{b:02x}"


def tool_colour(tool_kind: str | None) -> str:
    return TOOL_COLOURS.get(tool_kind or "", PALETTE["silver"])
