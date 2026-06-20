"""Reusable KRYTH Rich components."""

from __future__ import annotations

import os
from typing import Iterable

import rich.box
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.hud import startup_reveal
from agent.ui.motion import gradient_text, status_chip
from agent.ui.panels import _print_panel
from agent.ui.theme import CORE, DIVIDER, DOT, ERROR, SEP, TEE, WAITING


def section_header(title: str, style: str = "section.exec") -> None:
    console.print()
    console.print(Rule(title=f"[{style}]{CORE} {title.upper()}[/{style}]", characters=DIVIDER, style="divider", align="left"))


def _provider_label(model: str, base_url: str) -> str:
    """Human provider name derived from model / endpoint."""
    try:
        from agent.model.provider_registry import detect_provider
        p = detect_provider(model or "", base_url or "")
        name = getattr(p, "value", str(p)) or "unknown"
    except Exception:
        name = "unknown"
    pretty = {
        "openai": "OpenAI", "anthropic": "Anthropic", "deepseek": "DeepSeek",
        "qwen": "Qwen", "nvidia": "NVIDIA", "gemini": "Gemini", "groq": "Groq",
        "mistral": "Mistral", "llama": "Llama", "glm": "GLM", "grok": "Grok",
        "kimi": "Kimi", "stepfun": "StepFun", "ollama": "Ollama",
        "openrouter": "OpenRouter",
    }
    return pretty.get(name, name.title())


def _adapter_label() -> str:
    """Adapter delivery mode label for the header/status bar."""
    try:
        from agent.llm import _tool_mode, _adapter_stream_enabled
        native = _tool_mode() != "text"
        seam = _adapter_stream_enabled()
        base = "Native" if native else "Text"
        return f"{base} (adapter seam)" if seam else base
    except Exception:
        return "Native"


def _no_api_key() -> bool:
    """True only when NO provider key is available (env or stored config)."""
    env_keys = (
        "OPENAI_API_KEY", "NVIDIA_API_KEY", "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY", "GROQ_API_KEY", "KRYTH_API_KEY",
    )
    for k in env_keys:
        v = os.environ.get(k, "").strip()
        if v and v not in ("not-set", "not-configured"):
            return False
    try:
        from kryth.config import _load
        cfg = _load()
        for k, v in (cfg or {}).items():
            if k.endswith("api_key") and str(v).strip():
                return False
    except Exception:
        pass
    return True


def banner(model: str, base_url: str, skill_count: int, version: str = "1.0") -> None:
    """Premium KRYTH header — UI v3.

    A sharp, minimal top section: signature gradient rule, the KRYTH wordmark,
    a runtime subtitle, and Provider / Adapter / Runtime metadata. No box,
    generous whitespace, signature #DFFF00 identity.
    """
    from agent.ui.theme import gradient_rule, ACCENT_PRIMARY

    try:
        from kryth import __version__ as _xver
    except ImportError:
        _xver = version

    provider = _provider_label(model, base_url)
    adapter = _adapter_label()

    rule = gradient_rule()
    console.print()
    console.print(rule)
    console.print()
    # Wordmark + subtitle
    console.print(Text.assemble(
        ("  KRYTH", "v3.brand"),
        ("   AI Runtime v2", "v3.subtitle"),
        (f"   ·   v{_xver}", "v3.subtitle"),
    ))
    console.print()
    # Metadata rows — aligned key/value, muted keys, accent/secondary values.
    meta = Table.grid(padding=(0, 2))
    meta.add_column(justify="left", no_wrap=True, style="v3.meta.key", min_width=10)
    meta.add_column(justify="left", no_wrap=True)
    meta.add_row(Text("  Provider", style="v3.meta.key"), Text(provider, style="v3.meta.accent"))
    meta.add_row(Text("  Model",    style="v3.meta.key"), Text(model or "—", style="v3.meta.val"))
    meta.add_row(Text("  Adapter",  style="v3.meta.key"), Text(adapter, style="v3.meta.val"))
    meta.add_row(Text("  Runtime",  style="v3.meta.key"), Text("Event Driven", style="v3.meta.val"))
    if skill_count:
        meta.add_row(Text("  Tools",  style="v3.meta.key"), Text(f"{skill_count} loaded", style="v3.meta.val"))
    console.print(meta)
    console.print()
    console.print(rule)
    console.print()

    if _no_api_key():
        console.print(Text.assemble(
            ("  ", ""), ("▲", "log.warn"),
            ("  No API key detected — set a provider key to begin.", "muted"),
        ))
        console.print()


