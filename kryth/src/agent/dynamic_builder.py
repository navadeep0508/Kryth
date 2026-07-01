"""Dynamic Agent Builder — creates agents based on actual work components.

Agents are created dynamically based on the specific components identified
in the task analysis, not from fixed presets.
"""

from __future__ import annotations

import time
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable

from agent import ui

from agent.task_analyzer import TaskAnalysis, WorkComponent
from agent.execution_strategy import StrategyDecision


@dataclass
class BuildResult:
    """Result of a dynamic build."""
    success: bool
    output: str
    components_completed: List[str]
    failed_components: List[str]
    total_turns: int


# ---------------------------------------------------------------------------
# Agent prompt construction
# ---------------------------------------------------------------------------

def _build_agent_prompt(component: WorkComponent, project_context: str, skill_context: str) -> str:
    """Build a focused prompt for a specific work component."""
    prompt = f"""You are a specialized agent focused on: {component.name}

TASK: {component.description}

WORKING CONTEXT:
- Project context: {project_context[:2000] if project_context else 'None'}
- Skill context: {skill_context[:1000] if skill_context else 'None'}

INSTRUCTIONS:
1. Focus exclusively on your assigned component
2. Create the files and functionality described
3. Follow best practices and write complete, working code
4. Do not work on other components
5. Report completion with a summary of what was built

Output format: Start with "COMPONENT COMPLETE: <component name>" then provide a brief summary.
"""
    return prompt


def _build_integration_prompt(
    spec: "DynamicBuildSpec",
    component_outputs: Dict[str, str],
    user_input: str
) -> str:
    """Build prompt for integration phase."""
    outputs_text = "\n\n".join([
        f"=== {comp_id} ===\n{output}"
        for comp_id, output in component_outputs.items()
    ])
    
    prompt = f"""You are the integrator. Combine all components into a cohesive project.

USER REQUEST: {user_input}

COMPONENT OUTPUTS:
{outputs_text}

INTEGRATION NOTES: {spec.integration_notes}

INSTRUCTIONS:
1. Review all component outputs
2. Identify any missing connections or conflicts
3. Create any necessary glue code, configuration files, or documentation
4. Ensure all parts work together
5. Provide final project structure and setup instructions

Output format: Start with "INTEGRATION COMPLETE" then provide final summary and setup steps.
"""
    return prompt


@dataclass
class DynamicBuildSpec:
    """Specification for a dynamic build."""
    user_input: str
    project_name: str
    components: List[WorkComponent]
    strategy: StrategyDecision
    integration_notes: str = ""
    project_context: str = ""
    skill_context: str = ""


# ---------------------------------------------------------------------------
# Main dynamic builder
# ---------------------------------------------------------------------------

def run_dynamic_build(
    user_input: str,
    strategy: StrategyDecision,
    components: List[WorkComponent],
    *,
    project_context: str = "",
    skill_context: str = "",
    max_turns_per_agent: int = 60,
    on_progress: Optional[Callable[[str, str], None]] = None,
) -> Optional[str]:
    """Execute a dynamic build with the given strategy."""
    
    if not components:
        return None
    
    # Build specification
    spec = DynamicBuildSpec(
        user_input=user_input,
        project_name=_extract_project_name(user_input),
        components=components,
        strategy=strategy,
        integration_notes=_generate_integration_notes(components),
        project_context=project_context,
        skill_context=skill_context,
    )
    
    # Execute based on strategy
    if strategy.strategy == "single":
        return _run_single_agent(spec, max_turns_per_agent)
    elif strategy.strategy == "sequential":
        return _run_sequential(spec, max_turns_per_agent, on_progress)
    else:
        ui.error(f"Unknown strategy: {strategy.strategy}")
        return None


def _extract_project_name(user_input: str) -> str:
    """Extract a project name from user input."""
    # Try to get the thing being built
    match = re.search(
        r'\b(?:build|create|make|generate|write|develop|design)\s+(?:a\s+|an\s+)?(.+?)(?:\s+with|\s+using|\s+in|\s+that|$)',
        user_input, re.I
    )
    if match:
        name = match.group(1).strip()
        return name[:40] if name else "Project"
    return "Project"


