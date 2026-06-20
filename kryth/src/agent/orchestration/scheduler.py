"""Dynamic Parallel Scheduler — spawns agents according to the DAG layers.

Workers are created one layer at a time:
  - Layer 1 (no deps) starts immediately
  - Each subsequent layer starts only when its dependencies complete
  - Within a layer, agents run in parallel (ThreadPoolExecutor)

Enhancements over the original:
  - Ownership enforcement: agents acquire file/dir locks before running
  - Work stealing: idle agents pick up tasks from failed/slow siblings
  - Failure recovery: failed agents are retried once via WorkQueue
  - Dynamic worker count: scales with team size and cost estimate
  - Agent lifecycle events: AGENT_CREATED, AGENT_TASK_START, etc.
"""
from __future__ import annotations

import contextvars
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from agent.orchestration.task_dag import TaskDAG
from agent.orchestration.team_generator import AgentRole, TeamPlan
from agent.orchestration.work_queue import WorkQueue, WorkItem

# Module-level shared state for provider health tracking (additive, advisory only).
# Workers record successes/failures here; the dashboard reads it for incident display.
try:
    from agent.production.reliability import (
        ProviderHealth, RetryPolicy, classify_error, is_provider_failure,
    )
    _provider_health = ProviderHealth()
    _retry_policy = RetryPolicy()
    _RELIABILITY_AVAILABLE = True
except Exception:
    _provider_health = None  # type: ignore[assignment]
    _retry_policy = None     # type: ignore[assignment]
    _RELIABILITY_AVAILABLE = False


@dataclass
class WorkerStats:
    """Per-worker performance counters — populated during agent execution."""
    agent_id: str = ""
    role: str = ""
    active_s: float = 0.0       # wall time actually executing (LLM calls)
    wait_s: float = 0.0         # time spent in dependency wait
    tokens_in: int = 0
    tokens_out: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    provider_retries: int = 0

    @property
    def total_s(self) -> float:
        return self.active_s + self.wait_s

    @property
    def utilization(self) -> float:
        """Fraction of time spent doing useful work (not waiting)."""
        t = self.total_s
        return self.active_s / t if t > 0 else 1.0


@dataclass
class WorkerResult:
    agent_id: str
    role: str
    success: bool
    output: str
    turns_used: int = 0
    error: str = ""
    stats: "WorkerStats | None" = None   # V1.7: populated after execution


@dataclass
class SchedulerResult:
    success: bool
    outputs: Dict[str, WorkerResult] = field(default_factory=dict)
    final_output: str = ""
    total_turns: int = 0
    failed_agents: List[str] = field(default_factory=list)
    worker_stats: "Dict[str, WorkerStats]" = field(default_factory=dict)  # V1.7


ProgressCallback = Callable[[str, str, str], None]   # (agent_id, role, status)

# Thread-local storage for the active scheduler's notify callback.
# _run_single_agent uses this so provider-retry loops can emit RETRYING/RECOVERED
# status events back to the dashboard without needing a direct function reference.
_local = threading.local()


def _get_notify_fn():
    """Return the active scheduler's notify callback, or None."""
    return getattr(_local, "notify_fn", None)


def _set_notify_fn(fn):
    _local.notify_fn = fn


def _dynamic_worker_count(team_size: int, cost_estimate: int) -> int:
    """Concurrent-agent cap. Each agent is a full LLM session; running many at
    once hammers the provider and triggers rate-limit (429/api) errors. Cap the
    number RUNNING simultaneously (default 4) — the work queue lets remaining
    agents start as soon as a slot frees, so a 9-agent layer drains 4-at-a-time
    instead of firing all 9 into the API at once.
    """
    import os
    cap = int(os.environ.get("KRYTH_MAX_CONCURRENT_AGENTS", "4"))
    cap = max(1, min(cap, 16))
    return min(team_size, cap)


def _shard_context(full_context: str, agent_role: str, task_desc: str, max_chars: int = 1800) -> str:
    """Extract only the context lines relevant to this agent's role and tasks.

    Scores each line: +2 for agent role keyword match, +1 for task keyword match.
    Returns top-scoring lines up to max_chars. Fallback: first max_chars chars.
    Phase 5 target: ≥40% context reduction vs full project_context[:3000].
    """
    if not full_context:
        return ""
    if len(full_context) <= max_chars:
        return full_context

    # Build keyword sets from role and task descriptions
    import re as _re
    role_words = set(_re.findall(r"[a-z]+", agent_role.lower()))
    task_words = set(_re.findall(r"[a-z]+", task_desc.lower()))
    # Remove noise words
    _STOPWORDS = {"the", "a", "an", "is", "in", "of", "to", "for", "and", "or",
                  "with", "your", "you", "all", "this", "that", "be", "are", "do"}
    role_words -= _STOPWORDS
    task_words -= _STOPWORDS

    lines = full_context.splitlines()
    scored = []
    for line in lines:
        if not line.strip():
            scored.append((0, line))
            continue
        low = line.lower()
        score = sum(2 for w in role_words if w in low) + sum(1 for w in task_words if w in low)
        scored.append((score, line))

    # Sort descending by score, maintain original order for equal scores
    order = sorted(range(len(scored)), key=lambda i: -scored[i][0])
    result_lines = []
    chars = 0
    seen = set()
    for idx in order:
        line = scored[idx][1]
        if idx in seen:
            continue
        seen.add(idx)
        if chars + len(line) + 1 > max_chars:
            break
        result_lines.append((idx, line))
        chars += len(line) + 1

    # Restore original order
    result_lines.sort(key=lambda x: x[0])
    sharded = "\n".join(l for _, l in result_lines)
    return sharded if sharded.strip() else full_context[:max_chars]


