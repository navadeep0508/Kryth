"""KRYTH Live Mission Operations Center — Textual split-pane dashboard.

A full live operations center that shows:
  • Agent hierarchy tree (CEO → Lead → Workers)
  • Parallel execution graph (tool categories × bar)
  • Live tool stream (per-agent timestamped actions)
  • Live patch/write viewer (diff & progress bar)
  • Mission DAG panel (dependency tree with status icons)
  • File ownership table
  • Model router visualization
  • Background intelligence feed
  • Live performance counters

Architecture:
    EventBus (existing)
         │
         ├─► Rich renderer  (existing log output → LogPane)
         └─► push_event()  → DashboardState → Textual widgets (4 FPS)

Usage:
    from agent.ui.dashboard import start_dashboard, stop_dashboard, push_event
    start_dashboard(goal="Build Auth", total_agents=5, total_layers=2)
    push_event("agent_update", id="be-1", role="Backend #1",
               status="running", task="write auth.py", parent="backend-lead")
    push_event("tool_stream", agent="Backend #1", icon="✎",
               action="Writing auth.py", detail="287/542 lines")
    stop_dashboard()
"""
from __future__ import annotations

import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AgentNode:
    id: str
    role: str
    status: str = "waiting"   # waiting|running|done|failed|repairing
    task: str = ""
    parent: str = ""          # parent agent id (empty = root)
    model: str = ""
    turns: int = 0
    files: List[str] = field(default_factory=list)

@dataclass
class StreamEntry:
    ts: str
    agent: str
    icon: str
    action: str
    detail: str = ""

@dataclass
class PatchEntry:
    filename: str
    lines_written: int = 0
    lines_total: int = 0
    diff_lines: List[Tuple[str, str]] = field(default_factory=list)  # ("+"/"-", text)
    is_new: bool = False

@dataclass
class DAGEntry:
    id: str
    label: str
    status: str = "pending"   # pending|active|done|failed
    depth: int = 0
    children: List[str] = field(default_factory=list)

@dataclass
class DashboardState:
    goal: str = ""
    progress: int = 0
    total_agents: int = 0
    total_layers: int = 0
    layer: int = 0
    start_time: float = field(default_factory=time.monotonic)

    # Agent tree
    agents: Dict[str, AgentNode] = field(default_factory=dict)

    # Parallel tool bars — category → active count
    tool_parallel: Dict[str, int] = field(default_factory=dict)
    tool_counts: Dict[str, int] = field(default_factory=dict)

    # Live tool stream (last 20)
    stream: deque = field(default_factory=lambda: deque(maxlen=20))

    # Live patch viewer (last active patch)
    patch: Optional[PatchEntry] = None

    # Mission DAG
    dag_nodes: Dict[str, DAGEntry] = field(default_factory=dict)
    dag_order: List[str] = field(default_factory=list)

    # File ownership
    ownership: Dict[str, str] = field(default_factory=dict)

    # Model routing
    model_routes: Dict[str, str] = field(default_factory=dict)

    # Background intelligence feed (last 8)
    intel: deque = field(default_factory=lambda: deque(maxlen=8))

    # Performance
    tokens: int = 0
    peak_agents: int = 0
    parallelism: int = 0
    speedup: float = 1.0

    # Spawn animation queue
    spawn_queue: deque = field(default_factory=lambda: deque(maxlen=10))

    @property
    def elapsed(self) -> str:
        s = int(time.monotonic() - self.start_time)
        m, sec = divmod(s, 60)
        return f"{m:02d}:{sec:02d}"

    @property
    def active_count(self) -> int:
        return sum(1 for a in self.agents.values() if a.status == "running")

    @property
    def done_count(self) -> int:
        return sum(1 for a in self.agents.values() if a.status == "done")


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_state = DashboardState()
_lock = threading.RLock()
_event_queue: queue.Queue = queue.Queue()
_app_instance = None
_app_thread: Optional[threading.Thread] = None
_running = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_dashboard(goal: str = "", total_agents: int = 0, total_layers: int = 0) -> None:
    """Initialise dashboard state. The scheduler drives the Live display directly."""
    global _running, _state, _app_instance
    _running = True
    with _lock:
        _state = DashboardState(goal=goal[:60], total_agents=total_agents,
                                total_layers=total_layers)
        _state.model_routes = {
            "Planning": os.getenv("KRYTH_MODEL_PLANNING", "Planner"),
            "Coding":   os.getenv("KRYTH_MODEL_CODING", "Main"),
            "Vision":   os.getenv("KRYTH_MODEL_VISION", "Vision"),
            "Search":   os.getenv("KRYTH_MODEL_SEARCH", "Fast"),
            "Summary":  os.getenv("KRYTH_MODEL_SUMMARY", "Fast"),
        }


