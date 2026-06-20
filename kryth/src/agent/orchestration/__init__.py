"""KRYTH Orchestration Engine — public API.

Usage in agent_loop.py:

    from agent.orchestration import orchestrate

    result = orchestrate(
        user_input=user_input,
        project_root=".",
        project_context=session.project_map,
        multi_agent_mode=session.multi_agent_mode,
    )

    if result.approved and result.output:
        # Use result.output as the agent response
        ...
    elif not result.approved:
        # Fall through to single-agent run_inner_loop
        session.multi_agent_mode = result.mode_updated.value
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent.orchestration.approval_gate import ApprovalMode, ApprovalResult, request_approval
from agent.orchestration.capability_graph import build_capability_graph
from agent.orchestration.cost_optimizer import analyze as cost_analyze
from agent.orchestration.intent_engine import analyze_intent
from agent.orchestration.repo_intelligence import RepoProfile, analyze_repo
from agent.orchestration.scheduler import SchedulerResult, run_schedule
from agent.orchestration.task_dag import build_task_dag
from agent.orchestration.team_generator import TeamPlan, generate_team, team_from_contract

# V2: Planner-First — import lazily to avoid circular imports
# from agent.orchestration.project_planner import plan_project, dag_from_plan, team_from_plan


import contextlib as _contextlib


@_contextlib.contextmanager
def _orchestration_view():
    """Take over the main view for the duration of orchestration: suppress the
    shared worker-log stream (clean view) so the scheduler's own team dashboard
    is the single painter. Never starts a competing Rich Live (the scheduler
    already owns one) and never raises into the scheduler."""
    cv = None
    try:
        from agent.ui import clean_view as cv
        cv.set_orchestrated(True)
    except Exception:
        cv = None
    try:
        yield
    finally:
        try:
            if cv is not None:
                cv.set_orchestrated(False)
        except Exception:
            pass


@dataclass
class OrchestrationResult:
    """Full result of the orchestration pipeline."""
    # Did the user approve multi-agent?
    approved: bool
    # Final output string (non-empty when approved and execution succeeded)
    output: str = ""
    # Updated approval mode (SESSION_APPROVED or ALWAYS_SINGLE may change)
    mode_updated: ApprovalMode = ApprovalMode.ASK
    # Full detail objects for introspection / tests
    team: Optional[TeamPlan] = None
    scheduler_result: Optional[SchedulerResult] = None
    approval: Optional[ApprovalResult] = None
    # What the user should see about why we made this decision
    explanation: str = ""
    # V2: Planner-First — the project plan that drove team/DAG generation
    project_plan: "Optional[object]" = None   # ProjectPlan | None


def orchestrate(
    user_input: str,
    project_root: str = ".",
    project_context: str = "",
    multi_agent_mode: str = "ASK",
    max_turns_per_agent: int = 80,
    max_workers: int = 8,
    ask_fn=None,           # injectable for tests
    team_contract=None,    # MissionTeamContract from the approved preview (source of truth)
) -> OrchestrationResult:
    """Run the full orchestration pipeline.

    Phases:
      1. Repo Intelligence
      2. Intent Engine
      3. Capability Graph
      4. Task DAG
      5. Team Generation
      6. Cost Optimizer
      7. Human Approval Gate
      8. Dynamic Scheduler  (only if approved)
    """
    # Fast exit: ALWAYS_SINGLE never needs orchestration analysis
    try:
        _mode_check = ApprovalMode(multi_agent_mode.upper())
    except ValueError:
        _mode_check = ApprovalMode.ASK
    if _mode_check == ApprovalMode.ALWAYS_SINGLE:
        return OrchestrationResult(
            approved=False,
            mode_updated=ApprovalMode.ALWAYS_SINGLE,
            explanation="ALWAYS_SINGLE mode — skipping analysis, using single-agent",
        )

    from agent import ui
    import time as _time
    _start_time = _time.monotonic()

    # ── Phase 0.5: Planner-First Architecture (V2) ────────────────────────────
    # Run the Project Planner BEFORE repo scanning, capability graph, or team
    # generation. If it succeeds, the plan becomes the source of truth for:
    #   • DAG structure (one node per module)
    #   • Team composition (one agent per business capability)
    #   • Dependency graph (from the plan, not keyword heuristics)
    #   • Execution mode recommendation
    # If it fails (any exception, parse error, LLM timeout) the existing
    # pipeline runs unchanged — purely additive, zero risk to existing flow.
    _project_plan = None
    _plan_drove_execution = False
    try:
        from agent.env import getenv_bool
        if getenv_bool("KRYTH_PLANNER_FIRST", True):
            ui.llm_waiting("◈ Planning project structure…")
            from agent.orchestration.project_planner import (
                plan_project, dag_from_plan, team_from_plan, mode_from_plan,
                render_plan_panel,
            )
            _project_plan = plan_project(user_input, project_context)
            if _project_plan is not None and not _project_plan.is_trivial:
                ui.muted(
                    f"  ◈ Planner: {_project_plan.project_name} — "
                    f"{len(_project_plan.modules)} modules, "
                    f"mode={_project_plan.recommended_mode.upper()}"
                )
                # Render plan panel before approval
                render_plan_panel(_project_plan)
                # Build DAG + team from plan (replaces heuristic phases 1-5)
                _plan_dag = dag_from_plan(_project_plan)
                _plan_team = team_from_plan(_project_plan, _plan_dag, user_input)
                # Override recommended execution mode from plan structure
                _plan_mode = mode_from_plan(_project_plan)
                # Only orchestrate if plan suggests multi-agent
                if _plan_mode == "direct" or len(_plan_team.agents) <= 1:
                    ui.muted(
                        f"  Planner: single-agent recommended "
                        f"({len(_plan_team.agents)} module) — using DIRECT"
                    )
                    return OrchestrationResult(
                        approved=False,
                        explanation=f"Planner: {_project_plan.project_name} requires single-agent execution",
                        project_plan=_project_plan,
                    )
                # Hand off to approval + scheduler using plan-generated team
                cost_analysis = cost_analyze(_plan_dag, _plan_team)
                _plan_drove_execution = True

                # V5 Phase 8 — Ponytail planner awareness: the Planner LLM call
                # itself is untouched; this is an orchestration-layer decision
                # of NORMAL vs PONYTAIL execution profile, made from the plan's
                # already-computed shape (module/file count) + task language.
                # Only applies when the user has NOT explicitly forced a
                # profile (via /exec, /mode ponytail, or KRYTH_EXEC_PROFILE) —
                # an explicit user choice always wins. Never leaks past this
                # mission: restored in a finally block.
                import os as _os
                _prior_exec_profile = _os.environ.get("KRYTH_EXEC_PROFILE", "")
                _ponytail_auto_set = False
                if not _prior_exec_profile:
                    try:
                        from agent.orchestration.ponytail import classify_task_for_ponytail
                        _decision, _reason = classify_task_for_ponytail(
                            user_input,
                            module_count=len(_project_plan.modules),
                            estimated_files=_project_plan.estimated_files,
                        )
                        if _decision == "ponytail":
                            _os.environ["KRYTH_EXEC_PROFILE"] = "ponytail"
                            _ponytail_auto_set = True
                            ui.muted(f"  ◈ Ponytail mode active — {_reason}")
                    except Exception:
                        pass

                try:
                    # Re-use existing approval gate + scheduler with plan-derived
                    # artifacts. V4: pass project_plan so _execute_team routes
                    # through MilestoneEngine.
                    _plan_orch_result = _execute_team(
                        team=_plan_team,
                        dag=_plan_dag,
                        cost_analysis=cost_analysis,
                        multi_agent_mode=multi_agent_mode,
                        ask_fn=ask_fn,
                        project_context=project_context,
                        user_input=user_input,
                        max_turns_per_agent=max_turns_per_agent,
                        max_workers=max_workers,
                        project_root=project_root,
                        start_time=_start_time,
                        ui=ui,
                        preapproved=(multi_agent_mode.upper() == "SESSION_APPROVED"),
                        project_plan=_project_plan,
                    )
                finally:
                    if _ponytail_auto_set:
                        _os.environ["KRYTH_EXEC_PROFILE"] = _prior_exec_profile

                _plan_orch_result.project_plan = _project_plan
                return _plan_orch_result
    except Exception as _plan_exc:
        # Log but never fail — existing pipeline takes over
        try:
            ui.muted(f"  (Planner unavailable: {type(_plan_exc).__name__} — using heuristic pipeline)")
        except Exception:
            pass
        _project_plan = None

    # --- Phase 0: Speculative pre-loading (background daemon thread) ---
    # Pre-fetch files likely to be needed based on keyword patterns in the prompt.
    # Runs completely in background — never blocks mission start.
    import threading as _threading

    def _speculative_prefetch(input_text: str, root: str) -> None:
        try:
            import os, re
            from pathlib import Path
            from agent.context import IGNORE_DIRS

            lower = input_text.lower()
            patterns = []
            if re.search(r"\bauth|login|jwt|session|oauth\b", lower):
                patterns += ["auth", "middleware", "jwt", "login"]
            if re.search(r"\bpayment|stripe|billing|checkout\b", lower):
                patterns += ["payment", "billing", "stripe", "webhook"]
            if re.search(r"\btest|spec|pytest|jest\b", lower):
                patterns += ["tests", "test_", "spec", "fixtures"]
            if re.search(r"\bdeploy|docker|ci|cd|k8s|kubernetes\b", lower):
                patterns += ["Dockerfile", ".github", "docker-compose", "k8s"]
            if re.search(r"\bdatabase|schema|migration|model\b", lower):
                patterns += ["models", "schema", "migration", "alembic", "prisma"]

            if not patterns:
                return

            # Walk repo and read matching files (lightweight, capped at 10 files)
            preloaded = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
                for fname in filenames:
                    if any(p.lower() in fname.lower() or p.lower() in dirpath.lower()
                           for p in patterns):
                        fpath = os.path.join(dirpath, fname)
                        try:
                            size = os.path.getsize(fpath)
                            if size < 50_000:  # skip huge files
                                preloaded.append(fpath)
                        except OSError:
                            pass
                    if len(preloaded) >= 10:
                        break
                if len(preloaded) >= 10:
                    break
        except Exception:
            pass  # speculative — never fail the mission

    _threading.Thread(
        target=_speculative_prefetch,
        args=(user_input, project_root),
        daemon=True,
        name="kryth-prefetch",
    ).start()

    # --- Phase 1+2: Repo Intelligence + Intent Engine (parallel — independent) ---
    ui.llm_waiting("◈ Scanning repository + analyzing intent…")
    repo: RepoProfile
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="kryth-orch") as _pool:
        _repo_fut = _pool.submit(analyze_repo, project_root)
        _intent_fut = _pool.submit(analyze_intent, user_input)
        try:
            repo = _repo_fut.result(timeout=15)
        except Exception:
            repo = RepoProfile(root=project_root)
        try:
            intent = _intent_fut.result(timeout=10)
        except Exception:
            from agent.orchestration.intent_engine import analyze_intent as _ai
            intent = _ai(user_input)

    # --- Phase 3: Capability Graph ---
    ui.llm_waiting("◈ Mapping capabilities…")
    cap_graph = build_capability_graph(intent, repo, user_input)
    required_caps = cap_graph.required_names()

    # If only one capability and simple intent → don't orchestrate
    if len(required_caps) <= 1 and not intent.is_compound:
        return OrchestrationResult(
            approved=False,
            output="",
            explanation="Single-capability task — single-agent is more efficient",
        )

    # --- Phase 4+4b: Task DAG + Experience Search (parallel — both need only user_input) ---
    ui.llm_waiting(f"◈ Building task graph ({len(required_caps)} capabilities)…")
    experience_team_hint = None

    def _search_experience():
        try:
            from agent.experience import get_experience
            exp = get_experience(project_root)
            similar = exp.search(user_input, top_k=3)
            if similar.has_high_confidence(threshold=0.60):
                team_rec = exp.recommend("team", user_input)
                if team_rec and team_rec.confidence >= 0.60:
                    return team_rec
        except Exception:
            pass
        return None

    def _build_dag():
        return build_task_dag(
            name=user_input[:60],
            capabilities_required=required_caps,
            repo_root=project_root,
            user_input=user_input,
            repo_profile=repo,
        )

    with _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="kryth-orch") as _pool2:
        _dag_fut = _pool2.submit(_build_dag)
        _exp_fut = _pool2.submit(_search_experience)
        try:
            dag = _dag_fut.result(timeout=15)
        except Exception:
            dag = build_task_dag(
                name=user_input[:60],
                capabilities_required=required_caps,
                repo_root=project_root,
                user_input=user_input,
            )
        try:
            experience_team_hint = _exp_fut.result(timeout=10)
            if experience_team_hint:
                ui.muted(f"  (experience match found — confidence {experience_team_hint.confidence:.0%})")
        except Exception:
            pass

    # If DAG has only one node → single agent
    if len(dag.nodes) <= 1:
        return OrchestrationResult(
            approved=False,
            output="",
            explanation="Single-node task DAG — single-agent execution",
        )

    # --- Phase 5: Team Generation ---
    # If the user approved a Mission Execution Preview, that organization is the
    # SINGLE SOURCE OF TRUTH — build the exact teams shown, bypassing fresh
    # generation, scaling, and experience overrides. Preview == execution.
    _contract_teams = (getattr(team_contract, "teams", None)
                       if team_contract is not None else None)
    if _contract_teams:
        team = team_from_contract(team_contract, user_input=user_input)
        ui.muted(f"  ◈ Spawning {len(team.agents)} teams from approved mission preview")
        # honor the approved plan verbatim — skip scaling / experience / re-gen
        cost_analysis = cost_analyze(dag, team)
        return _execute_team(
            team=team, dag=dag, cost_analysis=cost_analysis,
            multi_agent_mode=multi_agent_mode, ask_fn=ask_fn,
            project_context=project_context, user_input=user_input,
            max_turns_per_agent=max_turns_per_agent, max_workers=max_workers,
            project_root=project_root, start_time=_start_time, ui=ui,
            preapproved=True,
        )

    ui.llm_waiting(f"◈ Building team for {len(dag.nodes)}-node DAG…")
    try:
        team = generate_team(dag, user_input=user_input, repo_profile=repo)
    except Exception:
        team = generate_team(dag)

    # --- Phase 5b: Dynamic Team Scaling ---
    # Explode large-domain agents into N parallel sub-agents with non-overlapping ownership
    try:
        from agent.orchestration.team_scaler import scale_team
        team = scale_team(team, dag, project_root, user_input)
        if any("#" in a.role for a in team.agents):
            ui.muted(f"  ◈ Scaled to {len(team.agents)} agents via domain partitioning")
    except Exception:
        pass  # scaling is optional — proceed with original team

    # Override with experience hint if more confident than fresh generation
    if experience_team_hint and experience_team_hint.confidence >= 0.70:
        from agent.orchestration.team_generator import AgentRole
        hint_agents = [
            AgentRole(
                id=role.lower().replace(" ", "_"),
                role=role,
                mission=f"Complete {role} tasks for: {user_input[:80]}",
                task_node_ids=[],
                max_turns=80,
            )
            for role in experience_team_hint.roles
        ]
        if hint_agents:
            team.agents = hint_agents
            team.recommended_strategy = experience_team_hint.strategy
            team.reasoning = f"Experience-guided: {experience_team_hint.reasoning}"
            ui.muted(f"  (using proven team from history: {experience_team_hint.strategy})")

    # If only one agent needed → don't orchestrate
    if len(team.agents) <= 1:
        return OrchestrationResult(
            approved=False,
            output="",
            team=team,
            explanation="Only one agent role needed — single-agent execution",
        )

    # --- Phase 6: Cost Optimizer ---
    ui.llm_waiting(f"◈ Analyzing {len(team.agents)}-agent plan…")
    cost_analysis = cost_analyze(dag, team)

    # --- Phase 7: Human Approval Gate ---
    try:
        mode = ApprovalMode(multi_agent_mode.upper())
    except ValueError:
        mode = ApprovalMode.ASK

    approval = request_approval(
        dag, team, cost_analysis, mode, ask_fn=ask_fn
    )

    if not approval.approved:
        return OrchestrationResult(
            approved=False,
            mode_updated=approval.mode_updated,
            approval=approval,
            team=team,
            explanation=approval.explanation,
        )

    # --- Phase 8: Dynamic Scheduler ---
    ui.muted(f"  Orchestrating {len(team.agents)} agents via {team.recommended_strategy} strategy…")

    with _orchestration_view():
        sched = run_schedule(
            dag=dag,
            team=team,
            strategy=team.recommended_strategy,
            project_context=project_context,
            user_input=user_input,
            max_turns_per_agent=max_turns_per_agent,
            max_workers=max_workers,
        )

    # --- Phase 8b: Mission Summary ---
    _duration = _time.monotonic() - _start_time
    try:
        from agent.ui.mission_control import render_mission_summary
        render_mission_summary(
            goal=user_input[:60],
            duration_s=_duration,
            agents_used=len(team.agents),
            peak_parallel=max(
                (len([a for a in team.agents if a.dependencies == []]) +
                 len([a for a in team.agents if a.dependencies])), 1
            ),
            files_read=0,      # not tracked here — future metric
            files_modified=0,
            commands=0,
            tests_run=0,
            cache_hits=0,
            repairs=len(sched.failed_agents),
            context_saves=0,
            parallel_speedup=round(team.parallel_benefit, 1),
            test_cache_saved=0.0,
        )
    except Exception:
        pass

    # --- Phase 9: Record experience for future runs ---
    try:
        from agent.experience import get_experience
        exp = get_experience(project_root)
        success = not sched.failed_agents
        exp.learn(
            "team",
            task_description=user_input,
            roles=[a.role for a in team.agents],
            strategy=team.recommended_strategy,
            execution_turns=sched.total_turns,
            repair_count=len(sched.failed_agents),
            merge_conflicts=0,
            success=success,
        )
        exp.learn(
            "parallel",
            task_type=user_input[:60],
            agent_count=len(team.agents),
            parallel_depth=team.layer_count,
            execution_turns=sched.total_turns,
            conflict_count=0,
            merge_problems=len(sched.failed_agents),
            success=success,
        )
    except Exception:
        pass

    return OrchestrationResult(
        approved=True,
        output=sched.final_output,
        mode_updated=approval.mode_updated,
        team=team,
        scheduler_result=sched,
        approval=approval,
        explanation=f"Completed via {team.recommended_strategy} — {len(team.agents)} agents",
    )


def _execute_team(*, team, dag, cost_analysis, multi_agent_mode, ask_fn,
                  project_context, user_input, max_turns_per_agent, max_workers,
                  project_root, start_time, ui, preapproved=False,
                  project_plan=None) -> "OrchestrationResult":
    """Run approval → V4 MilestoneEngine (default) or V2 scheduler (fallback) → record.

    V4 default: when project_plan is provided and KRYTH_V2_COMPAT is not set,
    execution routes through MilestoneEngine.run() which enforces:
      Milestone → Team Lead Review → Contract Validation → Planner Review → Checkpoint
    V2 compat: set KRYTH_V2_COMPAT=1 to use the flat run_schedule() path.
    """
    import time as _time
    try:
        mode = ApprovalMode(multi_agent_mode.upper())
    except ValueError:
        mode = ApprovalMode.ASK
    if preapproved:
        mode = ApprovalMode.SESSION_APPROVED

    approval = request_approval(dag, team, cost_analysis, mode, ask_fn=ask_fn)
    if not approval.approved:
        return OrchestrationResult(
            approved=False, mode_updated=approval.mode_updated, approval=approval,
            team=team, explanation=approval.explanation)

    # ── V4: MilestoneEngine path (default when plan is available) ────────────
    from agent.env import getenv_bool as _getenv_bool
    _use_milestone = (
        project_plan is not None
        and not _getenv_bool("KRYTH_V2_COMPAT", False)
    )

    if _use_milestone:
        ui.muted(
            f"  ◈ V4 Milestone Execution: {len(team.agents)} agents, "
            f"{len(project_plan.ensure_structured_milestones())} milestones"
        )
        sched = _run_milestone_engine(
            plan=project_plan,
            dag=dag,
            team=team,
            project_context=project_context,
            user_input=user_input,
            max_turns_per_agent=max_turns_per_agent,
            max_workers=max_workers,
            project_root=project_root,
            ui=ui,
        )
    else:
        # V2 compat: flat DAG scheduler
        if project_plan is not None:
            ui.muted(f"  (KRYTH_V2_COMPAT — using flat scheduler)")
        ui.muted(f"  Orchestrating {len(team.agents)} agents via {team.recommended_strategy} strategy…")
        with _orchestration_view():
            sched = run_schedule(
                dag=dag, team=team, strategy=team.recommended_strategy,
                project_context=project_context, user_input=user_input,
                max_turns_per_agent=max_turns_per_agent, max_workers=max_workers,
            )

    _duration = _time.monotonic() - start_time
    try:
        from agent.ui.mission_control import render_mission_summary
        render_mission_summary(
            goal=user_input[:60], duration_s=_duration, agents_used=len(team.agents),
            peak_parallel=max(len(team.agents), 1), files_read=0, files_modified=0,
            commands=0, tests_run=0, cache_hits=0, repairs=len(sched.failed_agents),
            context_saves=0, parallel_speedup=round(team.parallel_benefit, 1),
            test_cache_saved=0.0)
    except Exception:
        pass
    try:
        from agent.experience import get_experience
        exp = get_experience(project_root)
        success = not sched.failed_agents
        exp.learn("team", task_description=user_input, roles=[a.role for a in team.agents],
                  strategy=team.recommended_strategy, execution_turns=sched.total_turns,
                  repair_count=len(sched.failed_agents), merge_conflicts=0, success=success)
    except Exception:
        pass

    return OrchestrationResult(
        approved=True, output=sched.final_output, mode_updated=approval.mode_updated,
        team=team, scheduler_result=sched, approval=approval,
        explanation=f"Completed via {'V4 milestones' if _use_milestone else team.recommended_strategy} — {len(team.agents)} agents")


def _run_milestone_engine(
    *, plan, dag, team, project_context, user_input,
    max_turns_per_agent, max_workers, project_root, ui,
) -> "SchedulerResult":
    """Execute plan via MilestoneEngine and return a SchedulerResult.

    Handles milestone-level checkpoint resume (Phase 8): reads the session
    store for the last completed milestone order and resumes from there.
    Falls back to run_schedule() on any exception — execution never stops.
    """
    from agent.orchestration.scheduler import SchedulerResult, WorkerResult

    # Phase 8: detect resume point from checkpoint store
    resume_from_order = 1
    try:
        from agent.persistence import session_store
        store = session_store()
        checkpoints = store.list_checkpoints() if hasattr(store, "list_checkpoints") else []
        ms_labels = [c for c in (checkpoints or []) if "milestone-" in str(c)]
        if ms_labels:
            last = max(
                int(str(c).split("milestone-")[-1].split("-")[0])
                for c in ms_labels
                if str(c).split("milestone-")[-1].split("-")[0].isdigit()
            )
            resume_from_order = last + 1
            if resume_from_order > 1:
                ui.muted(f"  ◈ V4 Recovery: resuming from Milestone {resume_from_order}")
    except Exception:
        pass

    # V5 Ponytail Phase 9: mode banner shown once, up front, when active.
    try:
        from agent.production.execution_profiles import active_profile, is_ponytail
        if is_ponytail(active_profile()):
            from agent.ui.ops_center import ponytail_mode_banner
            ponytail_mode_banner()
    except Exception:
        pass

    # V5/V6 Intelligence — route through run_v5 when KRYTH_V5_INTELLIGENCE is
    # enabled (default: on). Disable with KRYTH_V4_ONLY=1.
    try:
        from agent.env import getenv_bool as _gb
        _v5_on = _gb("KRYTH_V5_INTELLIGENCE", True) and not _gb("KRYTH_V4_ONLY", False)
        if _v5_on:
            from agent.orchestration.v5_runtime import run_v5
            # V6 Phase 1: compute the right execution tier from plan shape —
            # no extra LLM call, uses the already-built plan's module/file count.
            _v6_tier = 4  # default: ENTERPRISE (full V5 stack)
            try:
                from agent.orchestration.execution_layer_selector import select_tier, tier_from_env
                _env_t = tier_from_env()
                if _env_t is not None:
                    _v6_tier = _env_t
                else:
                    _td = select_tier(plan=plan, user_input=user_input)
                    _v6_tier = int(_td.tier)
                    ui.muted(f"  ◈ V6 tier: {_td.tier.name} ({_td.reason})")
            except Exception:
                pass

            v5_report = run_v5(
                plan=plan, dag=dag, team=team,
                project_context=project_context, user_input=user_input,
                max_turns_per_agent=max_turns_per_agent, max_workers=max_workers,
                project_root=project_root, resume_from_order=resume_from_order,
                tier=_v6_tier,
            )
            mission_result = v5_report.mission_result
            # Log V5/V6 summaries (plain muted lines — no new dashboard)
            try:
                if v5_report.quality_report:
                    ui.muted(f"\n{v5_report.quality_report.summary()}")
                if v5_report.org_health_report:
                    ui.muted(f"\n{v5_report.org_health_report.summary()}")
                if v5_report.forecast:
                    ui.muted(f"  {v5_report.forecast.summary()}")
                if v5_report.recovery_attempts:
                    ui.muted(f"  {v5_report.recovery_summary}")
                if v5_report.blocker_report and v5_report.blocker_report.active_blockers:
                    ui.muted(f"\n{v5_report.blocker_report.summary()}")
                # V6 summaries
                if v5_report.token_summary and v5_report.token_summary.total_tokens > 0:
                    ui.muted(f"  {v5_report.token_summary.summary()}")
                if v5_report.overhead_report:
                    ui.muted(f"  {v5_report.overhead_report.summary()}")
                if v5_report.budget_status and v5_report.budget_status.budget_usd is not None:
                    ui.muted(f"  {v5_report.budget_status.summary()}")
            except Exception:
                pass
            # Also render V4 org dashboard using the mission result
            try:
                from agent.ui.ops_center import v4_org_dashboard
                v4_org_dashboard(mission_result, plan)
            except Exception:
                pass
            # Convert to SchedulerResult and return (same conversion as the V4 path below)
            from agent.orchestration.scheduler import SchedulerResult, WorkerResult
            outputs: dict = {}
            failed: list = []
            total_turns = 0
            for ms in mission_result.milestones:
                for mod_name, output_text in ms.worker_outputs.items():
                    wr = WorkerResult(agent_id=mod_name, role=f"{mod_name} Team",
                                      success=ms.success and ms.approved, output=output_text)
                    outputs[mod_name] = wr
                    if not (ms.success and ms.approved):
                        failed.append(mod_name)
                    total_turns += ms.deliverables_completed
            final_output = "\n\n".join(
                f"[{ms.milestone_name}]\n" + "\n".join(f"{k}: {v[:200]}" for k, v in ms.worker_outputs.items())
                for ms in mission_result.milestones if ms.worker_outputs
            ) or mission_result.scorecard()
            return SchedulerResult(success=mission_result.success, outputs=outputs,
                                   final_output=final_output, total_turns=total_turns,
                                   failed_agents=failed)
    except Exception as _v5_exc:
        try:
            ui.muted(f"  (V5 intelligence unavailable: {type(_v5_exc).__name__} — using V4 engine)")
        except Exception:
            pass

    try:
        from agent.orchestration.milestone_engine import MilestoneEngine

        def _on_milestone_done(name: str, ms_result) -> None:
            # Phase 7: render org dashboard update after each milestone
            try:
                from agent.ui.ops_center import v4_milestone_progress
                v4_milestone_progress(name, ms_result)
            except Exception:
                pass

        engine = MilestoneEngine(
            plan=plan,
            dag=dag,
            team=team,
            project_context=project_context,
            user_input=user_input,
            max_turns_per_agent=max_turns_per_agent,
            max_workers=max_workers,
            project_root=project_root,
            on_milestone_complete=_on_milestone_done,
            resume_from_order=resume_from_order,
        )

        with _orchestration_view():
            mission_result = engine.run()

        # Phase 7: final org dashboard
        try:
            from agent.ui.ops_center import v4_org_dashboard
            v4_org_dashboard(mission_result, plan)
        except Exception:
            pass

        # V5 Ponytail Phase 9: execution summary — only renders when the
        # ledger is non-empty (i.e. the PONYTAIL profile was actually active
        # for at least one module this mission).
        try:
            py_stats = engine.ponytail_summary()
            if py_stats:
                from agent.ui.ops_center import ponytail_report
                ponytail_report(
                    files_created=py_stats["files_created"],
                    files_reused=py_stats["files_reused"],
                    files_avoided=py_stats["files_avoided"],
                    abstractions_avoided=py_stats["abstractions_avoided"],
                    dependencies_reused=py_stats["dependencies_reused"],
                    overengineering_score=py_stats["overengineering_score"],
                    grade=py_stats["grade"],
                    complexity_added=py_stats["complexity_added"],
                    complexity_avoided=py_stats["complexity_avoided"],
                )
        except Exception:
            pass

        # Convert MissionDeliveryResult → SchedulerResult
        outputs: dict = {}
        failed: list = []
        total_turns = 0
        for ms in mission_result.milestones:
            for mod_name, output_text in ms.worker_outputs.items():
                role = f"{mod_name} Team"
                success = ms.success and ms.approved
                wr = WorkerResult(
                    agent_id=mod_name,
                    role=role,
                    success=success,
                    output=output_text,
                )
                outputs[mod_name] = wr
                if not success:
                    failed.append(mod_name)
                total_turns += ms.deliverables_completed

        final_output = "\n\n".join(
            f"[{ms.milestone_name}]\n" + "\n".join(
                f"{k}: {v[:200]}" for k, v in ms.worker_outputs.items()
            )
            for ms in mission_result.milestones if ms.worker_outputs
        )
        if not final_output:
            final_output = mission_result.scorecard()

        return SchedulerResult(
            success=mission_result.success,
            outputs=outputs,
            final_output=final_output,
            total_turns=total_turns,
            failed_agents=failed,
        )

    except Exception as exc:
        # Graceful fallback to flat scheduler — execution never aborts
        try:
            ui.warn(f"  MilestoneEngine error ({type(exc).__name__}) — falling back to scheduler")
        except Exception:
            pass
        with _orchestration_view():
            return run_schedule(
                dag=dag, team=team, strategy=team.recommended_strategy,
                project_context=project_context, user_input=user_input,
                max_turns_per_agent=max_turns_per_agent, max_workers=max_workers,
            )


__all__ = [
    "orchestrate",
    "OrchestrationResult",
    "ApprovalMode",
]