def _tool_action(name: str) -> str:
    """Map raw tool names to human-readable engineering action labels."""
    try:
        from agent.ui.engineering import tool_to_eng_label
        return tool_to_eng_label(name).upper()
    except Exception:
        pass
    n = name.lower()
    if n in {"read_file", "list_files"}:
        return "REPOSITORY ANALYSIS"
    if n in {"grep", "glob", "search_code", "semantic_search",
             "lookup_symbol", "lookup_imports", "lookup_dependents",
             "fts_search", "ast_search", "search_smart"}:
        return "CODE NAVIGATION"
    if n in {"write_file"}:
        return "CODE GENERATION"
    if n in {"edit_file", "multi_edit"}:
        return "CODE MODIFICATION"
    if n in {"run_command", "run_tests", "run_install",
             "shell_exec", "shell_run_plan"}:
        return "BUILD EXECUTION"
    if n in {"delete_file"}:
        return "CLEANUP"
    if n in {"todo_write", "todo_read"}:
        return "TASK PLANNING"
    if n in {"spawn_agent", "spawn_agents_parallel"}:
        return "TEAM DEPLOYMENT"
    if n.startswith("browser_") or n in {"open_url", "browser_use_task"}:
        return "BROWSER VERIFICATION"
    if n in {"git_op"}:
        return "VERSION CONTROL"
    if n in {"self_critique", "verify_files"}:
        return "CODE REVIEW"
    return name.replace("_", " ").title()


def tool_header(name: str, summary: str) -> None:
    """Tool header — only shown in debug mode (KRYTH_DEBUG_UI=1).

    In normal mode all tool visibility is handled by mission_console
    which routes through emit_timeline. This function is intentionally
    suppressed in normal mode so the terminal stays clean.
    """
    import os
    if not os.environ.get("KRYTH_DEBUG_UI", "").lower() in {"1", "true", "yes"}:
        return
    action = _tool_action(name)
    console.print()
    if summary:
        console.print(Text.assemble(
            (CORE, "tool.bullet"),
            (" " + action, "tool.name"),
            ("  " + DOT + "  ", "muted"),
            (summary, "tool.arg"),
        ))
    else:
        console.print(Text.assemble(
            (CORE, "tool.bullet"),
            (" " + action, "tool.name"),
        ))


def tool_result(first_line: str, extra_lines: int, *, error: bool = False) -> None:
    style = "tool.error" if error else "tool.result"
    suffix = f" [muted](+{extra_lines} more)[/muted]" if extra_lines else ""
    console.print(f"  [tool.tee]{TEE}[/tool.tee] [{style}]{first_line}[/{style}]{suffix}")


def tool_error_line(msg: str) -> None:
    console.print(f"  [log.error]{ERROR}[/log.error] [log.error]{msg}[/log.error]")


def tool_status_line(msg: str, style: str = "muted") -> None:
    console.print(f"  [tool.tee]{TEE}[/tool.tee] [{style}]{msg}[/{style}]")


def status_line(parts: Iterable[str]) -> None:
    parts = list(parts)
    if parts:
        console.print(Text.assemble((CORE, "kryth.core"), " ", (SEP.join(parts), "status.label")))


def turn_complete(elapsed: float | None, tokens_in: int, tokens_out: int, tool_calls: int = 0) -> None:
    """Clean mission-complete panel. Tokens only shown in debug mode."""
    import os
    _debug = os.environ.get("KRYTH_DEBUG_UI", "").lower() in {"1", "true", "yes"}

    elapsed_str = f"{elapsed:.1f}s" if elapsed else "?"

    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=14)
    body.add_column(no_wrap=True)

    body.add_row(
        Text.assemble((CORE, "log.success"), (" Outcome", "muted")),
        Text("Complete", style="log.success"),
    )
    body.add_row(
        Text.assemble((CORE, "kryth.core"), (" Duration", "muted")),
        Text(elapsed_str, style="title"),
    )

    if _debug:
        total = tokens_in + tokens_out
        body.add_row(
            Text.assemble((CORE, "kryth.core"), (" Tokens", "muted")),
            Text(f"{total:,}  (in {tokens_in:,} / out {tokens_out:,})", style="muted"),
        )
        if tool_calls:
            body.add_row(
                Text.assemble((CORE, "kryth.core"), (" Actions", "muted")),
                Text(str(tool_calls), style="muted"),
            )

    console.print()  # keep one blank line above the turn-complete panel
    _print_panel(Panel(
        body,
        title=Text.assemble((CORE, "log.success"), ("  Mission Complete", "log.success")),
        title_align="left",
        border_style="log.success",
        padding=(1, 2),
        expand=False,
        box=rich.box.ROUNDED,
    ))