def _build_agent_system_prompt(
    agent: AgentRole,
    dag: TaskDAG,
    project_context: str,
    prior_outputs: Dict[str, str],
    bus=None,
) -> str:
    task_descriptions = []
    for nid in agent.task_node_ids:
        node = dag.nodes.get(nid)
        if node:
            task_descriptions.append(f"- {node.name}: {node.description}")
            if node.validation:
                task_descriptions.append(f"  Validation: {'; '.join(node.validation)}")

    tasks_block = "\n".join(task_descriptions) or "Complete your assigned work."

    prior_block = ""
    if prior_outputs:
        parts = []
        direct_deps = set(agent.dependencies)
        for aid, out in prior_outputs.items():
            if aid.startswith("__"):  # skip internal keys like __user_input__
                continue
            if not out.strip():
                continue
            # Direct dependencies get more context; transitive get less (Phase 5)
            cap = 1500 if aid in direct_deps else 800
            parts.append(f"=== {aid} output ===\n{out[:cap]}")
        if parts:
            prior_block = "\n\nContext from completed agents:\n" + "\n\n".join(parts)

    validation_block = ""
    if agent.validation_rules:
        rules = "\n".join(f"- {r}" for r in agent.validation_rules)
        validation_block = f"\n\nValidation requirements:\n{rules}"

    recovery_block = ""
    if agent.recovery_rules:
        rules = "\n".join(f"- {r}" for r in agent.recovery_rules)
        recovery_block = f"\n\nRollback strategy:\n{rules}"

    # Inject sibling events from the team bus
    bus_block = ""
    if bus is not None:
        try:
            events = bus.poll(agent.id)
            if events:
                bus_block = "\n\n" + bus.format_for_prompt(events)
        except Exception:
            pass

    # V5: Ponytail worker execution philosophy — additive, only when the
    # active execution profile is PONYTAIL. Does not touch DAG/team/milestone
    # generation, only the text injected into this worker's own system prompt.
    ponytail_block = ""
    try:
        from agent.production.execution_profiles import active_profile, is_ponytail
        if is_ponytail(active_profile()):
            from agent.orchestration.ponytail import PONYTAIL_RULES, dependency_reuse_hints
            ponytail_block = "\n\n" + PONYTAIL_RULES
            hints = dependency_reuse_hints(agent.mission + " " + tasks_block)
            if hints:
                ponytail_block += "\n\nKnown shortcuts for this task:\n" + "\n".join(f"- {h}" for h in hints)
    except Exception:
        pass

    return f"""You are a specialized engineering agent: {agent.role.upper()}

MISSION: {agent.mission}

ORIGINAL USER REQUEST: {prior_outputs.get("__user_input__", "").strip() or "(see tasks below)"}

ASSIGNED TASKS:
{tasks_block}

STRICT RULES:
1. Focus ONLY on your assigned tasks. Do not work on other areas.
2. Call tools and implement code — do not describe what you WOULD do.
3. When ALL assigned tasks are done and verified: stop calling tools and
   emit your final summary. Do NOT create extra files, tests, or improvements
   beyond what was assigned.
4. Your FINAL message (no tool calls after it) MUST start with: AGENT_COMPLETE: {agent.id}
5. NEVER re-run a command that already succeeded or already showed
   "Requirement already satisfied". If pip install shows packages are installed,
   move on — do NOT run pip install again.
6. FAILURE PROTOCOL — mandatory when any command fails:
   a. READ the error output (first 30 lines are enough).
   b. Identify the ROOT CAUSE before touching any file.
   c. Make ONE targeted fix based on the root cause.
   d. If the SAME command fails a THIRD time: give up on that task,
      document the error, and emit AGENT_COMPLETE. Do not keep guessing.
7. You have a limited turn budget. Do not waste turns re-running the same
   failing command with tiny variations. If something fails twice the same
   way, it needs a fundamentally different approach — or you should stop.

EXECUTION CONTRACT — MANDATORY:
You are a WORKER in a milestone-driven engineering organization.
Chain of command: Planner (CEO) → Program Manager → Team Lead → You (Worker).

Your ONLY job:
- Execute the assigned contract below.
- Do NOT re-plan, re-scope, or re-architect.
- Do NOT discuss design decisions with other teams.
- Call tools immediately on your first turn.
- When ALL contract deliverables are complete: emit AGENT_COMPLETE: {agent.id}

ORGANIZATIONAL RULES:
1. Planner has already designed the architecture — do not question it.
2. Team Leads coordinate. Workers execute.
3. Your contract is final. Scope is locked.
4. Deliver what is specified. Nothing more, nothing less.

PROJECT CONTEXT:
{_shard_context(project_context, agent.role, tasks_block) if project_context else "(none)"}
{prior_block}{validation_block}{recovery_block}{bus_block}{ponytail_block}"""


