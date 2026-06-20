"""LLM analyzer — measures reasoning efficiency, token usage, and retry patterns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from benchmark.benchmark_metrics import BenchmarkRun, MissionMetrics


@dataclass
class LLMMissionStats:
    mission_id: str
    tokens_in: int
    tokens_out: int
    total_tokens: int
    turns_used: int
    tokens_per_turn: float
    tokens_out_per_turn: float
    context_ratio: float          # tokens_in / total — how much is context vs generation
    tools_per_turn: float
    silent_turn_estimate: int     # turns - tool_batches (approx no-tool turns)
    had_api_error: bool
    had_timeout: bool


@dataclass
class LLMAnalysis:
    per_mission: List[LLMMissionStats] = field(default_factory=list)
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_turns: int = 0
    avg_tokens_per_turn: float = 0.0
    avg_context_ratio: float = 0.0
    avg_tools_per_turn: float = 0.0
    avg_silent_turns: float = 0.0
    missions_with_api_error: int = 0
    missions_with_timeout: int = 0
    # Flags
    context_bloat_detected: bool = False    # avg_context_ratio > 0.85
    low_tool_density: bool = False          # avg_tools_per_turn < 1.5
    high_silent_turns: bool = False         # avg_silent_turns > 2


def analyze_llm(run: BenchmarkRun) -> LLMAnalysis:
    stats = []
    for m in run.missions:
        total_tok = m.tokens_in + m.tokens_out
        turns = max(m.turns_used, 1)
        context_ratio = m.tokens_in / max(total_tok, 1)
        tools_per_turn = m.total_tool_calls / turns
        # Estimate silent turns: total turns minus turns that had at least one tool
        # parallel.total_tool_batches = turns with tools
        tool_turns = m.parallel.total_tool_batches
        silent_est = max(turns - tool_turns, 0)

        had_api = "api_error" in (m.error or "").lower() or "retry" in (m.error or "").lower()
        had_timeout = "timeout" in (m.error or "").lower() or "TIMEOUT" in (m.error or "")

        stats.append(LLMMissionStats(
            mission_id=m.mission_id,
            tokens_in=m.tokens_in,
            tokens_out=m.tokens_out,
            total_tokens=total_tok,
            turns_used=m.turns_used,
            tokens_per_turn=total_tok / turns,
            tokens_out_per_turn=m.tokens_out / turns,
            context_ratio=context_ratio,
            tools_per_turn=tools_per_turn,
            silent_turn_estimate=silent_est,
            had_api_error=had_api,
            had_timeout=had_timeout,
        ))

    analysis = LLMAnalysis(per_mission=stats)
    if not stats:
        return analysis

    n = len(stats)
    analysis.total_tokens_in = sum(s.tokens_in for s in stats)
    analysis.total_tokens_out = sum(s.tokens_out for s in stats)
    analysis.total_turns = sum(s.turns_used for s in stats)
    analysis.avg_tokens_per_turn = sum(s.tokens_per_turn for s in stats) / n
    analysis.avg_context_ratio = sum(s.context_ratio for s in stats) / n
    analysis.avg_tools_per_turn = sum(s.tools_per_turn for s in stats) / n
    analysis.avg_silent_turns = sum(s.silent_turn_estimate for s in stats) / n
    analysis.missions_with_api_error = sum(1 for s in stats if s.had_api_error)
    analysis.missions_with_timeout = sum(1 for s in stats if s.had_timeout)
    analysis.context_bloat_detected = analysis.avg_context_ratio > 0.85
    analysis.low_tool_density = analysis.avg_tools_per_turn < 1.5
    analysis.high_silent_turns = analysis.avg_silent_turns > 2

    return analysis