def status_bar(*, provider: str = "", tools: int = 0, tokens: int = 0,
               adapter: str = "", telemetry_on: bool = False) -> None:
    """Persistent-style status line (UI v3). Muted, single line, signature dot
    separators. Printed at turn end so the user always sees runtime context."""
    segs = []
    if provider:
        segs.append(("Provider ", provider))
    segs.append(("Tools ", str(tools)))
    segs.append(("Tokens ", f"{tokens:,}"))
    if adapter:
        segs.append(("Adapter ", adapter))
    segs.append(("Telemetry ", "ON" if telemetry_on else "off"))

    t = Text()
    t.append("  ")
    for i, (label, val) in enumerate(segs):
        if i:
            t.append("   ·   ", style="v3.duration")
        t.append(label, style="v3.statusbar")
        t.append(val, style="v3.statusbar.accent")
    console.print(t)


def debug_panel(*, model: str = "", base_url: str = "", tools: int = 0) -> None:
    """Debug-mode runtime panel (UI v3). Surfaces runtime + telemetry internals.
    NEVER shown in normal mode."""
    if os.environ.get("KRYTH_DEBUG_UI", "").lower() not in {"1", "true", "yes"}:
        return
    try:
        from agent.model import telemetry as _tel
        snap = _tel.snapshot()
    except Exception:
        snap = {"counters": {}, "ttft": {}, "event_orders": []}
    counters = snap.get("counters", {})
    try:
        from kryth import __version__ as _ver
    except Exception:
        _ver = "?"

    ttft = snap.get("ttft", {})
    ttft_str = "—"
    if ttft:
        first = next(iter(ttft.values()))
        ttft_str = f"{first.get('avg', '?')}s avg ({first.get('n', 0)} samples)"

    rows = [
        ("Runtime", f"v{_ver}  ·  Event Driven"),
        ("Provider", _provider_label(model, base_url)),
        ("Adapter", _adapter_label()),
        ("Tool Count", str(tools)),
        ("Adapter used", str(counters.get("adapter_used", 0))),
        ("Legacy used", str(counters.get("legacy_used", 0))),
        ("Fallback used", str(counters.get("fallback_activated", 0))),
        ("Retries", str(counters.get("retry", 0))),
        ("Parser errors", str(counters.get("parser_error", 0))),
        ("Tool normalizations", str(counters.get("tool_normalization", 0))),
        ("Harmony sanitized", str(counters.get("harmony_sanitization", 0))),
        ("Stream completions", str(counters.get("stream_completion", 0))),
        ("TTFT", ttft_str),
        ("Telemetry", "ON" if os.environ.get("KRYTH_RUNTIME_TELEMETRY") else "counters-only"),
    ]
    body = Table.grid(padding=(0, 3), expand=False)
    body.add_column(no_wrap=True, style="v3.meta.key", min_width=20)
    body.add_column(no_wrap=True, style="v3.meta.val")
    for k, v in rows:
        body.add_row(k, v)
    console.print()
    _print_panel(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), ("  Runtime Debug", "kryth.core")),
        title_align="left",
        border_style="hud.border.dim",
        padding=(1, 2),
        expand=False,
        box=rich.box.ROUNDED,
    ))


def planner_panel(goal: str = "", current: str = "",
                  completed: list[str] | None = None,
                  next_step: str = "") -> None:
    """Premium planner-state panel (UI v4).

    Renders Goal / Current / Completed / Next from a planner state object. This
    renderer is intentionally independent of any planner *logic* — it draws
    whatever state it is given, so the planner backend can be wired later
    without touching the UI. No-ops when there is nothing meaningful to show.
    """
    completed = completed or []
    if not (goal or current or completed or next_step):
        return

    from agent.ui.theme import CHECK, BULLET, ARROW

    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, style="v3.meta.key", min_width=10, justify="left")
    body.add_column(overflow="fold")

    if goal:
        body.add_row(Text("Goal", style="v3.meta.key"),
                     Text(goal, style="mission.goal"))
    if current:
        body.add_row(
            Text("Current", style="v3.meta.key"),
            Text.assemble((BULLET + " ", "v3.step.active"), (current, "v3.card.title")),
        )
    if completed:
        done = Text()
        for i, step in enumerate(completed):
            if i:
                done.append("\n")
            done.append(f"{CHECK} ", style="v3.step.done")
            done.append(step, style="v3.meta.val")
        body.add_row(Text("Completed", style="v3.meta.key"), done)
    if next_step:
        body.add_row(
            Text("Next", style="v3.meta.key"),
            Text.assemble((ARROW + " ", "v3.step.pending"), (next_step, "v3.meta.val")),
        )

    console.print()
    _print_panel(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), ("  Planner", "kryth.core")),
        title_align="left",
        border_style="mission.border",
        padding=(1, 2),
        expand=False,
        box=rich.box.ROUNDED,
    ))


# ── DAG visualization (presentation only — no scheduler change) ──────────────