def stop_dashboard() -> None:
    global _running, _app_instance
    _running = False
    if _app_instance is not None:
        try:
            _app_instance.exit()
        except Exception:
            pass
        _app_instance = None


def push_event(kind: str, **data) -> None:
    if _running:
        try:
            _event_queue.put_nowait({"kind": kind, **data})
        except queue.Full:
            pass


def get_active() -> bool:
    return _running


# ---------------------------------------------------------------------------
# Event processing
# ---------------------------------------------------------------------------

_TOOL_CATEGORY = {
    "read_file": "READ", "list_files": "READ", "glob": "READ",
    "grep": "SEARCH", "search_code": "SEARCH", "semantic_search": "SEARCH",
    "fts_search": "SEARCH", "ast_search": "SEARCH", "search_smart": "SEARCH",
    "write_file": "WRITE", "edit_file": "EDIT", "multi_edit": "EDIT",
    "run_command": "CMD", "shell_exec": "CMD",
    "browser_click": "BROWSER", "browser_type": "BROWSER",
    "browser_screenshot": "BROWSER", "browser_use_task": "BROWSER",
    "run_tests": "TEST", "run_install": "TEST",
    "spawn_agent": "AGENT", "spawn_agents_parallel": "AGENT",
}

_TOOL_ICON = {
    "READ": "📖", "WRITE": "✎", "EDIT": "✎", "SEARCH": "◈",
    "CMD": "⚡", "BROWSER": "🌐", "TEST": "🧪", "AGENT": "◈",
}


def _process_events() -> bool:
    changed = False
    try:
        while True:
            ev = _event_queue.get_nowait()
            _handle(ev)
            changed = True
    except queue.Empty:
        pass
    return changed


