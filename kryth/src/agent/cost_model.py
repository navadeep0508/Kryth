"""Dynamic Cost Model — estimates execution costs for different strategies.

This module provides a pluggable cost model that estimates the total cost
(compute, time, resources) for different execution strategies. The model
adapts based on task characteristics, system resources, and workload.

Cost factors:
- Agent startup overhead (initialization, context loading)
- Context transfer size (tokens passed between agents)
- Result integration complexity
- Model latency and throughput
- System resource constraints (memory, CPU)
- Parallel coordination overhead

The cost model is configurable and can be extended with custom estimators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable, Protocol
from abc import ABC, abstractmethod
from agent.task_analyzer import TaskAnalysis, WorkComponent


@dataclass
class CostParameters:
    """Parameters that influence cost estimation."""
    # Model characteristics
    avg_tokens_per_turn: float = 2000.0  # average tokens processed per turn
    model_latency_ms: float = 500.0  # average latency per turn
    context_window: int = 128000  # max context size in tokens
    
    # System resources
    available_memory_mb: float = 8192.0  # available RAM for agents
    agent_memory_overhead_mb: float = 500.0  # memory per agent
    max_concurrent_agents: int = 4  # hardware limit on parallel agents
    
    # Workload characteristics
    avg_file_size_kb: float = 10.0  # average file size created
    context_per_file_tokens: float = 500.0  # context needed per file in project
    
    # Overhead factors
    agent_startup_cost_turns: float = 10.0  # turns equivalent for agent startup
    context_transfer_cost_per_agent_turns: float = 3.0  # turns for context passing
    result_merge_base_cost_turns: float = 15.0  # base cost to merge results
    coordination_overhead_per_agent_turns: float = 2.0  # parallel coordination per agent
    
    # Adaptive factors
    parallelism_speedup_factor: float = 0.8  # efficiency of parallel execution (0-1)
    dependency_sequential_factor: float = 1.0  # no speedup for sequential deps


class CostEstimator(ABC):
    """Abstract interface for cost estimation components."""
    
    @abstractmethod
    def estimate_agent_cost(self, component: WorkComponent, context_size: int) -> float:
        """Estimate cost (in turns) for an agent to complete a component."""
        pass
    
    @abstractmethod
    def estimate_startup_cost(self) -> float:
        """Estimate cost to start a new agent."""
        pass
    
    @abstractmethod
    def estimate_context_transfer_cost(self, num_agents: int, context_size: int) -> float:
        """Estimate cost for context transfer between agents."""
        pass
    
    @abstractmethod
    def estimate_merge_cost(self, num_agents: int, total_work_size: int) -> float:
        """Estimate cost to merge results from multiple agents."""
        pass
    
    @abstractmethod
    def estimate_coordination_cost(self, num_agents: int, has_dependencies: bool) -> float:
        """Estimate coordination overhead for parallel/sequential execution."""
        pass


class DefaultCostEstimator(CostEstimator):
    """Default cost estimator using configurable parameters."""
    
    def __init__(self, params: CostParameters):
        self.params = params
    
    def estimate_agent_cost(self, component: WorkComponent, context_size: int = 0) -> float:
        """Estimate cost for a single agent to complete a component."""
        base = component.estimated_turns

        # Adjust for context size: larger context may require more turns for management
        context_factor = 1.0 + (context_size / self.params.context_window) * 0.1

        # Adjust for output complexity (more files = more work)
        output_paths = getattr(component, "output_paths", []) or []
        file_factor = 1.0 + (len(output_paths) * 0.05)

        return base * context_factor * file_factor
    
    def estimate_startup_cost(self) -> float:
        """Cost to initialize a new agent."""
        return self.params.agent_startup_cost_turns
    
    def estimate_context_transfer_cost(self, num_agents: int, context_size: int) -> float:
        """Cost to transfer context between agents."""
        # Each agent needs to receive context from coordinator
        per_agent = self.params.context_transfer_cost_per_agent_turns
        # Larger context increases cost
        context_factor = 1.0 + (context_size / self.params.context_window) * 0.2
        return num_agents * per_agent * context_factor
    
    def estimate_merge_cost(self, num_agents: int, total_work_size: int) -> float:
        """Cost to integrate results from multiple agents."""
        base = self.params.result_merge_base_cost_turns
        # More agents increases merge complexity
        agent_factor = 1.0 + (num_agents - 1) * 0.3
        # More total work increases merge effort
        work_factor = 1.0 + (total_work_size / 1000) * 0.1
        return base * agent_factor * work_factor
    
    def estimate_coordination_cost(self, num_agents: int, has_dependencies: bool) -> float:
        """Coordination overhead for multi-agent execution."""
        if num_agents <= 1:
            return 0.0
        
        if has_dependencies:
            # Sequential: coordination is minimal (just waiting)
            base = num_agents * 2.0  # small overhead per agent
        else:
            # Parallel: need synchronization, result collection
            base = num_agents * self.params.coordination_overhead_per_agent_turns
        
        # System resource constraints add overhead
        if num_agents > self.params.max_concurrent_agents:
            resource_penalty = (num_agents - self.params.max_concurrent_agents) * 5.0
            base += resource_penalty
        
        return base


@dataclass
class StrategyCosts:
    """Total cost breakdown for a strategy."""
    total_turns: float
    agent_costs: List[float]
    startup_cost: float
    context_transfer_cost: float
    merge_cost: float
    coordination_cost: float
    estimated_time_seconds: float
    estimated_memory_mb: float
    reasoning: str = ""


class AdaptiveCostModel:
    """Adaptive cost model that estimates execution costs for different strategies."""
    
    def __init__(self, estimator: CostEstimator, params: CostParameters):
        self.estimator = estimator
        self.params = params
    
    def estimate_strategy_costs(
        self,
        analysis: TaskAnalysis,
        component_groups: Optional[List[List[WorkComponent]]] = None
    ) -> Dict[str, StrategyCosts]:
        """Estimate costs for all possible strategies."""
        
        if component_groups is None:
            # Default: each component as separate unit, or all merged
            if len(analysis.components) == 1:
                component_groups = [analysis.components]
            else:
                # Options: all separate, or all merged
                component_groups = [
                    analysis.components,  # all merged into one
                    [[c] for c in analysis.components]  # each separate
                ]
        
        costs = {}
        
        # Single agent (all components merged)
        merged_group = analysis.components
        costs["single"] = self._estimate_cost_for_group(
            merged_group,
            has_dependencies=False,
            is_parallel=False
        )
        
        # Sequential (respecting dependencies)
        if analysis.has_dependencies:
            sequential_groups = self._group_by_dependency_order(analysis.components)
            costs["sequential"] = self._estimate_cost_for_groups_sequential(
                sequential_groups
            )
        else:
            # Without dependencies, sequential is just sum of individual costs
            sequential_groups = [[c] for c in analysis.components]
            costs["sequential"] = self._estimate_cost_for_groups_sequential(
                sequential_groups
            )
        
        # Parallel (only if independent)
        if analysis.can_parallelize:
            parallel_groups = [[c] for c in analysis.components]
            costs["parallel"] = self._estimate_cost_for_groups_parallel(
                parallel_groups
            )
        
        return costs
    
    def _estimate_cost_for_group(
        self,
        components: List[WorkComponent],
        has_dependencies: bool,
        is_parallel: bool
    ) -> StrategyCosts:
        """Estimate cost for a group of components executed as one unit."""
        if not components:
            return StrategyCosts(
                total_turns=float('inf'),
                agent_costs=[],
                startup_cost=0,
                context_transfer_cost=0,
                merge_cost=0,
                coordination_cost=0,
                estimated_time_seconds=0,
                estimated_memory_mb=0,
                reasoning="No components"
            )
        
        # Estimate context size needed (sum of all component contexts)
        context_size = sum(c.estimated_turns * self.params.avg_tokens_per_turn for c in components)
        
        # Agent cost for this group
        group_work = sum(c.estimated_turns for c in components)
        agent_cost = self.estimator.estimate_agent_cost(components[0], context_size)
        
        # For a single agent, we don't have transfer/merge/coordination
        total_turns = group_work
        
        # Estimate memory: one agent + context
        memory_mb = self.params.agent_memory_overhead_mb + (context_size / 1024 / 1024) * 2
        
        # Time: turns * latency
        time_sec = total_turns * (self.params.model_latency_ms / 1000)
        
        return StrategyCosts(
            total_turns=total_turns,
            agent_costs=[agent_cost],
            startup_cost=0,  # only one agent
            context_transfer_cost=0,
            merge_cost=0,
            coordination_cost=0,
            estimated_time_seconds=time_sec,
            estimated_memory_mb=memory_mb,
            reasoning=f"Single agent handles {len(components)} components"
        )
    
    def _estimate_cost_for_groups_sequential(
        self,
        groups: List[List[WorkComponent]]
    ) -> StrategyCosts:
        """Estimate cost for sequential execution of groups."""
        total_turns = 0
        agent_costs = []
        startup_cost = 0
        context_transfer_cost = 0
        coordination_cost = 0
        
        # Context accumulates as we progress
        accumulated_context_size = 0
        
        for i, group in enumerate(groups):
            if not group:
                continue
            
            # Each group after the first needs agent startup
            if i > 0:
                startup_cost += self.estimator.estimate_startup_cost()
                # Context transfer: pass accumulated context to next agent
                context_transfer_cost += self.estimator.estimate_context_transfer_cost(
                    1, accumulated_context_size
                )
            
            # Agent cost for this group
            group_context_size = sum(c.estimated_turns * self.params.avg_tokens_per_turn for c in group)
            group_cost = self.estimator.estimate_agent_cost(group[0], accumulated_context_size + group_context_size)
            agent_costs.append(group_cost)
            total_turns += group_cost
            
            # Update accumulated context (simplified: keep all previous work context)
            accumulated_context_size += group_context_size
        
        # Coordination overhead for sequential is minimal
        coordination_cost = self.estimator.estimate_coordination_cost(len(groups), True)
        
        # Merge cost for sequential (combining results)
        total_work_size = sum(len(g) for g in groups)
        merge_cost = self.estimator.estimate_merge_cost(len(groups), total_work_size)
        total_turns += merge_cost
        
        # Memory: sum of all agents (but they run sequentially, so peak is max)
        peak_memory = len(groups) * self.params.agent_memory_overhead_mb
        
        # Time: sum of all turns * latency
        time_sec = total_turns * (self.params.model_latency_ms / 1000)
        
        reasoning = f"Sequential: {len(groups)} groups, total turns {total_turns:.0f}"
        
        return StrategyCosts(
            total_turns=total_turns,
            agent_costs=agent_costs,
            startup_cost=startup_cost,
            context_transfer_cost=context_transfer_cost,
            merge_cost=merge_cost,
            coordination_cost=coordination_cost,
            estimated_time_seconds=time_sec,
            estimated_memory_mb=peak_memory,
            reasoning=reasoning
        )
    
    def _estimate_cost_for_groups_parallel(
        self,
        groups: List[List[WorkComponent]]
    ) -> StrategyCosts:
        """Estimate cost for parallel execution of groups."""
        if not groups:
            return StrategyCosts(
                total_turns=float('inf'),
                agent_costs=[],
                startup_cost=0,
                context_transfer_cost=0,
                merge_cost=0,
                coordination_cost=0,
                estimated_time_seconds=0,
                estimated_memory_mb=0,
                reasoning="No groups"
            )
        
        # All agents start simultaneously
        num_agents = len(groups)
        
        # Startup cost for all agents
        startup_cost = num_agents * self.estimator.estimate_startup_cost()
        
        # Context transfer: coordinator sends initial context to all agents
        # For parallel, we assume minimal shared context needed
        initial_context_size = sum(
            c.estimated_turns * self.params.avg_tokens_per_turn 
            for group in groups for c in group
        ) / num_agents  # approximate per-agent context
        context_transfer_cost = self.estimator.estimate_context_transfer_cost(
            num_agents, initial_context_size
        )
        
        # Agent costs: each group runs in parallel, time = max(agent_cost)
        agent_costs = []
        for group in groups:
            if not group:
                continue
            group_context_size = sum(c.estimated_turns * self.params.avg_tokens_per_turn for c in group)
            agent_cost = self.estimator.estimate_agent_cost(group[0], group_context_size)
            agent_costs.append(agent_cost)
        
        max_agent_cost = max(agent_costs) if agent_costs else 0
        
        # Coordination overhead for parallel
        coordination_cost = self.estimator.estimate_coordination_cost(num_agents, False)
        
        # Merge cost
        total_work_size = sum(len(g) for g in groups)
        merge_cost = self.estimator.estimate_merge_cost(num_agents, total_work_size)
        
        # Total turns: max agent time + overheads
        total_turns = max_agent_cost + coordination_cost + merge_cost
        
        # Memory: all agents run simultaneously
        memory_mb = num_agents * self.params.agent_memory_overhead_mb
        
        # Time: max agent time + coordination + merge
        time_sec = (max_agent_cost + coordination_cost + merge_cost) * (self.params.model_latency_ms / 1000)
        
        # Speedup calculation
        sequential_time = sum(agent_costs) * (self.params.model_latency_ms / 1000)
        speedup = sequential_time / time_sec if time_sec > 0 else 1
        
        reasoning = (f"Parallel: {num_agents} agents, max cost {max_agent_cost:.0f}, "
                    f"speedup {speedup:.1f}x, total {total_turns:.0f} turns")
        
        return StrategyCosts(
            total_turns=total_turns,
            agent_costs=agent_costs,
            startup_cost=startup_cost,
            context_transfer_cost=context_transfer_cost,
            merge_cost=merge_cost,
            coordination_cost=coordination_cost,
            estimated_time_seconds=time_sec,
            estimated_memory_mb=memory_mb,
            reasoning=reasoning
        )
    
    def _group_by_dependency_order(self, components: List[WorkComponent]) -> List[List[WorkComponent]]:
        """Group components by dependency order (topological layers)."""
        # Simple: each component as its own group, sorted by dependencies
        sorted_components = self._topological_sort(components)
        return [[c] for c in sorted_components]
    
    def _topological_sort(self, components: List[WorkComponent]) -> List[WorkComponent]:
        """Sort components by dependencies."""
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


def create_default_cost_model() -> AdaptiveCostModel:
    """Create a cost model with default parameters."""
    params = CostParameters()
    estimator = DefaultCostEstimator(params)
    return AdaptiveCostModel(estimator, params)