# Node state → (glyph, style). Matches the spec's circle legend.
_DAG_STATE = {
    "waiting":  ("○", "v3.step.pending"),
    "pending":  ("○", "v3.step.pending"),
    "running":  ("◐", "v3.step.active"),
    "active":   ("◐", "v3.step.active"),
    "complete": ("●", "v3.step.done"),
    "done":     ("●", "v3.step.done"),
    "failed":   ("✗", "term.failed"),
    "blocked":  ("◌", "log.warn"),
}


def dag_reasoning_panel(est) -> None:
    """Phases 7 & 8 — show WHY DAG (or DIRECT/SWARM) was chosen.

    `est` is a MissionEstimate (or its .to_dict()). Renders the sequential vs
    parallel estimate, expected speedup, independent units, and the decision —
    so users understand the routing instead of wondering if DAG was forgotten.
    """
    d = est.to_dict() if hasattr(est, "to_dict") else dict(est or {})
    rec = str(d.get("recommendation", "direct")).upper()
    chosen_parallel = rec in ("DAG", "SWARM")

    body = Table.grid(padding=(0, 3), expand=False)
    body.add_column(no_wrap=True, style="v3.meta.key", min_width=20)
    body.add_column(no_wrap=True, style="v3.meta.val")
    if d.get("complexity_score") is not None:
        body.add_row("Complexity score", f"{d.get('complexity_score', 0)}/100")
    if d.get("parallel_density"):
        body.add_row(Text("Parallel density", style="v3.meta.key"),
                     Text(str(d["parallel_density"]).upper(), style="v3.meta.accent"))
    if d.get("decomposition_potential"):
        body.add_row("Decomposition", str(d["decomposition_potential"]).upper())
    body.add_row("Independent streams", str(d.get("independent_units", 1)))
    if d.get("components"):
        body.add_row("Components", ", ".join(d["components"]))
    if d.get("sections"):
        body.add_row("Sections", ", ".join(d["sections"]))
    body.add_row("Files (est)", str(d.get("files", 0)))
    if chosen_parallel:
        body.add_row("Agents", str(d.get("agents", 1)))
    if d.get("coordination_cost_s"):
        body.add_row("Coordination cost", f"{d.get('coordination_cost_s', 0):.0f}s")
    body.add_row("Sequential estimate", f"{d.get('seq_time_s', 0):.0f}s")
    body.add_row("Parallel estimate", f"{d.get('dag_time_s', 0):.0f}s")
    body.add_row(Text("Expected speedup", style="v3.meta.key"),
                 Text(f"{d.get('speedup', 1.0):.2f}x", style="v3.meta.accent"))
    body.add_row(Text("Decision", style="v3.meta.key"),
                 Text(rec, style="mission.goal" if chosen_parallel else "v3.meta.val"))
    if d.get("reason"):
        body.add_row("Reason", d["reason"])

    title = "Why DAG" if chosen_parallel else "Why Direct"
    console.print()
    _print_panel(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), (f"  {title}", "kryth.core")),
        title_align="left",
        border_style="mission.border" if chosen_parallel else "hud.border.dim",
        padding=(1, 2),
        expand=False,
        box=rich.box.ROUNDED,
    ))


def dag_tree_panel(nodes: list) -> None:
    """Phase 1 — live DAG tree. `nodes`: [{id, label, status, deps, owner,
    duration}]. Renders state glyph + label + owner/duration/deps. No-op when
    empty. Pure presentation — never touches scheduler state."""
    nodes = list(nodes or [])
    if not nodes:
        return
    body = Text()
    by_id = {n.get("id"): n for n in nodes}
    # Roots = nodes with no deps; render children indented one level (a full
    # arbitrary-depth tree is overkill for a terminal panel).
    def _line(n, indent):
        st = str(n.get("status", "waiting")).lower()
        glyph, style = _DAG_STATE.get(st, ("○", "v3.step.pending"))
        body.append("  " * indent)
        body.append(f"{glyph} ", style=style)
        body.append(str(n.get("label") or n.get("id") or "?"),
                    style="v3.card.title" if st in ("running", "active") else "v3.meta.val")
        meta = []
        if n.get("owner"):
            meta.append(str(n["owner"]))
        if n.get("duration"):
            meta.append(str(n["duration"]))
        if meta:
            body.append("   " + "  ".join(meta), style="v3.duration")
        body.append("\n")

    roots = [n for n in nodes if not n.get("deps")]
    if not roots:
        roots = nodes[:1]
    seen = set()
    for r in roots:
        _line(r, 0); seen.add(r.get("id"))
        for n in nodes:
            if n.get("id") not in seen and r.get("id") in (n.get("deps") or []):
                _line(n, 1); seen.add(n.get("id"))
    for n in nodes:  # any unrendered (multi-dep) nodes
        if n.get("id") not in seen:
            _line(n, 1)

    console.print()
    _print_panel(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), ("  Execution DAG", "kryth.core")),
        title_align="left", border_style="v3.card.border",
        padding=(0, 2), expand=False, box=rich.box.ROUNDED,
    ))