def _run_single_agent(
    agent: AgentRole,
    dag: TaskDAG,
    project_context: str,
    prior_outputs: Dict[str, str],
    max_turns: int,
    ownership_mgr=None,
    bus=None,
) -> WorkerResult:
    """Run one agent, acquiring ownership locks for its directories first."""
    from agent.tools._subagent import _build_nested
    from agent.agent_loop import run_inner_loop
    from agent.session import push_session, pop_session, get_session

    # Acquire file/dir ownership locks before starting
    lock_keys = (
        [f"FILE:{f}" for f in agent.owns.files]
        + [f"DIR:{d}" for d in agent.owns.directories]
    )
    if ownership_mgr and lock_keys:
        ownership_mgr.acquire_all(lock_keys)

    # Phase 4 — Dependency waiting guard:
    # If any required dependency has not produced output yet, return immediately
    # with status="waiting_dependency" instead of spinning the LLM on a blocked task.
    # (The layer system prevents this in normal flow; this is a safety net for
    # work-stealing edge cases where a stolen task's deps might not be complete.)
    _unmet_deps = [
        dep for dep in (agent.dependencies or [])
        if dep not in prior_outputs or not str(prior_outputs.get(dep, "")).strip()
    ]
    if _unmet_deps:
        _waiting_for = ", ".join(_unmet_deps[:3])
        try:
            _notify_fn = _get_notify_fn()
            if _notify_fn is not None:
                _notify_fn(agent.id, agent.role, "waiting",
                           f"waiting for: {_waiting_for}")
        except Exception:
            pass
        return WorkerResult(
            agent_id=agent.id, role=agent.role,
            success=False, output="",
            error=f"waiting_dependency:{_waiting_for}",
        )

    # Respect per-agent turn budget when explicitly set; fall back to caller's cap.
    effective_turns = max(max_turns, getattr(agent, "max_turns", 0) or max_turns)
    prompt = _build_agent_system_prompt(agent, dag, project_context, prior_outputs, bus=bus)
    parent = get_session()
    nested = _build_nested(agent.role, prompt, parent.depth, parent_profile=getattr(parent, 'profile', 'default'))
    nested.system_prompt = prompt
    nested._agent_role = agent.role   # shown in live progress spinner
    nested._agent_id   = agent.id    # used by MC dashboard lookup
    # Inherit the approved mission contract so workers don't re-prompt per file
    # (the user already approved the mission in the Execution Preview).
    nested.mission_contract = getattr(parent, "mission_contract", None)
    nested.remembered_permissions = dict(getattr(parent, "remembered_permissions", {}) or {})
    if not nested.messages:
        nested.messages = [{"role": "system", "content": prompt}]
    # Phase 6 — No Replanning: inject guard when mission plan exists
    _has_plan = getattr(parent, "mission_contract", None) is not None
    try:
        from agent.anti_paralysis import worker_plan_guard
        _plan_guard = worker_plan_guard(_has_plan)
        if _plan_guard:
            nested.messages.append({"role": "system", "content": _plan_guard})
    except Exception:
        pass
    # V3 — Inject deliverable contract if a ProjectPlan is attached to parent session
    try:
        _project_plan = getattr(parent, "_project_plan", None)
        if _project_plan is not None:
            _contract = _project_plan.get_contract(agent.role.replace(" Team", ""))
            if _contract is None:
                _contract = _project_plan.get_contract(agent.id)
            if _contract is not None:
                nested.messages.append({
                    "role": "system",
                    "content": (
                        "[DELIVERABLE CONTRACT — READ BEFORE STARTING]\n"
                        + _contract.to_worker_brief()
                    ),
                })
    except Exception:
        pass
    nested.messages.append({"role": "user", "content": f"Begin your work: {agent.mission}"})

    # V1.7: per-agent stats
    _wstats = WorkerStats(agent_id=agent.id, role=agent.role)
    _agent_start = __import__("time").monotonic()

    token = push_session(nested)
    try:
        result = run_inner_loop(nested, effective_turns, verbose_usage=False)
        _wstats.active_s = __import__("time").monotonic() - _agent_start
        content = getattr(result, "content", "") or ""
        turns = getattr(result, "turns_used", 0)
        _wstats.llm_calls = turns
        _wstats.tool_calls = getattr(nested, "tool_call_count", 0)
        _wstats.tokens_in  = getattr(nested, "cumulative_in_tokens", 0)
        _wstats.tokens_out = getattr(nested, "cumulative_out_tokens", 0)
        # Treat interrupted/api_error as failure so the scheduler can retry.
        # "done" and "max_turns" are both acceptable completions — max_turns
        # means partial output is available and better than nothing.
        status = getattr(result, "status", "done")
        success = status not in ("interrupted", "api_error")
        error = f"agent status: {status}" if not success else ""

        # Record success in provider health tracker.
        if success and _RELIABILITY_AVAILABLE and _provider_health is not None:
            import os as _os
            _prov = _os.environ.get("KRYTH_BASE_URL", "default")
            try:
                _provider_health.record_success(_prov)
            except Exception:
                pass

        # Provider failure isolation (BUG 3/4): if the failure looks like a
        # transient provider issue (timeout / rate-limit / malformed stream),
        # consult the RetryPolicy and retry at the agent level before reporting
        # the team as FAILED. This prevents a provider blip from marking a
        # healthy team as failed.
        if not success and _RELIABILITY_AVAILABLE and _retry_policy is not None:
            _ec = classify_error(error)
            if is_provider_failure(_ec):
                import os as _os
                import time as _time
                _prov = _os.environ.get("KRYTH_BASE_URL", "default")
                try:
                    _provider_health.record_error(_prov, _ec)
                except Exception:
                    pass
                for _attempt in range(1, 4):  # up to 3 retries for provider errors
                    _dec = _retry_policy.decide(_ec, _attempt)
                    if not _dec.should_retry:
                        break
                    # Emit RETRYING status so dashboard shows progress
                    _retry_detail = (
                        f"provider retry {_attempt}/3 "
                        f"({_ec.value}, backoff {_dec.backoff_s:.1f}s)"
                    )
                    try:
                        from agent import ui as _ui
                        _ui.muted(f"  ⟳ {agent.role} — {_retry_detail}")
                    except Exception:
                        pass
                    # Notify scheduler notify callback if available
                    try:
                        _notify_fn = _get_notify_fn()
                        if _notify_fn is not None:
                            _notify_fn(agent.id, agent.role, "retrying", _retry_detail)
                    except Exception:
                        pass
                    if _dec.backoff_s > 0:
                        _time.sleep(min(_dec.backoff_s, 8.0))
                    # Re-run the agent loop for this attempt
                    nested2 = _build_nested(
                        agent.role, prompt, parent.depth,
                        parent_profile=getattr(parent, 'profile', 'default')
                    )
                    nested2.system_prompt = prompt
                    nested2._agent_role = agent.role
                    nested2._agent_id = agent.id
                    nested2.mission_contract = getattr(parent, "mission_contract", None)
                    nested2.remembered_permissions = dict(
                        getattr(parent, "remembered_permissions", {}) or {}
                    )
                    if not nested2.messages:
                        nested2.messages = [{"role": "system", "content": prompt}]
                    nested2.messages.append({
                        "role": "user",
                        "content": f"Begin your work: {agent.mission}",
                    })
                    _tok2 = push_session(nested2)
                    try:
                        _r2 = run_inner_loop(nested2, effective_turns, verbose_usage=False)
                        _s2 = getattr(_r2, "status", "done")
                        if _s2 not in ("interrupted", "api_error"):
                            try:
                                _provider_health.record_success(_prov)
                            except Exception:
                                pass
                            # Emit RECOVERED status
                            try:
                                _notify_fn = _get_notify_fn()
                                if _notify_fn is not None:
                                    _notify_fn(agent.id, agent.role, "running",
                                               f"recovered after {_attempt} retry")
                            except Exception:
                                pass
                            return WorkerResult(
                                agent_id=agent.id, role=agent.role,
                                success=True,
                                output=getattr(_r2, "content", "") or "",
                                turns_used=turns + getattr(_r2, "turns_used", 0),
                            )
                        # Another provider failure — record and continue loop
                        _ec2 = classify_error(f"agent status: {_s2}")
                        try:
                            _provider_health.record_error(_prov, _ec2, retried=True)
                        except Exception:
                            pass
                    except Exception as _exc2:
                        _ec2 = classify_error(_exc2)
                        try:
                            _provider_health.record_error(_prov, _ec2, retried=True)
                        except Exception:
                            pass
                    finally:
                        pop_session(_tok2)

        # Self-healing: on failure, attempt automated repair before giving up
        if not success:
            try:
                from agent.orchestration.repair_loop import attempt_repair
                repair = attempt_repair(
                    agent_id=agent.id,
                    role=agent.role,
                    mission=agent.mission,
                    original_error=error,
                    project_context=prior_outputs.get("__context__", ""),
                    max_turns=min(30, max_turns // 2),
                )
                if repair.success:
                    return WorkerResult(
                        agent_id=agent.id, role=agent.role,
                        success=True, output=repair.output,
                        turns_used=turns + getattr(repair, "attempts", 0) * 5,
                    )
            except Exception:
                pass  # repair unavailable — fall through to failure

        return WorkerResult(
            agent_id=agent.id, role=agent.role,
            success=success, output=content, turns_used=turns, error=error,
            stats=_wstats,
        )
    except Exception as exc:
        _wstats.active_s = __import__("time").monotonic() - _agent_start
        # Classify the exception — provider errors get a human reason, not a
        # raw exception name in the dashboard (BUG 5).
        _exc_str = str(exc)
        _reason = _exc_str
        if _RELIABILITY_AVAILABLE:
            try:
                from agent.production.reliability import classify_error as _ce, is_provider_failure as _ipf
                _ec = _ce(exc)
                if _ipf(_ec):
                    _reason = {
                        "timeout":    "Provider timeout — connection to model dropped",
                        "rate_limit": "Provider rate-limited — too many concurrent requests",
                        "malformed":  "Provider stream error — malformed chunk from model",
                        "payload_too_large": "Tool payload too large — request exceeded limits",
                        "provider":   "Transient provider error — gateway returned 5xx",
                    }.get(_ec.value, _exc_str)
            except Exception:
                pass
        return WorkerResult(
            agent_id=agent.id, role=agent.role,
            success=False, output="", error=_reason,
            stats=_wstats,
        )
    finally:
        pop_session(token)
        if ownership_mgr and lock_keys:
            ownership_mgr.release_all(lock_keys)
        # Push provider health snapshot to dashboard after each agent completes.
        try:
            from agent.ui.dashboard import push_provider_health
            push_provider_health()
        except Exception:
            pass


def _run_integrator(
    prior_outputs: Dict[str, str],
    dag: TaskDAG,
    project_context: str,
    user_input: str,
    max_turns: int,
) -> WorkerResult:
    outputs_text = "\n\n".join(
        f"=== {aid} ===\n{out}" for aid, out in prior_outputs.items()
    )

    from agent.tools._subagent import _build_nested
    from agent.agent_loop import run_inner_loop
    from agent.session import push_session, pop_session, get_session

    prompt = f"""You are the INTEGRATOR for a parallel engineering project.

ORIGINAL REQUEST: {user_input}

COMPONENT OUTPUTS:
{outputs_text[:6000]}

Your job:
1. Review all component outputs above
2. Identify any missing connections, import issues, or configuration gaps
3. Create any necessary glue code, shared config, or setup files
4. Ensure the overall system is cohesive and functional
5. Produce a final summary starting with: INTEGRATION_COMPLETE

Do NOT rewrite existing work — only add missing glue, fix imports, and surface a clear summary."""

    parent = get_session()
    nested = _build_nested("Integrator", prompt, parent.depth, parent_profile=getattr(parent, 'profile', 'default'))
    nested.system_prompt = prompt
    nested.mission_contract = getattr(parent, "mission_contract", None)
    nested.remembered_permissions = dict(getattr(parent, "remembered_permissions", {}) or {})
    nested.messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Review all component outputs and integrate the system."},
    ]

    token = push_session(nested)
    try:
        result = run_inner_loop(nested, max_turns, verbose_usage=False)
        content = getattr(result, "content", "") or ""
        return WorkerResult(agent_id="integrator", role="Integrator", success=True, output=content)
    except Exception as exc:
        return WorkerResult(agent_id="integrator", role="Integrator", success=False, output="", error=str(exc))
    finally:
        pop_session(token)


