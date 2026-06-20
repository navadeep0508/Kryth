"""ExecutionLayerSelector — V6 Phase 1.

The core V6 philosophy: pay only for the intelligence the mission actually needs.

    Small task  →  tiny runtime
    Large task  →  full intelligence stack

Tiers:
    0 MICRO      — read/write single file, trivial rename, tiny bug fix
                   layers: Direct executor + Ponytail only
                   overhead: ~0ms (no planner, no DAG, no milestones)

    1 SIMPLE     — small feature, single-module refactor
                   layers: Planner-lite + direct execution + Ponytail
                   overhead: ~50ms (planner call only)

    2 STANDARD   — medium coding task, 2-5 modules
                   layers: V3 stack (planner + milestones + contracts)
                   overhead: ~200ms

    3 COMPLEX    — large project, 5-10 modules
                   layers: V4 stack + team lead review + recovery
                   overhead: ~400ms

    4 ENTERPRISE — multi-team mission, 10+ modules
                   layers: Full V5 (portfolio + digital twin + quality + all)
                   overhead: ~600ms

Selection is automatic from the already-computed plan shape (module count,
file count, recommended_mode) — no new LLM call. Tiers 0-1 short-circuit
BEFORE the planner or DAG are even built; the mission_estimator's already-
computed complexity_score gates those cases in agent_loop.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, Optional


class ExecutionTier(IntEnum):
    MICRO      = 0
    SIMPLE     = 1
    STANDARD   = 2
    COMPLEX    = 3
    ENTERPRISE = 4


@dataclass(frozen=True)
class LayerSet:
    """Which V5 intelligence layers are active for this tier."""
    program_manager: bool = False   # ProgramManager / TeamLeadRuntime
    digital_twin: bool    = False    # MissionDigitalTwin
    blocker_intel: bool   = True     # BlockerIntelligence (cheap, always useful)
    org_health: bool      = False    # OrgHealthTracker
    forecaster: bool      = True     # ExecutionForecaster (cheap)
    recovery: bool        = False    # AutonomousRecoveryEngine
    quality: bool         = False    # QualityEngine
    portfolio: bool       = False    # PortfolioManager
    ponytail: bool        = True     # Ponytail profile (always active unless disabled)

    def active_names(self) -> list:
        return [f for f in self.__dataclass_fields__ if getattr(self, f)]


_LAYERS: Dict[int, LayerSet] = {
    ExecutionTier.MICRO: LayerSet(
        program_manager=False, digital_twin=False, blocker_intel=False,
        org_health=False, forecaster=False, recovery=False, quality=False,
        portfolio=False, ponytail=True,
    ),
    ExecutionTier.SIMPLE: LayerSet(
        program_manager=False, digital_twin=False, blocker_intel=True,
        org_health=False, forecaster=True, recovery=False, quality=False,
        portfolio=False, ponytail=True,
    ),
    ExecutionTier.STANDARD: LayerSet(
        program_manager=False, digital_twin=False, blocker_intel=True,
        org_health=False, forecaster=True, recovery=False, quality=True,
        portfolio=False, ponytail=True,
    ),
    ExecutionTier.COMPLEX: LayerSet(
        program_manager=True, digital_twin=False, blocker_intel=True,
        org_health=True, forecaster=True, recovery=True, quality=True,
        portfolio=False, ponytail=True,
    ),
    ExecutionTier.ENTERPRISE: LayerSet(
        program_manager=True, digital_twin=True, blocker_intel=True,
        org_health=True, forecaster=True, recovery=True, quality=True,
        portfolio=True, ponytail=True,
    ),
}


@dataclass
class TierDecision:
    tier: ExecutionTier
    layers: LayerSet
    reason: str
    module_count: int = 0
    estimated_files: int = 0
    complexity_score: int = 0    # 0-100 from mission_estimator


def select_tier(
    plan=None,
    user_input: str = "",
    complexity_score: int = 0,
    recommended_mode: str = "direct",
    module_count: int = 0,
    estimated_files: int = 0,
) -> TierDecision:
    """Automatically select the execution tier.

    Prefers plan metadata (exact module/file counts) when available.
    Falls back to complexity_score from mission_estimator when no plan.
    """
    # Derive counts from plan if provided
    if plan is not None:
        module_count = len(getattr(plan, "modules", [])) or module_count
        estimated_files = getattr(plan, "estimated_files", 0) or estimated_files
        recommended_mode = getattr(plan, "recommended_mode", recommended_mode) or recommended_mode

    # Tier 0: trivially small tasks — no orchestration needed at all
    if module_count <= 0 or recommended_mode == "direct":
        if complexity_score < 20 or module_count <= 0:
            return TierDecision(
                tier=ExecutionTier.MICRO,
                layers=_LAYERS[ExecutionTier.MICRO],
                reason="no modules / direct mode / trivial complexity",
                module_count=module_count,
                estimated_files=estimated_files,
                complexity_score=complexity_score,
            )

    # Tier 1: 1 module, small
    if module_count == 1 and estimated_files <= 3 and complexity_score < 40:
        return TierDecision(
            tier=ExecutionTier.SIMPLE,
            layers=_LAYERS[ExecutionTier.SIMPLE],
            reason=f"1 module, ≤3 files, score={complexity_score}",
            module_count=module_count,
            estimated_files=estimated_files,
            complexity_score=complexity_score,
        )

    # Tier 2: 2-4 modules, standard dag
    if module_count <= 4 and complexity_score < 60:
        return TierDecision(
            tier=ExecutionTier.STANDARD,
            layers=_LAYERS[ExecutionTier.STANDARD],
            reason=f"{module_count} modules, score={complexity_score}",
            module_count=module_count,
            estimated_files=estimated_files,
            complexity_score=complexity_score,
        )

    # Tier 3: 5-9 modules or high complexity
    if module_count <= 9 or complexity_score < 80:
        return TierDecision(
            tier=ExecutionTier.COMPLEX,
            layers=_LAYERS[ExecutionTier.COMPLEX],
            reason=f"{module_count} modules, score={complexity_score}",
            module_count=module_count,
            estimated_files=estimated_files,
            complexity_score=complexity_score,
        )

    # Tier 4: 10+ modules or very high complexity or swarm
    return TierDecision(
        tier=ExecutionTier.ENTERPRISE,
        layers=_LAYERS[ExecutionTier.ENTERPRISE],
        reason=f"{module_count} modules, score={complexity_score}, mode={recommended_mode}",
        module_count=module_count,
        estimated_files=estimated_files,
        complexity_score=complexity_score,
    )


def tier_from_env() -> Optional[int]:
    """Override: KRYTH_EXECUTION_TIER=0..4 forces a specific tier."""
    try:
        from agent.env import getenv
        v = getenv("KRYTH_EXECUTION_TIER").strip()
        if v.isdigit():
            return int(v)
    except Exception:
        pass
    return None


def layers_for(tier: ExecutionTier) -> LayerSet:
    return _LAYERS.get(tier, _LAYERS[ExecutionTier.ENTERPRISE])
