"""Execution Strategy — determines how to execute a task using cost optimization.

This module decides between:
- Single agent: one agent handles the entire task
- Sequential: multiple agents in dependency order
- Parallel: multiple agents running simultaneously (only for independent tasks)

The decision is based on a dynamic cost model that estimates:
- Agent startup overhead
- Context transfer size
- Result integration complexity
- Model latency
- System resource constraints

The goal is to minimize total execution cost while maintaining correctness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict
from agent.task_analyzer import TaskAnalysis, WorkComponent
from agent.cost_model import AdaptiveCostModel, create_default_cost_model


@dataclass
class StrategyDecision:
    """Decision on how to execute a task."""
    strategy: str  # "single", "sequential", "parallel"
    agents: List[AgentConfig]
    reasoning: str
    estimated_cost: float  # relative cost units (turns)
    estimated_time_seconds: float
    estimated_memory_mb: float
    requires_approval: bool = False  # if True, ask user before proceeding


@dataclass
class AgentConfig:
    """Configuration for a single agent."""
    id: str
    name: str
    description: str
    component: WorkComponent
    max_turns: int
    priority: int = 0  # for ordering in sequential execution
    depends_on: List[str] = None  # agent IDs this one depends on
    
    def __post_init__(self):
        if self.depends_on is None:
            self.depends_on = []


class ExecutionStrategyDecider:
    """Decides execution strategy using a dynamic cost model."""
    
    def __init__(self, cost_model: Optional[AdaptiveCostModel] = None):
        self.cost_model = cost_model or create_default_cost_model()
    
    def decide(self, analysis: TaskAnalysis, user_input: str) -> StrategyDecision:
        """Determine the best execution strategy by minimizing total cost."""
        
        # If only one component, single agent is optimal
        if len(analysis.components) == 1:
            return self._single_agent_strategy(analysis)
        
        # Estimate costs for all applicable strategies
        costs = self.cost_model.estimate_strategy_costs(analysis)
        
        # Find the strategy with minimum cost
        best_strategy = min(costs.keys(), key=lambda s: costs[s].total_turns)
        best_costs = costs[best_strategy]
        
        # Build the decision based on best strategy
        if best_strategy == "single":
            return self._single_agent_strategy(analysis)
        elif best_strategy == "sequential":
            return self._sequential_strategy(analysis, best_costs)
        else:  # parallel
            return self._parallel_strategy(analysis, best_costs)
    
    def _single_agent_strategy(self, analysis: TaskAnalysis) -> StrategyDecision:
        """Create a single agent to handle everything."""
        combined_desc = "Complete task"
        if len(analysis.components) > 1:
            combined_desc = "Complete task covering: " + ", ".join(c.name for c in analysis.components)
        else:
            combined_desc = analysis.components[0].description
        
        combined_turns = analysis.estimated_total_turns
        
        agent = AgentConfig(
            id="main",
            name="Main Agent",
            description=combined_desc,
            component=analysis.components[0],
            max_turns=min(combined_turns, 300)
        )
        
        # Get cost estimate for single agent
        costs = self.cost_model.estimate_strategy_costs(analysis)
        single_costs = costs.get("single")
        
        if single_costs:
            estimated_cost = single_costs.total_turns
            estimated_time = single_costs.estimated_time_seconds
            estimated_memory = single_costs.estimated_memory_mb
        else:
            estimated_cost = combined_turns
            estimated_time = combined_turns * (self.cost_model.params.model_latency_ms / 1000)
            estimated_memory = self.cost_model.params.agent_memory_overhead_mb
        
        return StrategyDecision(
            strategy="single",
            agents=[agent],
            reasoning=analysis.reasoning,
            estimated_cost=estimated_cost,
            estimated_time_seconds=estimated_time,
            estimated_memory_mb=estimated_memory,
            requires_approval=False
        )
    
    def _sequential_strategy(self, analysis: TaskAnalysis, costs: StrategyCosts) -> StrategyDecision:
        """Create agents that execute in dependency order."""
        agents = []
        
        # Sort by dependencies
        sorted_components = self._topological_sort(analysis.components)
        
        for i, component in enumerate(sorted_components):
            agent = AgentConfig(
                id=component.id,
                name=component.name,
                description=f"Execute {component.name}: {component.description}",
                component=component,
                max_turns=component.estimated_turns,
                priority=i,
                depends_on=component.dependencies
            )
            agents.append(agent)
        
        return StrategyDecision(
            strategy="sequential",
            agents=agents,
            reasoning=f"Sequential execution with dependencies: {' -> '.join(a.id for a in agents)}",
            estimated_cost=costs.total_turns,
            estimated_time_seconds=costs.estimated_time_seconds,
            estimated_memory_mb=costs.estimated_memory_mb,
            requires_approval=len(agents) > 2  # ask if many agents
        )
    
    def _parallel_strategy(self, analysis: TaskAnalysis, costs: StrategyCosts) -> StrategyDecision:
        """Create agents for parallel execution."""
        agents = []
        
        for component in analysis.components:
            agent = AgentConfig(
                id=component.id,
                name=component.name,
                description=f"Execute {component.name} in parallel: {component.description}",
                component=component,
                max_turns=component.estimated_turns,
                depends_on=[]
            )
            agents.append(agent)
        
        return StrategyDecision(
            strategy="parallel",
            agents=agents,
            reasoning=f"Parallel execution: {len(agents)} agents, cost {costs.total_turns:.0f} turns",
            estimated_cost=costs.total_turns,
            estimated_time_seconds=costs.estimated_time_seconds,
            estimated_memory_mb=costs.estimated_memory_mb,
            requires_approval=True  # always ask for parallel
        )
    
    def _topological_sort(self, components: List[WorkComponent]) -> List[WorkComponent]:
        """Sort components by dependencies (simple Kahn's algorithm)."""
        deps = {c.id: set(c.dependencies) for c in components}
        sorted_list = []
        while deps:
            ready = [cid for cid, cdeps in deps.items() if not cdeps]
            if not ready:
                sorted_list.extend([c for c in components if c.id in deps])
                break
            for cid in ready:
                sorted_list.append(next(c for c in components if c.id == cid))
                del deps[cid]
                for other_deps in deps.values():
                    other_deps.discard(cid)
        return sorted_list


def decide_execution_strategy(analysis: TaskAnalysis, user_input: str) -> StrategyDecision:
    """Convenience function to decide execution strategy."""
    decider = ExecutionStrategyDecider()
    return decider.decide(analysis, user_input)