def _agent_execution_layers(agents: List[AgentRole]) -> List[List[AgentRole]]:
    """Topological sort of agents by their inter-agent dependency graph.

    Returns a list of layers where each layer's agents have all their
    upstream dependencies satisfied by previous layers. Agents within
    a layer are independent and can run in parallel.

    Uses Kahn's algorithm — same as dag.layers() but at the agent level.
    Agents with unknown/missing dependencies are treated as ready.
    """
    agent_map = {a.id: a for a in agents}
    # indegree = number of deps that must complete before this agent can start
    indegree: Dict[str, int] = {a.id: 0 for a in agents}
    for a in agents:
        for dep in a.dependencies:
            if dep in agent_map:  # only count known deps
                indegree[a.id] += 1

    layers: List[List[AgentRole]] = []
    completed: Set[str] = set()
    remaining = list(agents)

    while remaining:
        # Agents whose all dependencies are now satisfied
        ready = [
            a for a in remaining
            if all(
                dep not in agent_map or dep in completed
                for dep in a.dependencies
            )
        ]
        if not ready:
            # Circular deps or all-unknown deps — run everything left together
            ready = remaining[:]
        layers.append(ready)
        for a in ready:
            completed.add(a.id)
        ready_ids = {a.id for a in ready}
        remaining = [a for a in remaining if a.id not in ready_ids]

    return layers