def _generate_integration_notes(components: List[WorkComponent]) -> str:
    """Generate integration notes based on components."""
    if len(components) <= 1:
        return "Single component - no integration needed."
    
    comp_names = [c.name for c in components]
    return f"Integrate components: {', '.join(comp_names)}. Ensure proper interfaces and data flow between them."


# ---------------------------------------------------------------------------
# Execution modes
# ---------------------------------------------------------------------------

def _run_single_agent(spec: DynamicBuildSpec, max_turns: int) -> str:
    """Run a single agent for the entire task."""
    component = spec.components[0]
    prompt = _build_agent_prompt(component, spec.project_context, spec.skill_context)
    
    ui.info(f"  Running single agent: {component.name}")
    result = _run_agent(component, prompt, max_turns)
    return result


def _run_sequential(
    spec: DynamicBuildSpec,
    max_turns: int,
    on_progress: Optional[Callable[[str, str], None]]
) -> str:
    """Run agents sequentially in dependency order."""
    from agent.execution_strategy import ExecutionStrategyDecider
    
    # Sort by dependencies
    sorted_components = ExecutionStrategyDecider()._topological_sort(spec.components)
    
    outputs = {}
    total_turns = 0
    
    for component in sorted_components:
        # Wait for dependencies to complete
        for dep in component.dependencies:
            while dep not in outputs:
                time.sleep(0.05)
        
        prompt = _build_agent_prompt(component, spec.project_context, spec.skill_context)
        ui.info(f"  Running sequential agent: {component.name} (depends on: {component.dependencies or 'none'})")
        
        result = _run_agent(component, prompt, max_turns)
        outputs[component.id] = result
        total_turns += component.estimated_turns
        
        if on_progress:
            on_progress(component.id, "done")
    
    # Integrate
    ui.info("  Integrating sequential components...")
    int_prompt = _build_integration_prompt(spec, outputs, spec.user_input)
    int_component = WorkComponent(
        id="integrator",
        name="Integrator",
        description="Integrate all sequential components"
    )
    final = _run_agent(int_component, int_prompt, max_turns)
    return final


def _run_agent(component: WorkComponent, prompt: str, max_turns: int) -> str:
    """Run a single agent with the given prompt."""
    from agent.tools._subagent import _build_nested
    from agent.agent_loop import run_inner_loop
    from agent.session import push_session, pop_session, get_session
    
    parent = get_session()
    nested = _build_nested(component.name, prompt, parent.depth)
    
    token = push_session(nested)
    try:
        result = run_inner_loop(nested, max_turns, verbose_usage=False)
        # Extract content from result
        if hasattr(result, 'content'):
            return result.content or ""
        return str(result)
    finally:
        pop_session(token)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_dynamic_build_with_approval(
    user_input: str,
    analysis: TaskAnalysis,
    strategy: StrategyDecision,
    *,
    project_context: str = "",
    skill_context: str = "",
    max_turns_per_agent: int = 60,
    on_progress: Optional[Callable[[str, str], None]] = None,
) -> Optional[str]:
    """Run dynamic build with user approval if required."""
    
    if strategy.requires_approval:
        ui.info("\n" + "="*60)
        ui.info("EXECUTION PLAN")
        ui.info("="*60)
        ui.info(f"Strategy: {strategy.strategy}")
        ui.info(f"Agents: {len(strategy.agents)}")
        for agent in strategy.agents:
            ui.info(f"  • {agent.name}: {agent.description[:80]}...")
        ui.info(f"Estimated time: {strategy.estimated_time:.0f} turns")
        ui.info(f"Estimated cost: {strategy.estimated_cost:.0f} turn units")
        ui.info("="*60)
        
        if not ui.ask_yes_no("Proceed with execution?"):
            ui.info("Execution cancelled by user.")
            return None
    
    return run_dynamic_build(
        user_input=user_input,
        strategy=strategy,
        components=analysis.components,
        project_context=project_context,
        skill_context=skill_context,
        max_turns_per_agent=max_turns_per_agent,
        on_progress=on_progress,
    )