def parallel_panel(*, workers: int = 0, running: int = 0, idle: int = 0,
                   peak: int = 0, queue_depth: int = 0, work_steals: int = 0) -> None:
    """Phase 5 — parallel efficiency. Efficiency = running / workers."""
    eff = round(100 * running / workers) if workers else 0
    t = Text("  ")
    for label, val in (("Workers", workers), ("Running", running), ("Idle", idle),
                       ("Peak", peak), ("Queue", queue_depth), ("Steals", work_steals)):
        t.append(f"{label} ", style="v3.statusbar")
        t.append(f"{val}   ", style="v3.statusbar.accent")
    t.append("Efficiency ", style="v3.statusbar")
    t.append(f"{eff}%", style="v3.meta.accent" if eff >= 60 else "log.warn")
    console.print(t)


def ownership_panel(owners: dict) -> None:
    """Phase 3 — file → owning agent map. `owners`: {path: agent}. No-op empty."""
    owners = dict(owners or {})
    if not owners:
        return
    body = Table.grid(padding=(0, 3), expand=False)
    body.add_column(no_wrap=True, style="v3.card.path", min_width=20)
    body.add_column(no_wrap=True, style="v3.meta.val")
    for path, agent in list(owners.items())[:20]:
        body.add_row(str(path), Text.assemble(("→ ", "v3.duration"), (str(agent), "agent.name")))
    console.print()
    _print_panel(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), ("  Ownership", "kryth.core")),
        title_align="left", border_style="v3.card.border",
        padding=(0, 2), expand=False, box=rich.box.ROUNDED,
    ))


_IMPACT_STYLE = {"high": "term.failed", "medium": "log.warn", "low": "v3.step.done"}


def critical_path_panel(nodes: list) -> None:
    """Phase 9 — critical path + bottleneck. Computes from the DAG snapshot via
    `dag_analysis.critical_path`; no scheduler coupling. No-op when empty."""
    nodes = list(nodes or [])
    if not nodes:
        return
    from agent.ui.dag_analysis import critical_path
    cp = critical_path(nodes)
    if not cp.path:
        return
    body = Text()
    # The chain, top to bottom with ↓ connectors.
    for i, label in enumerate(cp.labels):
        body.append("  ")
        is_neck = (cp.path[i] == cp.bottleneck_id)
        body.append(label, style="v3.meta.accent" if is_neck else "v3.meta.val")
        if is_neck:
            body.append("   ◀ bottleneck", style="log.warn")
        body.append("\n")
        if i < len(cp.labels) - 1:
            body.append("  ↓\n", style="v3.duration")
    body.append("\n")
    meta = Table.grid(padding=(0, 3), expand=False)
    meta.add_column(style="v3.meta.key", no_wrap=True, min_width=16)
    meta.add_column(style="v3.meta.val", no_wrap=True)
    if cp.bottleneck_label:
        meta.add_row("Blocking", cp.bottleneck_label)
    meta.add_row("Blocked workers", str(cp.blocked_workers))
    meta.add_row("Path length", f"{cp.length_s:.0f}s")
    meta.add_row(Text("Impact", style="v3.meta.key"),
                 Text(cp.impact.upper(), style=_IMPACT_STYLE.get(cp.impact, "v3.meta.val")))
    console.print()
    _print_panel(Panel(
        Group(body, meta),
        title=Text.assemble((CORE, "kryth.core"), ("  Critical Path", "kryth.core")),
        title_align="left", border_style="hud.border.dim",
        padding=(1, 2), expand=False, box=rich.box.ROUNDED,
    ))


def agent_timeline_panel(agents: list, *, width: int = 18) -> None:
    """Phase 6 — per-agent activity bars. `agents`: [{name, step, progress(0-100),
    status, runtime}]. Each renders a label + proportional bar. No-op empty."""
    agents = list(agents or [])
    if not agents:
        return
    body = Text()
    for a in agents:
        name = str(a.get("name") or a.get("id") or "agent")
        prog = max(0, min(100, int(a.get("progress", 0) or 0)))
        st = str(a.get("status", "")).lower()
        filled = round(width * prog / 100)
        bar_style = ("v3.step.done" if st in ("complete", "done")
                     else "term.failed" if st in ("failed", "error")
                     else "v3.step.active")
        body.append(f"  {name:<16}", style="agent.name")
        step = str(a.get("step") or a.get("tool") or "")
        if step:
            body.append(f"{step:<18}", style="v3.meta.val")
        body.append("█" * filled, style=bar_style)
        body.append("░" * (width - filled), style="v3.step.pending")
        rt = a.get("runtime")
        body.append(f"  {prog}%", style="v3.duration")
        if rt:
            body.append(f"  {rt}", style="v3.duration")
        body.append("\n")
    console.print()
    _print_panel(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), ("  Agent Timeline", "kryth.core")),
        title_align="left", border_style="v3.card.border",
        padding=(0, 2), expand=False, box=rich.box.ROUNDED,
    ))