def _handle(ev: dict) -> None:  # noqa: C901
    kind = ev.get("kind", "")
    with _lock:
        s = _state

        if kind == "agent_update":
            aid = ev.get("id") or ev.get("name", "")
            if not aid:
                return
            role = ev.get("role") or ev.get("name", aid)
            status = ev.get("status", "waiting")
            task = ev.get("task", "")
            parent = ev.get("parent", "")
            model = ev.get("model", "")

            if aid not in s.agents:
                s.agents[aid] = AgentNode(id=aid, role=role, parent=parent)
                if status == "running":
                    s.spawn_queue.append(f"+ {role}")
            node = s.agents[aid]
            node.role = role or node.role
            node.status = status
            node.parent = parent or node.parent
            if task:
                node.task = task[:55]
            if model:
                node.model = model
            # Track peak
            active = sum(1 for a in s.agents.values() if a.status == "running")
            if active > s.peak_agents:
                s.peak_agents = active
                s.parallelism = active

        elif kind == "tool_used":
            tool = ev.get("tool", "")
            agent = ev.get("agent", "")
            cat = _TOOL_CATEGORY.get(tool, "OTHER")
            s.tool_counts[cat] = s.tool_counts.get(cat, 0) + 1
            # Update parallel bars — show current active count (decay over time)
            s.tool_parallel[cat] = min(8, s.tool_parallel.get(cat, 0) + 1)
            # Stream entry
            icon = _TOOL_ICON.get(cat, "·")
            action = ev.get("action", tool)
            detail = ev.get("detail", "")
            s.stream.append(StreamEntry(
                ts=time.strftime("%H:%M:%S"),
                agent=agent or "—",
                icon=icon,
                action=action[:40],
                detail=detail[:35],
            ))

        elif kind == "tool_stream":
            s.stream.append(StreamEntry(
                ts=time.strftime("%H:%M:%S"),
                agent=ev.get("agent", "—"),
                icon=ev.get("icon", "·"),
                action=ev.get("action", "")[:40],
                detail=ev.get("detail", "")[:35],
            ))

        elif kind == "patch":
            fname = ev.get("filename", "?")
            lines_w = int(ev.get("lines_written", 0))
            lines_t = int(ev.get("lines_total", 0))
            diff = ev.get("diff", [])  # list of ("+"/"-", text)
            is_new = bool(ev.get("is_new", False))
            s.patch = PatchEntry(filename=fname, lines_written=lines_w,
                                 lines_total=lines_t, diff_lines=diff[:8],
                                 is_new=is_new)

        elif kind == "dag_update":
            nodes = ev.get("nodes", [])
            for n in nodes:
                nid = str(n.get("id", ""))
                if not nid:
                    continue
                if nid not in s.dag_nodes:
                    s.dag_order.append(nid)
                    s.dag_nodes[nid] = DAGEntry(
                        id=nid, label=str(n.get("label", nid)),
                        depth=int(n.get("depth", 0)),
                    )
                e = s.dag_nodes[nid]
                e.status = str(n.get("status", e.status))

        elif kind == "file_locked":
            path = ev.get("path", "")
            agent = ev.get("agent", "")
            if path:
                s.ownership[os.path.basename(path)] = agent
                s.stream.append(StreamEntry(
                    ts=time.strftime("%H:%M:%S"),
                    agent=agent, icon="🔒",
                    action=f"Locked {os.path.basename(path)}", detail="",
                ))

        elif kind == "file_unlocked":
            path = ev.get("path", "")
            s.ownership.pop(os.path.basename(path), None)

        elif kind == "intel":
            msg = ev.get("message", "")
            if msg:
                s.intel.append(f"✓ {msg}")

        elif kind == "progress":
            s.progress = max(0, min(100, int(ev.get("percent", s.progress))))

        elif kind == "layer":
            s.layer = int(ev.get("current", s.layer))

        elif kind == "tokens":
            s.tokens += int(ev.get("count", 0))

        elif kind == "speedup":
            s.speedup = float(ev.get("value", s.speedup))

        elif kind == "timeline":
            msg = ev.get("message", "")
            if msg:
                s.intel.append(f"◈ {msg}")

        elif kind == "parallel_tools":
            tools = ev.get("tools", [])
            # Parse "read_file×3" labels
            s.tool_parallel = {}
            for t in tools:
                if "×" in t:
                    parts = t.split("×")
                    cat = _TOOL_CATEGORY.get(parts[0].strip(), "OTHER")
                    try:
                        s.tool_parallel[cat] = int(parts[1].strip())
                    except (ValueError, IndexError):
                        s.tool_parallel[cat] = 1

        # Decay parallel bars every few cycles
        for cat in list(s.tool_parallel.keys()):
            if s.tool_parallel[cat] > 0:
                s.tool_parallel[cat] = max(0, s.tool_parallel[cat] - 1)


# ---------------------------------------------------------------------------
# Textual App
# ---------------------------------------------------------------------------

_STATUS_ICON = {
    "waiting":   "⌛",
    "running":   "◈",
    "done":      "✓",
    "failed":    "✖",
    "repairing": "⟳",
    "planning":  "◈",
}
_STATUS_STYLE = {
    "waiting":   "dim",
    "running":   "bold cyan",
    "done":      "bold green",
    "failed":    "bold red",
    "repairing": "bold yellow",
}


def _bar(n: int, max_n: int = 8, width: int = 10) -> str:
    filled = min(width, int(n * width / max(max_n, 1)))
    return "■" * filled + "·" * (width - filled)


