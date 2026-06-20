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
    current: str = ""         # what this agent is doing right now (humanized)
    history: deque = field(default_factory=lambda: deque(maxlen=4))  # recent completed steps

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

    # Provider health snapshot (updated from push_event)
    provider_health_rows: List[dict] = field(default_factory=list)

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
_app_thread: Optional[threading.Thread] = None
_running = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_dashboard(goal: str = "", total_agents: int = 0, total_layers: int = 0) -> None:
    """Initialise dashboard state and spawn the background display thread."""
    global _running, _state, _app_instance, _app_thread
    if _running:
        return  # already running
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
    _app_thread = threading.Thread(target=_run_app, daemon=True, name="kryth-dashboard")
    _app_thread.start()


def stop_dashboard() -> None:
    global _running
    _running = False


def push_event(kind: str, **data) -> None:
    if _running:
        _event_queue.put({"kind": kind, **data})


def get_active() -> bool:
    return _running


def push_provider_health() -> None:
    """Snapshot current provider health metrics and push to all dashboards.

    Only pushes when a dashboard is running (DAG/SWARM modes only).
    Safe to call from worker threads — uses the existing thread-safe queue.
    """
    try:
        from agent.production.reliability import _provider_health
        if _provider_health is None:
            return
        metrics = _provider_health.all_providers()
        if not metrics:
            return
        rows = []
        for prov, m in metrics.items():
            if m.total_requests < 1:
                continue
            if m.total_requests < 10:
                status = "healthy"
            elif m.success_rate >= 0.95:
                status = "healthy"
            elif m.success_rate >= 0.8:
                status = "degraded"
            else:
                status = "unhealthy"
            rows.append({
                "provider": prov,
                "status": status,
                "timeouts": m.provider_errors,
                "retries": m.failures - m.provider_errors,
                "success_rate": m.success_rate * 100,
            })
        if not rows:
            return
        # Push to Rich dashboard (DAG/SWARM)
        if _running:
            push_event("provider_health", rows=rows)
        # Push to live_engine EngineState for textual/live UI
        try:
            import sys as _sys
            _le = _sys.modules.get("agent.ui.live_engine")
            if _le is not None:
                _eng = getattr(_le, "_active_engine", None)
                if _eng is not None:
                    _eng._state.provider_health_rows = rows
        except Exception:
            pass
    except Exception:
        pass


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


