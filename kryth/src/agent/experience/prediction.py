"""Prediction Engine — estimates success probability and likely plan before acting."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from agent.experience.similar_task import SimilarTaskSearchResult


@dataclass
class Prediction:
    success_probability: float       # 0.0 – 1.0
    likely_repairs: List[str]
    likely_build_time_turns: int
    recommended_agent_count: int
    recommended_workflow: List[str]
    risk_factors: List[str]
    confidence: float
    reasoning: str
    evidence_count: int = 0


def predict(
    user_input: str,
    similar: SimilarTaskSearchResult,
    project_root: str = ".",
) -> Prediction:
    """Generate a prediction from the similar-task search results."""
    if not similar.matches:
        return Prediction(
            success_probability=0.70,
            likely_repairs=[],
            likely_build_time_turns=80,
            recommended_agent_count=1,
            recommended_workflow=[],
            risk_factors=["No historical data — estimate only"],
            confidence=0.30,
            reasoning="No similar tasks found; using conservative defaults",
        )

    top = similar.best_match

    # Success probability: weighted average of top-3 hits
    top_3 = similar.matches[:3]
    avg_sr = sum(m.historical_success_rate for m in top_3) / len(top_3)
    success_prob = min(1.0, avg_sr * similar.confidence)

    # Repairs from top match
    likely_repairs = top.known_repairs[:5] if top else []

    # Turns: use top-match extra if available, else estimate
    build_turns = 80
    if top and top.best_workflow_steps:
        build_turns = max(30, len(top.best_workflow_steps) * 15)

    # Agent count from parallel experience
    recommended_agents = 1
    try:
        from agent.experience.parallel_experience import ParallelExperienceEngine
        from agent.experience.store import get_store
        pe = ParallelExperienceEngine(get_store(project_root))
        ac, conf = pe.recommend_agent_count(user_input[:60])
        if conf >= 0.5:
            recommended_agents = ac
    except Exception:
        pass

    # Workflow from top match
    workflow = top.best_workflow_steps[:8] if top else []

    # Risk factors
    risks: List[str] = []
    if avg_sr < 0.80:
        risks.append(f"Historical success rate is only {avg_sr:.0%}")
    for m in top_3:
        risks.extend(m.known_risks[:2])
    risks = list(dict.fromkeys(risks))[:4]  # deduplicate, cap at 4

    confidence = similar.confidence

    reasoning = (
        f"Based on {len(similar.matches)} similar task(s). "
        f"Best match: '{top.title}' (similarity {top.similarity_score:.0%}, "
        f"success rate {top.historical_success_rate:.0%}). "
        f"Searched: {', '.join(similar.searched_systems)}."
    )

    return Prediction(
        success_probability=round(success_prob, 2),
        likely_repairs=likely_repairs,
        likely_build_time_turns=build_turns,
        recommended_agent_count=recommended_agents,
        recommended_workflow=workflow,
        risk_factors=risks,
        confidence=round(confidence, 2),
        reasoning=reasoning,
        evidence_count=len(similar.matches),
    )