def _render_agent_tree(s: DashboardState) -> list:
    """Build agent tree lines (role hierarchy via parent field)."""
    from rich.text import Text
    lines = []
    roots = [a for a in s.agents.values() if not a.parent]
    children_map: Dict[str, List[AgentNode]] = {}
    for a in s.agents.values():
        if a.parent:
            children_map.setdefault(a.parent, []).append(a)

    def _node(agent: AgentNode, prefix: str, is_last: bool) -> None:
        icon = _STATUS_ICON.get(agent.status, "·")
        style = _STATUS_STYLE.get(agent.status, "white")
        connector = "└── " if is_last else "├── "
        task_str = f"  {agent.task[:30]}" if agent.task else ""
        lines.append(Text.from_markup(
            f"[dim]{prefix}{connector}[/dim][{style}]{icon} {agent.role[:20]}[/{style}][dim]{task_str}[/dim]"
        ))
        children = children_map.get(agent.id, [])
        child_prefix = prefix + ("    " if is_last else "│   ")
        for i, child in enumerate(children):
            _node(child, child_prefix, i == len(children) - 1)

    for i, root in enumerate(roots):
        icon = _STATUS_ICON.get(root.status, "·")
        style = _STATUS_STYLE.get(root.status, "white")
        task_str = f"  {root.task[:30]}" if root.task else ""
        lines.append(Text.from_markup(
            f"[{style}]{icon} {root.role[:24]}[/{style}][dim]{task_str}[/dim]"
        ))
        children = children_map.get(root.id, [])
        for j, child in enumerate(children):
            _node(child, "", j == len(children) - 1)

    if not lines:
        lines.append(Text("  (agents spawning…)", style="dim"))
    return lines


def _run_app() -> None:
    """Entry point for the dashboard thread.

    Stops the existing Rich spinner, then starts its own Rich Live display
    showing the dashboard panel at the bottom of the terminal while logs
    scroll above it via live.console.print().
    """
    global _app_instance
    _rich_dashboard_loop()


def _rich_dashboard_loop() -> None:
    """Render the dashboard by updating the existing Rich spinner 4×/sec."""
    from rich.text import Text
    from rich.panel import Panel
    from rich.console import Group
    from rich.rule import Rule

    def _build_renderable():
        with _lock:
            s = _state
        rows = []

        # Header: goal + progress bar
        pct = s.progress
        bar_w = 28
        filled = int(pct * bar_w / 100)
        bar = Text()
        bar.append("█" * filled, style="bold cyan")
        bar.append("░" * (bar_w - filled), style="dim")
        bar.append(f" {pct}%", style="bold")

        header = Text()
        header.append("  ◈ ", style="bold cyan")
        header.append(s.goal[:45] or "Mission", style="bold white")
        header.append(f"  [{s.elapsed}]", style="dim")
        rows.append(header)
        rows.append(bar)
        rows.append(Text(
            f"  Layer {s.layer}/{s.total_layers}  ·  "
            f"{s.active_count} running  ·  {s.done_count}/{s.total_agents} done",
            style="dim",
        ))

        # Agents (compact, max 6)
        agents = list(s.agents.values())[:6]
        if agents:
            rows.append(Rule(style="dim"))
            for a in agents:
                icon = _STATUS_ICON.get(a.status, "·")
                style = _STATUS_STYLE.get(a.status, "white")
                task = f"  {a.task[:28]}" if a.task else ""
                t = Text()
                t.append(f"  {icon} ", style=style)
                t.append(a.role[:20], style=style)
                t.append(task, style="dim")
                rows.append(t)

        # Parallel bars (compact, only active)
        active_cats = {k: v for k, v in s.tool_parallel.items() if v > 0}
        if active_cats:
            rows.append(Rule(style="dim"))
            parts = []
            for cat, n in list(active_cats.items())[:5]:
                parts.append(f"{cat}{'■' * min(n, 4)}")
            rows.append(Text("  " + "  ".join(parts), style="cyan"))

        # Last action
        if s.stream:
            last = list(s.stream)[-1]
            rows.append(Text(
                f"  {last.icon} {last.agent[:12]}  {last.action[:30]}",
                style="dim",
            ))

        # Intel (last 2)
        intel = list(s.intel)[-2:]
        if intel:
            rows.append(Rule(style="dim"))
            for msg in intel:
                rows.append(Text(f"  {msg[:50]}", style="dim"))

        return Panel(Group(*rows), border_style="cyan", padding=(0, 1))

    # Stop the existing spinner so we can own the terminal with our Live
    try:
        from agent.ui.renderer import _activity
        _activity._stop_cycler()
        spinner = getattr(_activity, "_spinner", None)
        if spinner:
            spinner.stop()
    except Exception:
        pass

    from agent.ui.console import console as rich_console
    from rich.live import Live

    try:
        with Live(
            _build_renderable(),
            console=rich_console,
            refresh_per_second=4,
            transient=True,
            vertical_overflow="visible",
        ) as live:
            # Redirect console.print so log lines print ABOVE the Live panel
            orig_print = rich_console.print

            def _live_print(*args, **kwargs):
                try:
                    live.console.print(*args, **kwargs)
                except Exception:
                    try:
                        orig_print(*args, **kwargs)
                    except Exception:
                        pass

            rich_console.print = _live_print
            try:
                while _running:
                    _process_events()
                    live.update(_build_renderable())
                    time.sleep(0.25)
            finally:
                rich_console.print = orig_print
    except Exception:
        # Live failed — fall back to periodic prints
        while _running:
            _process_events()
            with _lock:
                s = _state
            try:
                rich_console.print(
                    f"  ◈ {s.goal[:35]}  {s.progress}%  "
                    f"{s.active_count} running  [{s.elapsed}]",
                    style="dim",
                )
            except Exception:
                pass
            time.sleep(4)