_TASK_STATE_GLYPH = {
    "ready": ("○", "v3.step.pending"),
    "running": ("◐", "v3.step.active"),
    "waiting_dependency": ("◌", "log.warn"),
    "blocked": ("◌", "term.failed"),
    "paused": ("‖", "v3.step.pending"),
    "resumable": ("▸", "v3.step.active"),
    "complete": ("●", "v3.step.done"),
    "failed": ("✗", "term.failed"),
}


def dependency_status_panel(snapshot: list, blocking: dict | None = None) -> None:
    """Phase 8 — task states + the blocking dependency (Phase 9).

    `snapshot` = DependencyAwareQueue.snapshot(); `blocking` =
    most_blocking_dependency(...). Presentation-only; no scheduler coupling."""
    rows = list(snapshot or [])
    if not rows:
        return
    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True)                       # glyph + task
    body.add_column(no_wrap=True, style="v3.duration")  # state
    body.add_column(no_wrap=True, style="log.warn")     # blocked-by / resume
    for r in rows[:20]:
        st = str(r.get("state", "ready"))
        glyph, style = _TASK_STATE_GLYPH.get(st, ("○", "v3.meta.val"))
        name = Text.assemble((f"{glyph} ", style),
                             (str(r.get("description") or r.get("task_id")), "v3.meta.val"))
        extra = ""
        if r.get("blocked_by"):
            extra = f"waiting: {r['blocked_by']}"
        elif r.get("resume_count"):
            extra = f"resumed ×{r['resume_count']}"
        body.add_row(name, st.replace("_", " "), extra)

    pieces = [body]
    if blocking and blocking.get("dependency"):
        b = Text("\n  ")
        b.append("bottleneck ", style="log.warn")
        b.append(str(blocking["dependency"]), style="v3.meta.accent")
        b.append(f"  blocking {blocking.get('blocking', 0)} tasks · "
                 f"{blocking.get('workers_waiting', 0)} workers · "
                 f"impact {str(blocking.get('impact', 'low')).upper()}", style="v3.duration")
        pieces.append(b)

    console.print()
    _print_panel(Panel(
        Group(*pieces),
        title=Text.assemble((CORE, "kryth.core"), ("  Task Lifecycle", "kryth.core")),
        title_align="left", border_style="v3.card.border",
        padding=(0, 2), expand=False, box=rich.box.ROUNDED,
    ))


def tool_card(action: str, path: str = "", *, state: str = "start") -> None:
    """Compact, elegant tool card (UI v4).

    A two-line card: ACTION (upper, accent) + path/target (soft accent). Used
    for the important file operations. `state` ∈ {start, done, error} tints the
    border and title. Minimal — no excessive chrome.
    """
    title_style = {
        "start": "v3.card.title",
        "done": "term.success",
        "error": "term.failed",
    }.get(state, "v3.card.title")
    border = {
        "start": "v3.card.border",
        "done": "term.success",
        "error": "term.failed",
    }.get(state, "v3.card.border")

    inner = Text()
    inner.append(action.upper(), style=title_style)
    if path:
        inner.append("\n")
        inner.append(path, style="v3.card.path")

    _print_panel(Panel(
        inner,
        border_style=border,
        padding=(0, 2),
        expand=False,
        box=rich.box.ROUNDED,
    ))


def plan_panel(plan: dict) -> None:
    body = Table.grid(padding=(0, 1), expand=False)
    body.add_column(style="muted", no_wrap=True, min_width=8, justify="right")
    body.add_column(overflow="fold")

    for key, label in (("goal", "goal"), ("task_type", "type")):
        value = (plan.get(key) or "").strip()
        if value:
            body.add_row(label, f"[title]{value}[/title]" if key == "goal" else f"[accent]{value}[/accent]")

    files = plan.get("required_files") or []
    if files:
        text = Text()
        for i, item in enumerate(files[:20]):
            if i:
                text.append("\n")
            if isinstance(item, dict):
                text.append(item.get("path", "?"), style="title")
                if item.get("purpose"):
                    text.append(f"  {DOT} {item['purpose']}", style="muted")
            else:
                text.append(str(item), style="title")
        body.add_row("files", text)

    steps = plan.get("execution_steps") or []
    if steps:
        text = Text()
        for i, step in enumerate(steps[:20], start=1):
            if i > 1:
                text.append("\n")
            # Use diamond symbol for each stage with spacing
            text.append(f" {CORE}", style="kryth.core")
            text.append(f"  {step}", style="title")
        body.add_row("stages", text)

    validation = plan.get("validation_steps") or []
    if validation:
        body.add_row("verify", "\n".join(str(v) for v in validation[:10]))

    console.print()
    _print_panel(Panel(body, title=Text.assemble((CORE, "kryth.core"), (" Plan", "section.plan")), title_align="left", border_style="divider", padding=(1, 2), expand=False, box=rich.box.ROUNDED))