def _humanize_action(tool: str, detail: str = "") -> str:
    """Turn a raw tool call into a short human phrase for the agent box — no
    raw command/arg spam. e.g. write_file hero.tsx → 'Creating hero.tsx'."""
    import os as _os
    verb = {
        "write_file": "Creating", "create_file": "Creating", "edit_file": "Editing",
        "str_replace": "Editing", "apply_patch": "Editing", "read_file": "Reading",
        "list_files": "Scanning project", "run_command": "Running", "bash": "Running",
        "search": "Searching", "grep": "Searching", "todo_write": "Planning",
    }.get(tool, tool.replace("_", " ").title())
    target = (detail or "").strip()
    # keep just a filename / short token, never a full command line
    if target:
        target = target.split("\n")[0][:32]
        base = _os.path.basename(target.split()[0]) if target.split() else target
        if tool in ("list_files", "todo_write"):
            return verb
        return f"{verb} {base}".strip()
    return verb


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
            # Per-agent activity: roll the previous "current" into history, set
            # the new humanized current action. This drives the separate boxes.
            node = s.agents.get(agent) or next(
                (a for a in s.agents.values() if a.role == agent), None)
            if node is not None:
                phrase = _humanize_action(tool, detail or action)
                if node.current and node.current != phrase:
                    node.history.append(node.current)
                node.current = phrase

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

        elif kind == "provider_health":
            # Provider health snapshot pushed by scheduler after each agent run.
            # rows = list of dicts: {provider, status, timeouts, retries, success_rate}
            rows = ev.get("rows", [])
            if rows:
                s.provider_health_rows = rows[-10:]  # keep last 10 providers

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

    The caller (scheduler) stops the spinner on the main thread before
    spawning this thread. This thread then starts its own Rich Live and
    updates the panel at 4 FPS until stop_dashboard() is called.
    """
    _rich_dashboard_loop()


def _rich_dashboard_loop() -> None:
    """Render the dashboard by updating the existing Rich spinner 4×/sec."""
    from rich.text import Text
    from rich.panel import Panel
    from rich.console import Group
    from rich.rule import Rule

    from rich.table import Table

    def _build_renderable():
        with _lock:
            s = _state

        # Header: goal + progress bar + layer summary
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
        head_panel = Panel(
            Group(header, bar, Text(
                f"  {s.active_count} running  ·  {s.done_count}/{s.total_agents} done",
                style="dim")),
            border_style="cyan", padding=(0, 1))

        # One SEPARATE box per agent — current action + recent done bullets.
        # No raw read/write/tool logs; each box is the agent's status card.
        _SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        s._tick = (getattr(s, "_tick", 0) + 1) % len(_SPIN)
        spin = _SPIN[s._tick]
        agents = list(s.agents.values())
        boxes = []
        for a in agents:
            icon = _STATUS_ICON.get(a.status, "·")
            style = _STATUS_STYLE.get(a.status, "white")
            running = a.status == "running"
            body = Text()
            cur = a.current or (a.task[:30] if a.task else
                                ("done" if a.status == "done" else "waiting…"))
            # Animated spinner on the live action line; static marker otherwise.
            lead = (spin if running else ("✓" if a.status == "done" else "▸"))
            body.append(f"{lead} ", style=("bold cyan" if running else style))
            body.append(cur[:34] + "\n", style="bold white" if running else "white")
            for past in list(a.history)[-3:]:
                body.append("  ✓ ", style="green")
                body.append(past[:32] + "\n", style="dim")
            title = Text()
            title.append(f"{icon} ", style=style)
            title.append(a.role[:22], style=f"bold {style}")
            boxes.append(Panel(body, title=title, title_align="left",
                               border_style=style, padding=(0, 1)))

        sections = [head_panel]
        if boxes:
            grid = Table.grid(padding=(0, 1), expand=True)
            cols = 1 if len(boxes) <= 2 else 2 if len(boxes) <= 8 else 3
            for _ in range(cols):
                grid.add_column(ratio=1)
            for i in range(0, len(boxes), cols):
                grid.add_row(*boxes[i:i + cols])
            sections.append(grid)

        # Provider health panel — only shown when health data is available
        if s.provider_health_rows:
            from rich.table import Table as _Tbl
            ph_tbl = _Tbl.grid(padding=(0, 2), expand=False)
            for _ in range(5):
                ph_tbl.add_column(no_wrap=True)
            ph_header = Text()
            ph_header.append("  Provider  ", style="bold dim")
            ph_header.append("Status      ", style="bold dim")
            ph_header.append("Timeouts  ", style="bold dim")
            ph_header.append("Retries   ", style="bold dim")
            ph_header.append("Success%", style="bold dim")
            _status_style_map = {
                "healthy": "bold green", "degraded": "bold yellow",
                "unhealthy": "bold red",
            }
            for row in s.provider_health_rows:
                _st = row.get("status", "healthy")
                _sty = _status_style_map.get(_st, "white")
                ph_tbl.add_row(
                    Text(str(row.get("provider", ""))[:22], style="dim white"),
                    Text(_st.upper(), style=_sty),
                    Text(str(row.get("timeouts", 0)), style="dim"),
                    Text(str(row.get("retries", 0)), style="dim"),
                    Text(f"{row.get('success_rate', 100):.0f}%", style=_sty),
                )
            sections.append(Panel(
                Group(ph_header, ph_tbl),
                title=Text("◈  Provider Health", style="bold cyan"),
                border_style="dim cyan", padding=(0, 1),
            ))

        # Intel (last 2) — mission-level notes only, no tool logs
        intel = list(s.intel)[-2:]
        if intel:
            note = Text()
            for msg in intel:
                note.append(f"  {msg[:60]}\n", style="dim")
            sections.append(note)

        return Group(*sections)

    from agent.ui.console import console as rich_console
    from rich.live import Live

    try:
        with Live(
            _build_renderable(),
            console=rich_console,
            refresh_per_second=8,
            transient=True,
            # CROP (not "visible"): keep the dashboard in a fixed region and
            # redraw IN PLACE. "visible" re-prints/scrolls when the panel grid is
            # taller than the terminal, which stacks duplicate headers.
            vertical_overflow="crop",
        ) as live:
            # Rich Live automatically routes console.print() calls above the
            # panel when a Live is active on that console — no redirect needed.
            try:
                while _running:
                    _process_events()
                    live.update(_build_renderable())
                    time.sleep(0.05)
            finally:
                pass
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
            time.sleep(1)