def _run_app_textual() -> None:
    try:
        from textual.app import App, ComposeResult
        from textual.widgets import Static, Log, TabbedContent, TabPane
        from textual.containers import Horizontal, Vertical
        from rich.text import Text
        from rich.panel import Panel
        from rich.console import Group
        from rich.rule import Rule

        class MissionPanel(Static):
            """Main right-panel — agent tree, parallel graph, stream, patch."""

            def on_mount(self) -> None:
                self.set_interval(0.25, self._tick)

            def _tick(self) -> None:
                _process_events()
                self.update(self._render())

            def _render(self):
                with _lock:
                    s = _state
                sections = []

                # ── Header ─────────────────────────────────────────────
                pct = s.progress
                bar_w = 22
                filled = int(pct * bar_w / 100)
                bar = f"[cyan]{'█' * filled}[/cyan][dim]{'░' * (bar_w - filled)}[/dim] [bold]{pct}%[/bold]"
                sections.append(Text.from_markup(
                    f"[bold cyan]  KRYTH[/bold cyan]  [dim]{s.goal[:40]}[/dim]  "
                    f"[dim][{s.elapsed}][/dim]"
                ))
                sections.append(Text.from_markup(f"  {bar}"))
                sections.append(Text.from_markup(
                    f"  [dim]Layer {s.layer}/{s.total_layers} · "
                    f"{s.active_count} active · {s.done_count}/{s.total_agents} done[/dim]"
                ))
                sections.append(Rule(style="dim"))

                # ── Spawn animation ────────────────────────────────────
                if s.spawn_queue:
                    for msg in list(s.spawn_queue)[-3:]:
                        sections.append(Text.from_markup(f"  [bold green]{msg}[/bold green]"))
                    sections.append(Text(""))

                # ── Agent Tree ─────────────────────────────────────────
                sections.append(Text.from_markup("  [bold]Agents[/bold]"))
                sections.extend(_render_agent_tree(s))
                sections.append(Rule(style="dim"))

                # ── Parallel Execution Graph ───────────────────────────
                sections.append(Text.from_markup("  [bold]Parallel Execution[/bold]"))
                cats = ["READ","WRITE","EDIT","SEARCH","CMD","BROWSER","TEST"]
                for cat in cats:
                    n = s.tool_parallel.get(cat, 0)
                    total = s.tool_counts.get(cat, 0)
                    if n > 0 or total > 0:
                        bar_str = _bar(n, 8, 8)
                        sections.append(Text.from_markup(
                            f"  [dim]{cat:<7}[/dim] [cyan]{bar_str}[/cyan]"
                            + (f" [dim]{n}[/dim]" if n else "")
                        ))
                sections.append(Rule(style="dim"))

                # ── Live Tool Stream ───────────────────────────────────
                sections.append(Text.from_markup("  [bold]Live Actions[/bold]"))
                stream_items = list(s.stream)[-6:]
                for entry in stream_items:
                    sections.append(Text.from_markup(
                        f"  [dim]{entry.ts}[/dim] [cyan]{entry.agent[:14]}[/cyan]"
                    ))
                    detail_str = f" [dim]{entry.detail}[/dim]" if entry.detail else ""
                    sections.append(Text.from_markup(
                        f"  {entry.icon} [white]{entry.action}[/white]{detail_str}"
                    ))
                if not stream_items:
                    sections.append(Text("  (waiting for actions…)", style="dim"))
                sections.append(Rule(style="dim"))

                # ── Live Patch Viewer ──────────────────────────────────
                if s.patch:
                    p = s.patch
                    if p.is_new and p.lines_total > 0:
                        filled_p = int(p.lines_written * 16 / max(p.lines_total, 1))
                        pbar = "█" * filled_p + "░" * (16 - filled_p)
                        sections.append(Text.from_markup(
                            f"  [bold]✎ NEW[/bold] [cyan]{p.filename}[/cyan]"
                        ))
                        sections.append(Text.from_markup(
                            f"  [dim]{p.lines_written}/{p.lines_total} lines[/dim]"
                        ))
                        sections.append(Text.from_markup(f"  [cyan]{pbar}[/cyan]"))
                    elif p.diff_lines:
                        sections.append(Text.from_markup(
                            f"  [bold]✎ PATCH[/bold] [cyan]{p.filename}[/cyan]"
                        ))
                        for sign, txt in p.diff_lines[:5]:
                            if sign == "+":
                                sections.append(Text.from_markup(
                                    f"  [green]+ {txt[:38]}[/green]"
                                ))
                            elif sign == "-":
                                sections.append(Text.from_markup(
                                    f"  [red]- {txt[:38]}[/red]"
                                ))
                    sections.append(Rule(style="dim"))

                # ── File Ownership ─────────────────────────────────────
                if s.ownership:
                    sections.append(Text.from_markup("  [bold]File Ownership[/bold]"))
                    for fname, owner in list(s.ownership.items())[:5]:
                        sections.append(Text.from_markup(
                            f"  [dim]{fname[:18]:18}[/dim] [cyan]{owner[:16]}[/cyan]"
                        ))
                    sections.append(Rule(style="dim"))

                # ── Models ─────────────────────────────────────────────
                if s.model_routes:
                    sections.append(Text.from_markup("  [bold]Models[/bold]"))
                    for role, model in s.model_routes.items():
                        sections.append(Text.from_markup(
                            f"  [dim]{role:<10}[/dim] [white]{model[:16]}[/white]"
                        ))
                    sections.append(Rule(style="dim"))

                # ── Background Intelligence ────────────────────────────
                if s.intel:
                    sections.append(Text.from_markup("  [bold]Optimizer[/bold]"))
                    for msg in list(s.intel)[-5:]:
                        sections.append(Text.from_markup(f"  [dim]{msg[:42]}[/dim]"))
                    sections.append(Rule(style="dim"))

                # ── Performance ────────────────────────────────────────
                sections.append(Text.from_markup("  [bold]Performance[/bold]"))
                sections.append(Text.from_markup(
                    f"  [dim]Tokens[/dim]    [white]{s.tokens // 1000}k[/white]"
                ))
                sections.append(Text.from_markup(
                    f"  [dim]Parallel[/dim]  [cyan]{s.parallelism}[/cyan]"
                ))
                sections.append(Text.from_markup(
                    f"  [dim]Speedup[/dim]   [green]{s.speedup:.1f}x[/green]"
                ))
                sections.append(Text.from_markup(
                    f"  [dim]Peak[/dim]      [white]{s.peak_agents}[/white]"
                ))

                return Panel(
                    Group(*sections),
                    border_style="cyan",
                    padding=(0, 0),
                )

        class LogPane(Log):
            pass

        class KRYTHApp(App):
            CSS = """
            Screen { layout: horizontal; }
            #logs {
                width: 60%;
                height: 100%;
                overflow-y: scroll;
            }
            #panel {
                width: 40%;
                height: 100%;
                overflow-y: scroll;
            }
            """

            def compose(self) -> ComposeResult:
                yield LogPane(id="logs", highlight=True, markup=True)
                yield MissionPanel(id="panel")

            def on_mount(self) -> None:
                try:
                    from agent.ui.console import console as rc
                    log_pane = self.query_one("#logs", LogPane)
                    _patch_console(rc, log_pane, self)
                except Exception:
                    pass

        app = KRYTHApp()
        _app_instance = app
        app.run()

    except ImportError:
        # Textual not installed — use ANSI overlay (works everywhere)
        _ansi_overlay_loop()
    except Exception:
        # Textual crashed — fall back to ANSI overlay
        _ansi_overlay_loop()