def plan_prose(text: str) -> None:
    body = Markdown(text) if any(m in text for m in ("#", "*", "`")) else Text(text)
    _print_panel(Panel(body, title=Text.assemble((CORE, "kryth.core"), (" Plan", "section.plan")), title_align="left", border_style="divider", padding=(1, 2), expand=False, box=rich.box.ROUNDED))


def shell_header(command: str, timeout: int, note: str | None) -> None:
    """Display shell command execution header."""
    del timeout
    console.print()
    if note:
        console.print(Text.assemble(
            (CORE, "section.shell"),
            (" EXEC", "section.shell"),
            ("  " + DOT + "  ", "muted"),
            (command, "title"),
            ("  " + DOT + "  ", "muted"),
            (note, "muted")
        ))
    else:
        console.print(Text.assemble(
            (CORE, "section.shell"),
            (" EXEC", "section.shell"),
            ("  " + DOT + "  ", "muted"),
            (command, "title")
        ))


_TODO_MARK = {
    "pending": (WAITING, "muted"),
    "in_progress": (CORE, "kryth.core"),
    "completed": (CORE, "log.success"),
}


def todos_panel(items: list[dict]) -> None:
    if not items:
        console.print("[muted](no tasks)[/muted]")
        return
    body = Table.grid(padding=(0, 1), expand=False)
    body.add_column(no_wrap=True)
    body.add_column(overflow="fold")
    for item in items:
        mark, style = _TODO_MARK.get(item["status"], (WAITING, "muted"))
        text = item["text"]
        cell = f"[strike muted]{text}[/strike muted]" if item["status"] == "completed" else f"[{style}]{text}[/{style}]"
        body.add_row(f"[{style}]{mark}[/{style}]", cell)
    _print_panel(Panel(body, title=Text.assemble((CORE, "kryth.core"), (" Tasks", "section.exec")), title_align="left", border_style="divider", padding=(0, 1), expand=False, box=rich.box.ROUNDED))


def subagent_open(depth: int, description: str) -> None:
    """Display subagent spawn as an engineering team deployment."""
    console.print()
    # Strip internal "[0] prefix" pattern if present
    import re as _re
    clean_desc = _re.sub(r'^\[\d+\]\s*', '', description)
    console.print(Text.assemble(
        (CORE, "agent.running"),
        ("  Team Deployment", "agent.running"),
        ("  " + DOT + "  ", "muted"),
        (clean_desc[:80], "title")
    ))


def subagent_close(depth: int) -> None:
    """Display subagent completion as team task done."""
    console.print(Text.assemble(
        (CORE, "agent.done"),
        ("  Team Task Complete", "agent.done"),
    ))


_PERMISSION_OPTIONS = (("y", "once"), ("a", "always"), ("n", "deny"))


def _release_existing_live() -> None:
    existing = getattr(console, "_live", None)
    if existing is None:
        return
    try:
        existing.stop()
    except Exception:
        try:
            console.clear_live()
        except Exception:
            pass


def _permission_panel(tool: str, signature: str, selected: int) -> Panel:
    """Premium permission request panel with clear visual hierarchy."""
    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=10)
    body.add_column(overflow="fold")

    body.add_row(
        Text.assemble((CORE, "kryth.core"), (" TOOL", "muted")),
        Text(tool, style="title")
    )
    body.add_row(
        Text.assemble((CORE, "kryth.core"), (" REQUEST", "muted")),
        Text(signature, style="muted")
    )

    # Spacer
    body.add_row("", "")

    # Options row with better styling
    options_text = Text()
    for i, (key, label) in enumerate(_PERMISSION_OPTIONS):
        if i > 0:
            options_text.append("    ", style="muted")

        if i == selected:
            options_text.append(f"[{key.upper()}] {label.capitalize()}", style="motion.hot")
        else:
            options_text.append(f"[{key.upper()}] {label.capitalize()}", style="kbd")

    body.add_row("", options_text)

    # Spacer
    body.add_row("", "")

    # Help text
    body.add_row(
        "",
        Text("← → navigate  ·  ↵ confirm  ·  esc deny", style="muted")
    )

    return Panel(
        body,
        title=Text.assemble((WAITING, "log.warn"), (" Action Approval Required", "title")),
        title_align="left",
        border_style="log.warn",
        padding=(1, 2),
        expand=False,
        box=rich.box.ROUNDED,
    )


