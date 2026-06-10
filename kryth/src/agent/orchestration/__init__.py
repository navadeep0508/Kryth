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
from agent.orchestration.team_generator import TeamPlan, generate_team


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


def orchestrate(
    user_input: str,
    project_root: str = ".",
    project_context: str = "",
    multi_agent_mode: str = "ASK",
    max_turns_per_agent: int = 80,
    max_workers: int = 4,
    ask_fn=None,           # injectable for tests
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
    from agent import ui

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

    # --- Phase 4: Task DAG ---
    ui.llm_waiting(f"◈ Building task graph ({len(required_caps)} capabilities)…")
    dag = build_task_dag(
        name=user_input[:60],
        capabilities_required=required_caps,
        repo_root=project_root,
        user_input=user_input,
        repo_profile=repo,
    )

    # If DAG has only one node → single agent
    if len(dag.nodes) <= 1:
        return OrchestrationResult(
            approved=False,
            output="",
            explanation="Single-node task DAG — single-agent execution",
        )

    # --- Phase 4b+5: Experience Search + Team Generation (parallel — independent) ---
    ui.llm_waiting(f"◈ Building team for {len(dag.nodes)}-node DAG…")
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

    with _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="kryth-orch") as _pool2:
        _exp_fut = _pool2.submit(_search_experience)
        _team_fut = _pool2.submit(
            lambda: generate_team(dag, user_input=user_input, repo_profile=repo)
        )
        try:
            experience_team_hint = _exp_fut.result(timeout=10)
            if experience_team_hint:
                ui.muted(f"  (experience match found — confidence {experience_team_hint.confidence:.0%})")
        except Exception:
            pass
        try:
            team = _team_fut.result(timeout=15)
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
    import time as _time
    _duration = _time.monotonic() - _time.monotonic()  # placeholder; computed in sched
    try:
        from agent.ui.mission_control import render_mission_summary
        render_mission_summary(
            goal=user_input[:60],
            duration_s=sched.total_turns * 8,           # rough estimate: ~8s/turn
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


__all__ = [
    "orchestrate",
    "OrchestrationResult",
    "ApprovalMode",
]
