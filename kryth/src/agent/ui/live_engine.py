"""KRYTH UI v5 — Live Layout Engine (feature-flagged, presentation only).

A persistent terminal application built on Rich's ``Live`` + ``Layout``. It
turns KRYTH from an incremental logger into a workstation with a sticky header,
sticky footer, a scrolling execution timeline, a dedicated assistant viewport,
and a separate tool-activity panel.

Architecture
------------
- Pure CONSUMER of the existing event bus. It subscribes to the same
  ``EventKind`` events the incremental renderer uses and never emits, mutates
  runtime state, or touches any backend contract.
- Maintains small per-panel state objects; on each relevant event it updates
  state and asks the Live display to refresh. Rich's Live performs a minimal
  terminal diff — only changed regions are rewritten (no full redraw loop).
- Adaptive: panel sizes derive from the live terminal height/width, so it
  works on narrow and wide terminals and survives resize.

Activation
----------
Enabled only when ``KRYTH_LIVE_UI`` is truthy. Default OFF → the incremental
renderer (agent.ui.renderer) is used, which is the immediate rollback path.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional

from agent.ui.console import console
from agent.ui.events import BUS, Event, EventKind
from agent.ui.theme import (
    ACCENT_PRIMARY, BRAILLE_FRAMES, CHECK, BULLET, BULLET_DIM, gradient_rule,
)


def live_ui_enabled() -> bool:
    try:
        from agent.env import getenv_bool
        return getenv_bool("KRYTH_LIVE_UI")
    except Exception:
        return os.environ.get("KRYTH_LIVE_UI", "").strip().lower() in {"1", "true", "yes", "on"}


def _debug() -> bool:
    return os.environ.get("KRYTH_DEBUG_UI", "").strip().lower() in {"1", "true", "yes", "on"}


# ── Panel state ──────────────────────────────────────────────────────────────

@dataclass
class TimelineItem:
    label: str
    state: str = "active"   # active | done | warn | error
    detail: str = ""


@dataclass
class EngineState:
    # Header
    model: str = ""
    base_url: str = ""
    provider: str = ""
    adapter: str = "Native"
    session_status: str = "Ready"
    # Planner (rendered if populated by future planner logic)
    goal: str = ""
    current: str = ""
    completed: List[str] = field(default_factory=list)
    next_step: str = ""
    # Timeline (scrolling history, capped). Supports thousands of entries —
    # the deque bounds memory while the renderer only paints the visible tail.
    timeline: Deque[TimelineItem] = field(default_factory=lambda: deque(maxlen=2000))
    # Session memory: recent COMPLETED operations that survive across turns so
    # the user never loses context (capped, most-recent-last).
    recent: Deque[str] = field(default_factory=lambda: deque(maxlen=8))
    # Assistant viewport (latest assistant prose)
    assistant: str = ""
    # Tool activity (recent cards)
    tools: Deque[tuple] = field(default_factory=lambda: deque(maxlen=40))  # (action, target, state)
    # Footer metrics
    tools_loaded: int = 0   # registry size (informational)
    dag_nodes: list = field(default_factory=list)   # live DAG (Phase 1)
    agents: dict = field(default_factory=dict)      # name -> {...} agent cards (Phase 6)
    peak_parallel: int = 0                           # max concurrent running (Phase 5)
    work_steals: int = 0                             # work-steal count (Phase 5)
    tool_count: int = 0     # tools CALLED this turn
    tokens: int = 0
    retries: int = 0
    ttft_ms: Optional[int] = None
    started: float = field(default_factory=lambda: 0.0)
    spinner_idx: int = 0
    provider_health_rows: list = field(default_factory=list)   # V1.6 Phase 6


# ── Engine ───────────────────────────────────────────────────────────────────

class LiveEngine:
    """Owns the Rich Live display + a single bus subscription."""

    def __init__(self) -> None:
        self._state = EngineState()
        self._live = None
        self._unsub: Optional[Callable[[], None]] = None
        self._installed = False

    # -- lifecycle ----------------------------------------------------------

    def install(self) -> None:
        if self._installed:
            return
        self._installed = True
        self._state = EngineState()
        self._unsub = BUS.subscribe(self._on_event)

    def uninstall(self) -> None:
        if not self._installed:
            return
        self._stop_live()
        if self._unsub:
            try:
                self._unsub()
            except Exception:
                pass
            self._unsub = None
        self._installed = False

    def _ensure_live(self) -> None:
        if self._live is not None:
            return
        try:
            from rich.live import Live
            self._live = Live(
                self._render(),
                console=console,
                refresh_per_second=12,
                transient=False,
                screen=False,           # don't take over the whole screen (scrollback kept)
                vertical_overflow="visible",
            )
            self._live.start()
        except Exception:
            self._live = None

    def _stop_live(self) -> None:
        if self._live is not None:
            try:
                self._live.update(self._render())
                self._live.stop()
            except Exception:
                pass
            self._live = None

    def ensure_stopped(self) -> None:
        """Public: guarantee the Live display is stopped (called by the REPL
        before rendering the prompt). Safe + idempotent; no-op when inactive."""
        self._stop_live()

    def _refresh(self) -> None:
        if self._live is None:
            self._ensure_live()
        if self._live is not None:
            try:
                self._live.update(self._render())
            except Exception:
                pass

    # -- event handling -----------------------------------------------------

    def _on_event(self, e: Event) -> None:
        try:
            self._handle(e)
        except Exception:
            pass

    def _handle(self, e: Event) -> None:
        s = self._state
        k = e.kind
        if k == EventKind.BANNER:
            s.model = e.data.get("model", "") or ""
            s.base_url = e.data.get("base_url", "") or ""
            s.tools_loaded = e.data.get("skill_count", 0) or 0  # registry size (header)
            s.provider = self._provider_label(s.model, s.base_url)
            s.adapter = self._adapter_label()
            self._ensure_live()
            self._refresh()
        elif k == EventKind.TURN_START:
            s.session_status = "Working"
            s.started = time.monotonic()
            s.assistant = ""
            s.tool_count = 0   # per-turn tools-called counter (footer)
            s.timeline.append(TimelineItem("Understanding request", "active"))
            self._refresh()
        elif k == EventKind.TURN_END:
            self._mark_last_done()
            s.session_status = "Ready"
            s.tokens = e.data.get("tokens_in", 0) + e.data.get("tokens_out", 0)
            self._refresh()
            # Stop Live at turn end so the final frame stays in scrollback and
            # the prompt is clean for the next input.
            self._stop_live()
        elif k in (EventKind.TURN_INTERRUPTED, EventKind.TURN_MAX_TURNS):
            s.session_status = "Stopped"
            self._refresh()
            self._stop_live()
        elif k == EventKind.LLM_WAITING:
            s.spinner_idx = (s.spinner_idx + 1) % len(BRAILLE_FRAMES)
            self._refresh()
        elif k == EventKind.LLM_CONTENT_CHUNK:
            s.assistant += str(e.data.get("piece", ""))
            self._refresh()
        elif k == EventKind.ASSISTANT_MESSAGE:
            s.assistant = str(e.data.get("text", ""))
            self._refresh()
        elif k == EventKind.TOOL_START:
            name = e.data.get("name", "")
            args = e.data.get("args", {}) or {}
            target = self._tool_target(args)
            s.tool_count += 1
            s.tools.append((self._action_label(name), target, "active"))
            s.timeline.append(TimelineItem(self._action_label(name), "active", target))
            self._refresh()
        elif k == EventKind.TOOL_RESULT:
            self._mark_last_tool(error=bool(e.data.get("error")))
            self._mark_last_done(error=bool(e.data.get("error")))
            self._refresh()
        elif k == EventKind.TOOL_FINISH:
            self._mark_last_tool(error=not e.data.get("ok", True))
            self._refresh()
        elif k in (EventKind.WRITE_PREVIEW, EventKind.DIFF):
            path = os.path.basename(e.data.get("path", "") or "")
            verb = "File created" if k == EventKind.WRITE_PREVIEW else "Patch applied"
            s.timeline.append(TimelineItem(verb, "done", path))
            self._refresh()
        elif k == EventKind.SHELL_END:
            ok = e.data.get("exit_code", 0) == 0
            cmd = (e.data.get("command", "") or "").split()[:1]
            s.timeline.append(TimelineItem(
                f"$ {cmd[0] if cmd else 'command'}", "done" if ok else "error"))
            self._refresh()
        elif k == EventKind.TIMELINE_EVENT:
            kind = e.data.get("kind", "info")
            st = {"success": "done", "warn": "warn", "error": "error"}.get(kind, "active")
            s.timeline.append(TimelineItem(str(e.data.get("message", "")), st))
            self._refresh()
        elif k == EventKind.LLM_ERROR:
            s.timeline.append(TimelineItem("Error", "error", str(e.data.get("message", ""))[:60]))
            self._refresh()
        elif k == EventKind.LLM_RETRY:
            s.retries += 1
            self._refresh()
        elif k == EventKind.DAG_UPDATE:
            nodes = e.data.get("nodes")
            if isinstance(nodes, list):
                s.dag_nodes = nodes
            self._refresh()
        elif k in (EventKind.AGENT_UPDATE, EventKind.AGENT_TASK_START, EventKind.AGENT_TASK_DONE):
            name = str(e.data.get("name") or e.data.get("agent_id") or "agent")
            card = s.agents.setdefault(name, {"name": name})
            for key in ("step", "task", "tool", "progress", "status", "runtime", "file"):
                if e.data.get(key) is not None:
                    card[key if key != "task" else "step"] = e.data[key]
            if k == EventKind.AGENT_TASK_DONE:
                card["status"] = "complete"
                card["progress"] = 100
            elif k == EventKind.AGENT_TASK_START:
                card.setdefault("status", "running")
            # Phase 5 — peak parallelism = max simultaneously-running agents.
            running = sum(1 for c in s.agents.values()
                          if str(c.get("status", "")).lower() in ("running", "active"))
            s.peak_parallel = max(s.peak_parallel, running)
            self._refresh()
        elif k == EventKind.WORK_STOLEN:
            s.work_steals += 1
            self._refresh()
        elif k == EventKind.COMPLETE:
            self._mark_last_done()
            self._refresh()

    # -- state helpers ------------------------------------------------------

    def _mark_last_done(self, error: bool = False) -> None:
        for item in reversed(self._state.timeline):
            if item.state == "active":
                item.state = "error" if error else "done"
                if not error:
                    # Remember the completed op for cross-turn session memory.
                    label = item.label + (f"  {item.detail}" if item.detail else "")
                    if not self._state.recent or self._state.recent[-1] != label:
                        self._state.recent.append(label)
                break

    def _mark_last_tool(self, error: bool = False) -> None:
        if self._state.tools:
            action, target, _ = self._state.tools[-1]
            self._state.tools[-1] = (action, target, "error" if error else "done")

    # -- labels -------------------------------------------------------------

    @staticmethod
    def _provider_label(model: str, base_url: str) -> str:
        try:
            from agent.ui.components import _provider_label
            return _provider_label(model, base_url)
        except Exception:
            return "—"

    @staticmethod
    def _adapter_label() -> str:
        try:
            from agent.ui.components import _adapter_label
            return _adapter_label()
        except Exception:
            return "Native"

    @staticmethod
    def _action_label(name: str) -> str:
        try:
            from agent.ui.components import _tool_action
            return _tool_action(name).title()
        except Exception:
            return name.replace("_", " ").title()

    @staticmethod
    def _tool_target(args: dict) -> str:
        for key in ("path", "file", "filename", "command", "query", "pattern", "url"):
            v = args.get(key)
            if v:
                return os.path.basename(str(v)) if key in ("path", "file", "filename") else str(v)[:48]
        return ""

    # -- rendering ----------------------------------------------------------

    @staticmethod
    def layout_mode(width: int) -> str:
        """Responsive breakpoint from terminal width.
        compact <80 · standard 80-119 · wide 120-159 · dashboard 160+."""
        if width < 80:
            return "compact"
        if width < 120:
            return "standard"
        if width < 160:
            return "wide"
        return "dashboard"

    @staticmethod
    def _tool_category(action: str) -> str:
        a = action.lower()
        if any(w in a for w in ("read", "write", "edit", "patch", "diff", "delete", "file", "generat", "modif", "cleanup", "navigation", "analysis")):
            return "File Operations"
        if any(w in a for w in ("test", "build", "execution", "command", "$")):
            return "Build & Test"
        if any(w in a for w in ("review", "verify", "check", "status", "critique")):
            return "Verification"
        if any(w in a for w in ("browser", "verification")):
            return "Browser"
        if any(w in a for w in ("git", "version")):
            return "Version Control"
        return "Other"

    def _render(self):
        from rich.console import Group
        from rich.panel import Panel
        from rich.text import Text
        import rich.box

        s = self._state
        try:
            height = console.size.height
            width = console.size.width
        except Exception:
            height, width = 30, 80
        mode = self.layout_mode(width)
        rule_w = min(width - 2, 120)

        def panel(body, title, border="v3.card.border", pad=(0, 1)):
            return Panel(body, title=Text(title, style="kryth.core"),
                         title_align="left", border_style=border,
                         box=rich.box.ROUNDED, padding=pad)

        # ── Sticky header ────────────────────────────────────────────────
        rule = gradient_rule(rule_w)
        head = Text()
        head.append("  KRYTH", style="v3.brand")
        head.append("   AI Runtime v2", style="v3.subtitle")
        meta = Text()
        meta.append("   Provider ", style="v3.meta.key")
        meta.append(s.provider or "—", style="v3.meta.accent")
        if mode != "compact":          # trim metadata on narrow terminals
            meta.append("    Adapter ", style="v3.meta.key")
            meta.append(s.adapter, style="v3.meta.val")
            meta.append("    Runtime ", style="v3.meta.key")
            meta.append("Event Driven", style="v3.meta.val")
        meta.append("    ", style="")
        meta.append(s.session_status,
                    style="v3.statusbar.accent" if s.session_status == "Working" else "v3.meta.val")
        header = Group(rule, Group(head, meta), rule)

        sections = []

        # ── Planner (standard+; hidden in compact) ───────────────────────
        if mode != "compact" and (s.goal or s.current or s.completed or s.next_step):
            pl = Text()
            if s.goal:
                pl.append("Goal  ", style="v3.meta.key"); pl.append(s.goal + "\n", style="mission.goal")
            if s.current:
                pl.append("Current  ", style="v3.meta.key")
                pl.append(f"{BULLET} {s.current}\n", style="v3.step.active")
            for c in s.completed[-4:]:
                pl.append(f"  {CHECK} ", style="v3.step.done"); pl.append(c + "\n", style="v3.meta.val")
            if s.next_step:
                pl.append("Next  ", style="v3.meta.key"); pl.append(s.next_step, style="v3.meta.val")
            sections.append(panel(pl, "Planner", border="mission.border", pad=(0, 2)))

        # ── Execution DAG (Phase 1) — when nodes are present ─────────────
        if s.dag_nodes:
            dg = Text()
            by_id = {n.get("id"): n for n in s.dag_nodes}
            roots = [n for n in s.dag_nodes if not n.get("deps")] or s.dag_nodes[:1]
            _DAG_GLYPH = {
                "waiting": ("○", "v3.step.pending"), "pending": ("○", "v3.step.pending"),
                "running": ("◐", "v3.step.active"), "active": ("◐", "v3.step.active"),
                "complete": ("●", "v3.step.done"), "done": ("●", "v3.step.done"),
                "failed": ("✗", "term.failed"), "blocked": ("◌", "timeline.warn"),
            }
            seen = set()
            def _dnode(n, ind):
                st = str(n.get("status", "waiting")).lower()
                g, sty = _DAG_GLYPH.get(st, ("○", "v3.step.pending"))
                dg.append("  " * ind); dg.append(f"{g} ", style=sty)
                dg.append(str(n.get("label") or n.get("id") or "?"),
                          style="v3.card.title" if st in ("running", "active") else "v3.meta.val")
                extra = "  ".join(str(n[k]) for k in ("owner", "duration") if n.get(k))
                if extra:
                    dg.append("   " + extra, style="v3.duration")
                dg.append("\n")
            for r in roots:
                _dnode(r, 0); seen.add(r.get("id"))
                for n in s.dag_nodes:
                    if n.get("id") not in seen and r.get("id") in (n.get("deps") or []):
                        _dnode(n, 1); seen.add(n.get("id"))
            for n in s.dag_nodes:
                if n.get("id") not in seen:
                    _dnode(n, 1)
            sections.append(panel(dg, "Execution DAG"))

            # ── Critical Path (Phase 9) — wide+ when the DAG has depth ────
            if mode in ("wide", "dashboard"):
                try:
                    from agent.ui.dag_analysis import critical_path
                    cp = critical_path(s.dag_nodes)
                    if cp.path and len(cp.path) > 1:
                        cpt = Text()
                        for i, lbl in enumerate(cp.labels):
                            neck = cp.path[i] == cp.bottleneck_id
                            cpt.append(f"  {lbl}", style="v3.meta.accent" if neck else "v3.meta.val")
                            if neck:
                                cpt.append("  ◀ bottleneck", style="timeline.warn")
                            cpt.append("\n")
                        cpt.append(f"  Impact {cp.impact.upper()}   "
                                   f"Blocked {cp.blocked_workers}   "
                                   f"Path {cp.length_s:.0f}s", style="v3.duration")
                        sections.append(panel(cpt, "Critical Path"))
                except Exception:
                    pass

        # ── Agent Timeline (Phase 6) — per-agent activity bars (wide+) ────
        if s.agents and mode in ("wide", "dashboard"):
            at = Text()
            for a in list(s.agents.values())[:12]:
                nm = str(a.get("name", "agent"))
                prog = max(0, min(100, int(a.get("progress", 0) or 0)))
                st = str(a.get("status", "")).lower()
                fill = round(14 * prog / 100)
                bs = ("v3.step.done" if st in ("complete", "done")
                      else "term.failed" if st in ("failed", "error") else "v3.step.active")
                at.append(f"  {nm:<14}", style="agent.name")
                step = str(a.get("step") or a.get("tool") or a.get("file") or "")[:16]
                at.append(f"{step:<16}", style="v3.meta.val")
                at.append("█" * fill, style=bs)
                at.append("░" * (14 - fill), style="v3.step.pending")
                at.append(f"  {prog}%", style="v3.duration")
                at.append("\n")
            sections.append(panel(at, "Agent Timeline"))

            # ── Parallel efficiency (Phase 5) — one-line summary ──────────
            run = sum(1 for c in s.agents.values()
                      if str(c.get("status", "")).lower() in ("running", "active"))
            total = len(s.agents)
            idle = total - run
            eff = round(100 * run / total) if total else 0
            pe = Text("  ")
            for lbl, val in (("Workers", total), ("Running", run), ("Idle", idle),
                             ("Peak", s.peak_parallel), ("Steals", s.work_steals)):
                pe.append(f"{lbl} ", style="v3.statusbar")
                pe.append(f"{val}   ", style="v3.statusbar.accent")
            pe.append("Efficiency ", style="v3.statusbar")
            pe.append(f"{eff}%", style="v3.meta.accent" if eff >= 60 else "timeline.warn")
            sections.append(panel(pe, "Parallel Efficiency"))

        # ── Session memory: recent completed ops (wide+) ─────────────────
        if mode in ("wide", "dashboard") and s.recent:
            rc = Text()
            for label in list(s.recent)[-5:]:
                rc.append(f"  {CHECK} ", style="v3.step.done")
                rc.append(label, style="v3.meta.val")
                rc.append("\n")
            sections.append(panel(rc, "Recent"))

        # ── Live Timeline (height-bounded tail) ──────────────────────────
        tl_rows = max(4, min(14, height - 16))
        tl = Text()
        items = list(s.timeline)[-tl_rows:]
        if not items:
            tl.append("  (idle)", style="v3.step.pending")
        for it in items:
            glyph, style = self._glyph(it.state)
            tl.append(f"  {glyph} ", style=style)
            tl.append(it.label, style="timeline.info" if it.state != "active" else "v3.step.active")
            if it.detail:
                tl.append(f"   {it.detail}", style="v3.duration")
            tl.append("\n")
        sections.append(panel(tl, "Live Timeline"))

        # ── Assistant viewport (always) ──────────────────────────────────
        atext = s.assistant.strip()
        if atext:
            av = Text()
            av.append("KRYTH\n", style="role.assistant")
            av.append(atext, style="v3.meta.val")
            sections.append(panel(av, "Assistant", pad=(0, 2)))

        # ── Tool activity — grouped (wide+; hidden in compact/standard) ──
        if mode in ("wide", "dashboard") and s.tools:
            groups: dict = {}
            for action, target, st in list(s.tools)[-12:]:
                groups.setdefault(self._tool_category(action), []).append((action, target, st))
            tv = Text()
            for cat, rows in groups.items():
                tv.append(f"{cat}\n", style="v3.meta.key")
                # Collapse repeated identical actions into a count.
                seen: dict = {}
                order: list = []
                for action, target, st in rows:
                    key = (action, target)
                    if key not in seen:
                        order.append(key); seen[key] = [st, 1]
                    else:
                        seen[key][0] = st; seen[key][1] += 1
                for key in order:
                    action, target = key
                    st, count = seen[key]
                    glyph, style = self._glyph(st)
                    tv.append(f"  {glyph} ", style=style)
                    tv.append(action, style="v3.card.title")
                    if target:
                        tv.append(f"   {target}", style="v3.card.path")
                    if count > 1:
                        tv.append(f"   ×{count}", style="v3.duration")
                    tv.append("\n")
            sections.append(panel(tv, "Tool Activity"))

        # ── Sticky footer ────────────────────────────────────────────────
        elapsed = (time.monotonic() - s.started) if s.started else 0.0
        ft = Text()
        ft.append("  Tools ", style="v3.statusbar"); ft.append(str(s.tool_count), style="v3.statusbar.accent")
        ft.append("   ·   Tokens ", style="v3.duration"); ft.append(f"{s.tokens:,}", style="v3.statusbar.accent")
        if mode != "compact":
            ft.append("   ·   Telemetry ", style="v3.duration")
            ft.append("ON" if os.environ.get("KRYTH_RUNTIME_TELEMETRY") else "off", style="v3.statusbar.accent")
            ft.append("   ·   TTFT ", style="v3.duration")
            ft.append(f"{s.ttft_ms}ms" if s.ttft_ms else "—", style="v3.statusbar.accent")
            ft.append("   ·   Retries ", style="v3.duration"); ft.append(str(s.retries), style="v3.statusbar.accent")
        ft.append("   ·   Elapsed ", style="v3.duration"); ft.append(f"{elapsed:.1f}s", style="v3.statusbar.accent")
        footer = Group(gradient_rule(rule_w), ft)

        body = Group(*sections) if sections else Text("")
        return Group(header, Text(""), body, Text(""), footer)

    @staticmethod
    def _glyph(state: str):
        if state == "done":
            return CHECK, "v3.step.done"
        if state == "error":
            return "✗", "term.failed"
        if state == "warn":
            return "▲", "timeline.warn"
        return BULLET, "v3.step.active"


# Module-level singleton + install/uninstall surface mirroring renderer.py.
_engine = LiveEngine()


def install() -> None:
    _engine.install()


def uninstall() -> None:
    _engine.uninstall()


def get_engine() -> LiveEngine:
    return _engine