def ask_permission_interactive(tool: str, signature: str) -> str:
    from rich.live import Live
    from agent.ui.keyread import read_key

    _release_existing_live()
    selected = 0
    console.print()
    with Live(_permission_panel(tool, signature, selected), console=console, refresh_per_second=20, transient=False, auto_refresh=False) as live:
        while True:
            try:
                key = read_key()
            except (EOFError, KeyboardInterrupt):
                return "n"
            if key in ("LEFT", "UP"):
                selected = (selected - 1) % len(_PERMISSION_OPTIONS)
            elif key in ("RIGHT", "DOWN", "TAB"):
                selected = (selected + 1) % len(_PERMISSION_OPTIONS)
            elif key == "ENTER":
                return _PERMISSION_OPTIONS[selected][0]
            elif key in ("ESC", "CTRL_C"):
                return "n"
            elif key in ("y", "a", "n"):
                selected = next(i for i, (k, _) in enumerate(_PERMISSION_OPTIONS) if k == key)
                live.update(_permission_panel(tool, signature, selected), refresh=True)
                return key
            live.update(_permission_panel(tool, signature, selected), refresh=True)


def divider() -> None:
    console.print(Rule(style="divider"))


def goodbye() -> None:
    """Premium shutdown message."""
    console.print()
    console.print(Text.assemble(
        (CORE, "kryth.core"),
        (" KRYTH", "title"),
        ("  ", ""),
        ("Session terminated", "muted")
    ), justify="center")
    console.print()


def _short_paths(paths: list[str], cap: int = 3) -> str:
    if not paths:
        return ""
    if len(paths) <= cap:
        return ", ".join(paths)
    return f"{', '.join(paths[:cap])}  [muted](+{len(paths) - cap} more)[/muted]"


def run_summary_panel(s: dict) -> None:
    status = s.get("status", "done")
    turns_used = s.get("turns_used", 0)
    rows: list[tuple[str, str]] = []

    if status == "max_turns":
        rows.append((f"[log.warn]{ERROR} Incomplete[/log.warn]", f"[muted]{turns_used} turns used; reply 'continue' to resume[/muted]"))
    elif status == "interrupted":
        rows.append((f"[log.warn]{ERROR} Interrupted[/log.warn]", "[muted]partial work preserved[/muted]"))
    elif status == "api_error":
        rows.append((f"[log.error]{ERROR} API error[/log.error]", "[muted]turn stopped before completion[/muted]"))

    if s.get("errors") or s.get("denied") or s.get("shell_fail"):
        rows.append((f"[log.error]{ERROR} Issues[/log.error]", f"[log.error]{s.get('errors', 0)} tool · {s.get('shell_fail', 0)} command · {s.get('denied', 0)} blocked[/log.error]"))
    if s.get("files_written"):
        rows.append((f"[log.success]{CORE} Files created[/log.success]", _short_paths(s["files_written"])))
    if s.get("files_edited"):
        rows.append((f"[accent]{CORE} Files modified[/accent]", _short_paths(s["files_edited"])))
    if s.get("files_deleted"):
        rows.append((f"[log.error]{ERROR} Files deleted[/log.error]", _short_paths(s["files_deleted"])))
    if s.get("shell_ok"):
        rows.append((f"[log.success]{CORE} Tests/commands[/log.success]", f"{s['shell_ok']} passed"))
    if s.get("subagent_spawns"):
        rows.append((f"[accent]{CORE} Workers[/accent]", str(s["subagent_spawns"])))

    activity = []
    if s.get("tools_called"):
        activity.append(f"{s['tools_called']} tool calls")
    if s.get("retries"):
        activity.append(f"{s['retries']} retries")
    if s.get("coercions"):
        activity.append(f"{s['coercions']} argument fixes")
    if activity:
        rows.append((f"[muted]{WAITING} Activity[/muted]", "[muted]" + " · ".join(activity) + "[/muted]"))

    if not rows:
        return

    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True)
    body.add_column(overflow="fold")
    for left, right in rows:
        body.add_row(left, right)

    title_style = "log.success" if status == "done" else "log.warn"
    title = "Complete" if status == "done" else status.replace("_", " ").title()
    _print_panel(Panel(body, title=Text.assemble((CORE if status == "done" else ERROR, title_style), (" " + title, title_style)), title_align="left", border_style="divider", padding=(0, 1), expand=False, box=rich.box.ROUNDED))
