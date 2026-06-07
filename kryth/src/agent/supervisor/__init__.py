"""KRYTH Autonomous Execution Supervisor.

The Supervisor sits above the orchestration + terminal engines and
continuously monitors, predicts, intervenes, recovers, and replans
while a mission is executing.

It is the runtime operating system of KRYTH — not a planner, not a
memory system. It is the watchdog that keeps execution alive.

Public API:

    from agent.supervisor import ExecutionSupervisor

    supervisor = ExecutionSupervisor(mission_goal="build the API")
    supervisor.start()
    # ...execution runs...
    supervisor.complete(summary)

    # Direct access to sub-systems:
    from agent.supervisor import (
        health_engine, budget_controller, confidence_controller,
        risk_engine, ownership_enforcer, parallel_optimizer,
        prediction_engine, recovery_manager,
    )
"""

from __future__ import annotations

from agent.supervisor.health import HealthEngine, SystemHealth, health_engine
from agent.supervisor.budget import BudgetController, BudgetSnapshot, budget_controller
from agent.supervisor.confidence import ConfidenceController, ConfidenceDecision, confidence_controller
from agent.supervisor.risk import RiskEngine, RiskScore, risk_engine
from agent.supervisor.ownership import OwnershipEnforcer, ownership_enforcer
from agent.supervisor.parallel_optimizer import ParallelOptimizer, parallel_optimizer
from agent.supervisor.agent_supervisor import AgentSupervisor, AgentHealthStatus, agent_supervisor
from agent.supervisor.terminal_supervisor import TerminalSupervisor, terminal_supervisor
from agent.supervisor.browser_supervisor import BrowserSupervisor, browser_supervisor
from agent.supervisor.prediction_engine import PredictiveEngine, StepPrediction, prediction_engine
from agent.supervisor.replanner import DynamicReplanner, ReplanResult, dynamic_replanner
from agent.supervisor.recovery_manager import RecoveryManager, RecoveryEvent, recovery_manager
from agent.supervisor.supervisor import ExecutionSupervisor, MissionContext


__all__ = [
    # Core supervisor
    "ExecutionSupervisor",
    "MissionContext",
    # Sub-systems
    "HealthEngine", "SystemHealth", "health_engine",
    "BudgetController", "BudgetSnapshot", "budget_controller",
    "ConfidenceController", "ConfidenceDecision", "confidence_controller",
    "RiskEngine", "RiskScore", "risk_engine",
    "OwnershipEnforcer", "ownership_enforcer",
    "ParallelOptimizer", "parallel_optimizer",
    "AgentSupervisor", "AgentHealthStatus", "agent_supervisor",
    "TerminalSupervisor", "terminal_supervisor",
    "BrowserSupervisor", "browser_supervisor",
    "PredictiveEngine", "StepPrediction", "prediction_engine",
    "DynamicReplanner", "ReplanResult", "dynamic_replanner",
    "RecoveryManager", "RecoveryEvent", "recovery_manager",
]