def _patch_console(rich_console, log_pane, app) -> None:
    orig = rich_console.print
    def _new(*args, **kwargs):
        try:
            orig(*args, **kwargs)
        except Exception:
            pass
        try:
            text = " ".join(str(a) for a in args).strip()
            if text:
                app.call_from_thread(log_pane.write_line, text)
        except Exception:
            pass
    rich_console.print = _new


def _ansi_overlay_loop() -> None:
    """ANSI escape code bottom-bar overlay.

    Uses cursor-save/restore to paint a status bar at the bottom of the
    terminal without interfering with the Rich log stream above it.
    Works even when a Rich Live is already running.
    """
    import sys
    import shutil

    SAVE    = "\033[s"
    RESTORE = "\033[u"
    HIDE    = "\033[?25l"
    SHOW    = "\033[?25h"
    CLEAR   = "\033[2K"
    UP      = "\033[{n}A"
    DOWN    = "\033[{n}B"
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    DIM     = "\033[2m"
    BOLD    = "\033[1m"
    RESET   = "\033[0m"
    BAR_HEIGHT = 3  # lines used by the overlay

    def _paint() -> None:
        with _lock:
            s = _state
        try:
            cols = shutil.get_terminal_size((80, 24)).columns
        except Exception:
            cols = 80

        pct = s.progress
        bar_w = max(10, cols // 4)
        filled = int(pct * bar_w / 100)
        prog = "█" * filled + "░" * (bar_w - filled)

        agents_str = "  ".join(
            f"{_STATUS_ICON.get(a.status,'·')} {a.role[:14]}"
            for a in list(s.agents.values())[:4]
        )
        parallel_str = "  ".join(
            f"{cat}:{n}"
            for cat, n in list(s.tool_parallel.items())[:4]
            if n > 0
        )
        stream_str = ""
        if s.stream:
            last = list(s.stream)[-1]
            stream_str = f"{last.icon} {last.agent[:10]} {last.action[:25]}"

        line1 = (
            f"{CYAN}{BOLD}◈ KRYTH{RESET}  "
            f"{DIM}{s.goal[:30]}{RESET}  "
            f"{CYAN}{prog}{RESET} {pct}%  "
            f"{DIM}[{s.elapsed}]  "
            f"{s.active_count} running  "
            f"{s.done_count}/{s.total_agents} done{RESET}"
        )
        line2 = f"{DIM}{agents_str[:cols-4]}{RESET}"
        line3 = (
            f"{DIM}{stream_str[:cols//2-2]}  "
            f"{parallel_str[:cols//2-2]}{RESET}"
        )

        out = (
            SAVE
            + f"\033[{BAR_HEIGHT}B"      # move down past log area (go to bottom)
            + "\r" + CLEAR + line3
            + "\033[1A\r" + CLEAR + line2
            + "\033[1A\r" + CLEAR + line1
            + RESTORE
        )
        sys.stdout.write(out)
        sys.stdout.flush()

    # Print blank lines to reserve space at bottom
    try:
        sys.stdout.write("\n" * BAR_HEIGHT)
        sys.stdout.flush()
    except Exception:
        pass

    while _running:
        _process_events()
        try:
            _paint()
        except Exception:
            pass
        time.sleep(0.3)

    # Clear overlay on exit
    try:
        import shutil as _sh
        cols = _sh.get_terminal_size((80, 24)).columns
        clear_line = " " * cols
        out = (
            "\033[s"
            + f"\033[{BAR_HEIGHT}B"
            + "\r" + clear_line
            + "\033[1A\r" + clear_line
            + "\033[1A\r" + clear_line
            + "\033[u"
        )
        sys.stdout.write(out)
        sys.stdout.flush()
    except Exception:
        pass


def _fallback_loop() -> None:
    while _running:
        _process_events()
        with _lock:
            s = _state
        try:
            from agent.ui.console import console
            console.print(
                f"  ◈ {s.goal[:40]}  {s.progress}%  "
                f"{s.active_count} running  [{s.elapsed}]",
                style="dim",
            )
        except Exception:
            pass
        time.sleep(5)