def run_schedule(
    dag: TaskDAG,
    team: TeamPlan,
    strategy: str,
    project_context: str = "",
    user_input: str = "",
    max_turns_per_agent: int = 80,
    max_workers: int = 4,
    on_progress: Optional[ProgressCallback] = None,
) -> SchedulerResult:
    """Execute the team according to the DAG and strategy.

    Ownership locks prevent concurrent writes to the same directory.
    A WorkQueue enables work stealing: after completing its assigned task,
    an idle agent picks up any pending tasks left by failed siblings.
    """
    from agent import ui

    # Lazy import ownership manager from tool_scheduler
    try:
        from agent.tool_scheduler import _OwnershipManager
        ownership = _OwnershipManager()
    except Exception:
        ownership = None

    # Create team event bus and subscribe all agents
    try:
        from agent.orchestration.team_event_bus import TeamEventBus
        bus = TeamEventBus()
        for a in team.agents:
            bus.subscribe(a.id)
    except Exception:
        bus = None

    # Emit AGENT_CREATED for each agent
    try:
        from agent.ui import agent_created
        for a in team.agents:
            agent_created(a.id, a.role, len(a.task_node_ids))
    except Exception:
        pass

    # Dynamic worker count
    est_tokens = team.estimated_total_tokens
    n_workers = _dynamic_worker_count(len(team.agents), est_tokens)
    effective_workers = min(max_workers, n_workers)

    agent_map: Dict[str, AgentRole] = {a.id: a for a in team.agents}
    completed_ids: Set[str] = set()
    outputs: Dict[str, WorkerResult] = {}
    prior_outputs: Dict[str, str] = {"__user_input__": user_input}
    failed: List[str] = []
    prior_lock = threading.Lock()

    # Compute agent layers early — needed for dashboard init, MC fallback, and main loop
    agent_layers = _agent_execution_layers(team.agents)
    n_layers = len(agent_layers)

    # ── Live Dashboard — background thread owns the Rich Live display ─────────
    # start_dashboard() spawns a daemon thread (_rich_dashboard_loop) that:
    #   1. Stops the existing spinner
    #   2. Creates its own Rich Live
    #   3. Drains the push_event() queue at 4 FPS
    # Agent workers call push_event() from any thread — queue is thread-safe.
    _dash_started = False
    if len(team.agents) > 1:
        try:
            from agent.ui.dashboard import start_dashboard, push_event
            # Stop the spinner on the main thread BEFORE spawning the dashboard
            # thread. Rich Status/Live must be stopped on the thread that created
            # them; calling stop() from the background thread deadlocks the
            # Rich internal buffer lock.
            try:
                from agent.ui.renderer import _activity
                _activity._stop_cycler()
                sp = getattr(_activity, "_spinner", None)
                if sp:
                    sp.stop()
            except Exception:
                pass
            start_dashboard(
                goal=user_input[:60] or "Mission",
                total_agents=len(team.agents),
                total_layers=n_layers,
            )
            push_event("timeline", message=f"Spawned {len(team.agents)} agents")
            # Pre-register all agents as WAITING so the dashboard shows the
            # full org before execution starts — agents update to RUNNING as
            # their layer begins.
            for _ag in team.agents:
                push_event("agent_update", id=_ag.id, role=_ag.role,
                           status="waiting", task="waiting for dependencies")
            _dash_started = True
            try:
                from agent.ui.streaming import set_parallel_mode
                set_parallel_mode(True)
            except Exception:
                pass
        except Exception:
            _dash_started = False

    def _dash_update():
        pass  # background thread handles refresh automatically

    def _dash_stop():
        if _dash_started:
            try:
                from agent.ui.streaming import set_parallel_mode
                set_parallel_mode(False)
            except Exception:
                pass
            try:
                import time as _t
                from agent.ui.dashboard import push_event, stop_dashboard
                push_event("progress", percent=100)
                _t.sleep(0.05)  # let background thread render final frame
                stop_dashboard()
            except Exception:
                pass

    # Legacy MC fallback (only if dashboard failed to start)
    _mc = None
    if not _dash_started and len(team.agents) > 1:
        try:
            from agent.ui.mission_control import MissionControl
            _mc = MissionControl(
                goal=user_input[:60] or "Mission",
                total_agents=len(team.agents),
                total_layers=n_layers,
            )
            _mc.start()
        except Exception:
            _mc = None

    def _notify(aid: str, role: str, status: str, detail: str = "") -> None:
        if on_progress:
            on_progress(aid, role, status)

        # Push to Live dashboard
        if _dash_started:
            try:
                from agent.ui.dashboard import push_event
                push_event("agent_update", id=aid, role=role, status=status)
                if status == "running":
                    push_event("timeline", message=f"{role} started")
                elif status == "done":
                    push_event("timeline", message=f"{role} done")
                elif status == "failed":
                    msg = detail or f"{role} failed"
                    push_event("timeline", message=msg)
                elif status == "retrying":
                    push_event("timeline", message=detail or f"{role} retrying…")
                _dash_update()
            except Exception:
                pass

        # Update legacy Rich MC panel
        if _mc is not None:
            try:
                task_hint = {
                    "running":  f"working on {aid}",
                    "done":     "completed",
                    "failed":   detail or "failed",
                    "repairing": "self-healing…",
                    "retrying": detail or "provider retry…",
                }.get(status, status)
                _mc.set_agent(aid, role, status, task_hint)
            except Exception:
                pass

        # Plain text fallback
        if not _dash_started and _mc is None:
            if status == "running":
                ui.muted(f"  ◈ {role}")
            elif status == "done":
                ui.muted(f"  ✓ {role}")
            elif status == "failed":
                ui.warn(f"  ✗ {role} — {detail or 'failed'}")
            elif status == "retrying":
                ui.muted(f"  ⟳ {role} — {detail or 'retrying…'}")
        try:
            from agent.ui import agent_task_start, agent_task_done, agent_failed
            if status == "running":
                agent_task_start(aid, aid, role)
            elif status == "done":
                agent_task_done(aid, aid, 0)
            elif status == "failed":
                agent_failed(aid, role, detail or "execution error")
        except Exception:
            pass

    def _broadcast_events(agent_id: str, output: str) -> None:
        """Scan agent output for EVENT: lines and broadcast to sibling agents."""
        if bus is None or not output:
            return
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("EVENT:"):
                detail = stripped[6:].strip()
                try:
                    bus.publish(agent_id, "discovery", {"detail": detail})
                except Exception:
                    pass

    # Single-agent fast path
    if strategy == "single" or len(team.agents) == 1:
        agent = team.agents[0]
        _notify(agent.id, agent.role, "running")
        result = _run_single_agent(agent, dag, project_context, {}, max_turns_per_agent, ownership, bus)
        outputs[agent.id] = result
        if not result.success:
            failed.append(agent.id)
        _notify(agent.id, agent.role, "done" if result.success else "failed")
        return SchedulerResult(
            success=not failed, outputs=outputs,
            final_output=result.output,
            total_turns=result.turns_used,
            failed_agents=failed,
        )

    # Register _notify in thread-local so provider-retry loops in worker
    # threads can emit RETRYING/RECOVERED status events to the dashboard.
    _set_notify_fn(_notify)

    # Build work queue for stealing
    work_queue = WorkQueue()

    for layer_idx, layer_agents in enumerate(agent_layers, 1):
        if not layer_agents:
            continue

        # Always print a visible layer header — user must see what's running
        agent_names = ", ".join(a.role for a in layer_agents)
        parallel_note = " [parallel]" if len(layer_agents) > 1 else ""
        ui.muted(f"\n  ◈ Layer {layer_idx}/{n_layers}{parallel_note} — {agent_names}")

        # Update dashboard layer + progress
        pct = int((layer_idx - 1) * 90 / max(n_layers, 1))
        if _dash_started:
            try:
                from agent.ui.dashboard import push_event
                push_event("layer", current=layer_idx)
                push_event("progress", percent=pct)
                push_event("timeline", message=f"Layer {layer_idx}: {agent_names[:50]}")
                _dash_update()
            except Exception:
                pass
        if _mc is not None:
            _mc.set_layer(layer_idx)
            _mc.set_progress(pct)

        # Pre-load this layer into the work queue
        for ag in layer_agents:
            work_queue.submit(
                ag.id, ag.role,
                _build_agent_system_prompt(ag, dag, project_context, prior_outputs),
                max_turns_per_agent,
                priority=len(ag.dependencies),
            )

        # Parallel-first: run in parallel whenever there are multiple agents
        # in this layer, regardless of the declared strategy.  The strategy
        # field only defaults "sequential" when the DAG is strictly linear
        # (every layer has exactly 1 agent); multi-agent layers always fan out.
        if len(layer_agents) > 1:
            # Capture context ONCE here; each worker gets its OWN copy via
            # ctx.copy() so push_session / pop_session are fully isolated.
            # Using the same ctx.run() for all workers would make them share
            # the ContextVar slot, causing get_session() to return the wrong
            # nested session and failing immediately.
            _parent_ctx = contextvars.copy_context()

            def _worker(ag: AgentRole) -> WorkerResult:
                with prior_lock:
                    snapshot = dict(prior_outputs)
                result = _parent_ctx.copy().run(
                    _run_single_agent, ag, dag, project_context,
                    snapshot, max_turns_per_agent, ownership, bus,
                )
                _broadcast_events(ag.id, result.output)
                if result.success:
                    work_queue.complete(ag.id, result.output)
                else:
                    work_queue.fail(ag.id, result.error, retry=True)
                    # Work stealing: pick up a pending task if available
                    stolen = work_queue.try_steal()
                    if stolen:
                        steal_agent = agent_map.get(stolen.task_id)
                        if steal_agent:
                            try:
                                from agent.ui import work_stolen
                                work_stolen(ag.id, stolen.task_id)
                            except Exception:
                                pass
                            steal_result = _parent_ctx.copy().run(
                                _run_single_agent, steal_agent, dag, project_context,
                                snapshot, max_turns_per_agent, ownership, bus,
                            )
                            _broadcast_events(steal_agent.id, steal_result.output)
                            work_queue.complete(stolen.task_id, steal_result.output)
                            return steal_result
                return result

            # Cap CONCURRENT agents (effective_workers) — not one thread per
            # agent. A 9-agent layer with a cap of 4 runs 4 at a time; the rest
            # queue in the pool and start as slots free. Prevents the provider
            # rate-limit storm from firing all agents at once.
            _layer_workers = max(1, min(effective_workers, len(layer_agents)))
            with ThreadPoolExecutor(max_workers=_layer_workers, thread_name_prefix="kryth-agent") as pool:
                for agent in layer_agents:
                    _notify(agent.id, agent.role, "running")

                futures = {pool.submit(_worker, ag): ag for ag in layer_agents}

                # Per-layer timeout: 480s leaves headroom within the 600s mission limit
                _layer_timeout = int(os.environ.get("KRYTH_LAYER_TIMEOUT", "480"))
                try:
                    for future in as_completed(futures, timeout=_layer_timeout):
                        ag = futures[future]
                        try:
                            result = future.result()
                        except Exception as exc:
                            result = WorkerResult(
                                agent_id=ag.id, role=ag.role,
                                success=False, output="", error=str(exc),
                            )
                        outputs[ag.id] = result
                        completed_ids.add(ag.id)
                        with prior_lock:
                            prior_outputs[ag.id] = result.output
                        if not result.success:
                            failed.append(ag.id)
                        _notify(ag.id, ag.role, "done" if result.success else "failed")
                except TimeoutError:
                    for ag in [a for a in layer_agents if a.id not in completed_ids]:
                        failed.append(ag.id)
                        _notify(ag.id, ag.role, "failed")

        else:
            for agent in layer_agents:
                _notify(agent.id, agent.role, "running")
                result = _run_single_agent(
                    agent, dag, project_context, prior_outputs,
                    max_turns_per_agent, ownership, bus,
                )
                _broadcast_events(agent.id, result.output)
                outputs[agent.id] = result
                completed_ids.add(agent.id)
                prior_outputs[agent.id] = result.output
                if not result.success:
                    failed.append(agent.id)
                    work_queue.fail(agent.id, result.error, retry=False)
                else:
                    work_queue.complete(agent.id, result.output)
                _notify(agent.id, agent.role, "done" if result.success else "failed")

    # ── No-idle drain: steal any pending tasks before stopping ────────────────
    # After the last layer's workers finish, drain anything still in the queue
    # (work-steal failures may have re-queued tasks). Run them sequentially now.
    _steal_rounds = 0
    while not work_queue.is_empty() and _steal_rounds < 20:
        _steal_rounds += 1
        stolen = work_queue.try_steal()
        if stolen is None:
            break
        steal_agent = agent_map.get(stolen.task_id)
        if steal_agent and steal_agent.id not in completed_ids:
            _notify(steal_agent.id, steal_agent.role, "running")
            result = _run_single_agent(
                steal_agent, dag, project_context, prior_outputs,
                max_turns_per_agent, ownership, bus,
            )
            _broadcast_events(steal_agent.id, result.output)
            outputs[steal_agent.id] = result
            completed_ids.add(steal_agent.id)
            prior_outputs[steal_agent.id] = result.output
            if result.success:
                work_queue.complete(stolen.task_id, result.output)
            else:
                failed.append(steal_agent.id)
            _notify(steal_agent.id, steal_agent.role, "done" if result.success else "failed")
        else:
            work_queue.complete(stolen.task_id, "")

    # Save mission state after all layers complete (Task 9 — recovery support).
    # Writes agent outputs to the session store so a crashed mission can be
    # inspected and partial results are not lost.
    try:
        from agent.persistence import session_store
        _store = session_store()
        _store.append_checkpoint(
            label="mission-state",
            summary=(
                f"Mission completed {len(outputs) - len(failed)}/{len(outputs)} agents. "
                f"Failed: {failed or 'none'}. "
                f"Total turns: {sum(r.turns_used for r in outputs.values())}."
            ),
            modified_files=[],
        )
    except Exception:
        pass

    # Stop dashboards before integrator
    if _dash_started:
        try:
            from agent.ui.dashboard import push_event
            push_event("progress", percent=95)
            push_event("timeline", message="Integration phase")
            _dash_update()
        except Exception:
            pass
        _dash_stop()
    if _mc is not None:
        _mc.set_progress(95)
        _mc.stop()
        _mc = None

    # Skip integrator for single-layer or single-agent outcomes
    if n_layers > 1 and len(outputs) > 1:
        int_result = _run_integrator(
            prior_outputs, dag, project_context, user_input, max_turns_per_agent
        )
        outputs["integrator"] = int_result
        final = int_result.output
    else:
        final = next((r.output for r in outputs.values() if r.output), "")

    total_turns = sum(r.turns_used for r in outputs.values())

    # V1.7: collect per-worker stats for utilization / token accounting
    _all_stats = {
        aid: wr.stats
        for aid, wr in outputs.items()
        if wr.stats is not None
    }

    return SchedulerResult(
        success=not failed,
        outputs=outputs,
        final_output=final,
        total_turns=total_turns,
        failed_agents=failed,
        worker_stats=_all_stats,
    )
