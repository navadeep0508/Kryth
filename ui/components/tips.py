"""Rotating startup tips shown during KRYTH initialization.

Tips are monochrome (no color codes) and render as compact panels. They rotate
at a configurable interval while the agent session is initializing, then the
tips view exits cleanly once initialization completes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from config.config import Config


@dataclass(frozen=True)
class Tip:
    """A single startup tip.

    `condition` is an optional callable that receives the Config and returns
    True when the tip is eligible to show. Use it to gate tips on features
    that may or may not be available.
    """

    id: str
    title: str
    description: str
    shortcut: str | None = None
    category: str = "general"
    condition: Callable[[Config], bool] | None = None


# ---------------------------------------------------------------------------
# Tip registry
# ---------------------------------------------------------------------------

TIP_REGISTRY: list[Tip] = [
    Tip(
        id="chat",
        title="Chat with KRYTH",
        description="Just type your message and press Enter to chat with the agent.",
        shortcut="Enter",
        category="navigation",
    ),
    Tip(
        id="slash-picker",
        title="Slash command picker",
        description="Type / to see every command; keep typing to filter, ↑/↓ to pick, Enter to run, Tab to fill in.",
        shortcut="/",
        category="navigation",
    ),
    Tip(
        id="execution-mode",
        title="Execution mode",
        description="Switch how freely the agent acts: /mode auto, /mode manual, /mode plan, /mode accept-edits.",
        shortcut="/mode",
        category="execution",
    ),
    Tip(
        id="cycle-mode",
        title="Cycle execution mode",
        description="Press Shift+Tab to quickly cycle through execution modes.",
        shortcut="Shift+Tab",
        category="execution",
    ),
    Tip(
        id="approval",
        title="Approval policy",
        description="Control when KRYTH asks before acting: /approval on_request, /approval auto, /approval never, /approval yolo.",
        shortcut="/approval",
        category="execution",
    ),
    Tip(
        id="compact",
        title="Context compaction",
        description="Compress conversation history to save tokens: /compact [preserve instructions].",
        shortcut="/compact",
        category="context",
    ),
    Tip(
        id="resume",
        title="Resume sessions",
        description="Continue a saved session: kryth --resume <id> or /resume <id>.",
        shortcut="/resume",
        category="sessions",
    ),
    Tip(
        id="thinking",
        title="Model reasoning",
        description="Control model thinking depth: /thinking [on|off|low|medium|high].",
        shortcut="/thinking",
        category="thinking",
    ),
    Tip(
        id="interrupt",
        title="Interrupt",
        description="Press Ctrl+C to interrupt the current turn.",
        shortcut="Ctrl+C",
        category="navigation",
    ),
]


# ---------------------------------------------------------------------------
# Tip selection
# ---------------------------------------------------------------------------

def select_tips(config: Config) -> list[Tip]:
    """Return the subset of registry tips eligible for the given config.

    A tip is included when its condition (if any) passes. All tips in the
    registry target features that exist in the current implementation, so by
    default every tip is eligible.
    """

    eligible: list[Tip] = []
    for tip in TIP_REGISTRY:
        if tip.condition is None or tip.condition(config):
            eligible.append(tip)
    return eligible


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_tip(tip: Tip, index: int, total: int) -> Text:
    """Render a single tip as a monochrome Rich Text block."""

    lines: list[tuple[str, str]] = []

    lines.append(("KRYTH TIP", "bold"))
    lines.append(("\n\n", ""))
    lines.append((tip.description, ""))
    lines.append(("\n\n", ""))

    meta_parts = []
    if tip.shortcut:
        meta_parts.append(f"[{tip.shortcut}]")
    meta_parts.append(tip.category)
    lines.append(("  " + "  ".join(meta_parts), "dim"))

    if total > 1:
        lines.append((f"\n  {index + 1}/{total}", ""))

    text = Text()
    for content, style in lines:
        text.append(content, style=style if style else None)
    return text


def render_tip_panel(tip: Tip, index: int, total: int, width: int | None = None) -> Panel:
    """Render a single tip inside a monochrome Panel."""

    panel_width = width
    if panel_width is None:
        panel_width = 60

    return Panel(
        render_tip(tip, index, total),
        title="KRYTH",
        border_style="default",
        width=panel_width,
    )


# ---------------------------------------------------------------------------
# Startup tips display
# ---------------------------------------------------------------------------

class StartupTips:
    """Displays rotating tips inside a Rich Live region during initialization.

    Tips rotate every ``startup_tip_interval`` seconds. Call :meth:`start`
    before beginning initialization and :meth:`stop` once it completes.
    """

    def __init__(self, console, config: Config) -> None:
        self.console = console
        self.config = config
        self.tips = select_tips(config)
        self._index = 0
        self._last_change = 0.0
        self._live: Live | None = None
        self._task: "asyncio.Task[None] | None" = None

    @property
    def current_tip(self) -> Tip | None:
        return self.tips[self._index] if self.tips else None

    def render(self) -> Panel:
        now = time.monotonic()
        if self.tips and now - self._last_change >= self.config.ui.startup_tip_interval:
            self._index = (self._index + 1) % len(self.tips)
            self._last_change = now

        tip = self.current_tip
        if tip is None:
            return Panel(Text(""), title="KRYTH", border_style="default")

        total = len(self.tips)
        width = None
        if self.console and self.console.width:
            width = min(60, max(20, self.console.width - 2))

        return render_tip_panel(tip, self._index, total, width=width)

    async def start(self) -> None:
        """Begin showing the Live tips region."""

        self.stop()
        self._last_change = time.monotonic()
        self._live = Live(
            self.render(),
            console=self.console,
            refresh_per_second=2,
            transient=True,
        )
        self._live.start()
        import asyncio

        self._task = asyncio.create_task(self._animate())

    async def _animate(self) -> None:
        import asyncio

        try:
            while True:
                await asyncio.sleep(0.2)
                if self._live is not None:
                    self._live.update(self.render())
        except asyncio.CancelledError:
            pass

    def stop(self) -> None:
        """Stop the Live tips region and clear the display."""

        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._live is not None:
            self._live.stop()
            self._live = None
