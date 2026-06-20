"""Recommendation engine — aggregates all analyses into ranked, actionable suggestions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from optimization.execution_profiler import ExecutionProfile
from optimization.bottleneck_detector import BottleneckReport
from optimization.worker_analyzer import WorkerAnalysis
from optimization.memory_analyzer import MemoryAnalysis
from optimization.llm_analyzer import LLMAnalysis
from optimization.tool_analyzer import ToolAnalysis
from optimization.scheduler_analyzer import SchedulerAnalysis


PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@dataclass
class Recommendation:
    priority: str               # CRITICAL / HIGH / MEDIUM / LOW
    category: str               # PARALLEL / MEMORY / TESTING / WORKER / LLM / TOOL / SCHEDULING / API
    text: str                   # One-sentence summary (the "headline")
    details: str                # Explanation of what was observed
    action: str                 # Concrete suggested action
    expected_gain_pct: float    # Estimated improvement in mission time (0 if unknown)
    confidence: float           # 0.0 – 1.0

    @property
    def rank(self) -> int:
        return PRIORITY_RANK.get(self.priority, 99)


@dataclass
class RecommendationSet:
    recommendations: List[Recommendation] = field(default_factory=list)
    total_expected_gain_pct: float = 0.0
    avg_confidence: float = 0.0

    def sorted(self) -> List[Recommendation]:
        return sorted(self.recommendations, key=lambda r: (r.rank, -r.expected_gain_pct))

    def high_and_above(self) -> List[Recommendation]:
        return [r for r in self.sorted() if r.rank <= 1]


def generate_recommendations(
    profile: ExecutionProfile,
    bottlenecks: BottleneckReport,
    workers: WorkerAnalysis,
    memory: MemoryAnalysis,
    llm: LLMAnalysis,
    tools: ToolAnalysis,
    scheduler: SchedulerAnalysis,
) -> RecommendationSet:
    recs: List[Recommendation] = []

    # ── API / retry pressure ─────────────────────────────────────────────────────
    if llm.missions_with_api_error > 0:
        recs.append(Recommendation(
            priority="HIGH",
            category="API",
            text=f"API retry pressure affected {llm.missions_with_api_error} mission(s) — missions waited up to 120s for rate limit recovery.",
            details=(
                f"{llm.missions_with_api_error} mission(s) triggered api_error retries. "
                "Each retry waits 20s + 40s + 60s = up to 120s of dead time per stuck turn."
            ),
            action="Increase KRYTH_INTER_MISSION_DELAY or implement exponential backoff with jitter.",
            expected_gain_pct=12.0,
            confidence=0.75,
        ))

    # ── Timeout missions ────────────────────────────────────────────────────────
    if llm.missions_with_timeout > 0:
        recs.append(Recommendation(
            priority="CRITICAL",
            category="SCHEDULING",
            text=f"{llm.missions_with_timeout} mission(s) hit the 600s timeout — workspace check salvaged some passes.",
            details="Timed-out missions consumed the full budget without completing all steps.",
            action="Profile the specific missions, reduce turn count via better initial planning.",
            expected_gain_pct=20.0,
            confidence=0.65,
        ))

    # ── Parallel efficiency ─────────────────────────────────────────────────────
    avg_par = scheduler.avg_parallel_efficiency_pct
    if avg_par < 40:
        recs.append(Recommendation(
            priority="HIGH",
            category="PARALLEL",
            text=f"Parallel tool dispatch efficiency is only {avg_par:.1f}% — most tool calls run serially.",
            details=(
                f"Average across all missions: {avg_par:.1f}% of tool calls are in parallel batches. "
                f"Worst mission: {scheduler.worst_mission}."
            ),
            action="Strengthen system prompt instructions to call multiple tools in parallel; audit task_classifier for missions routed to single-tool turns.",
            expected_gain_pct=15.0,
            confidence=0.70,
        ))
    elif avg_par < 60:
        recs.append(Recommendation(
            priority="MEDIUM",
            category="PARALLEL",
            text=f"Parallel efficiency at {avg_par:.1f}% — room to improve batch dispatch.",
            details=f"Best mission: {scheduler.best_mission} ({scheduler.avg_max_batch_size:.1f} avg max batch size).",
            action="Identify turns with single tool calls and group them into parallel batches.",
            expected_gain_pct=8.0,
            confidence=0.60,
        ))

    # ── Testing coverage ────────────────────────────────────────────────────────
    no_test_count = profile.missions_with_tests
    total = len(profile.missions)
    missions_without_tests = total - no_test_count
    if missions_without_tests > 0:
        recs.append(Recommendation(
            priority="MEDIUM",
            category="TESTING",
            text=f"Testing starts too late — {missions_without_tests}/{total} missions ran no tests at all.",
            details=(
                f"Only {no_test_count} missions triggered test runs. "
                "Missions without tests may silently produce broken code."
            ),
            action="Add test-file generation requirement to system prompt for all BUILD and FIX missions.",
            expected_gain_pct=5.0,
            confidence=0.55,
        ))

    # ── Worker utilization ───────────────────────────────────────────────────────
    avg_util = workers.avg_utilization_pct
    if avg_util > 0 and avg_util < 50:
        recs.append(Recommendation(
            priority="MEDIUM",
            category="WORKER",
            text=f"Worker utilization is only {avg_util:.1f}% — agents are spawned but idle.",
            details=(
                f"{workers.missions_idle_waste} mission(s) ended with idle worker slots. "
                f"Average peak active: {workers.avg_peak_active:.1f} agents."
            ),
            action="Reduce agent spawn count for simple missions; reserve multi-agent for complex/full-stack tasks.",
            expected_gain_pct=7.0,
            confidence=0.50,
        ))

    if workers.under_provisioned:
        recs.append(Recommendation(
            priority="LOW",
            category="WORKER",
            text=f"Large missions ({', '.join(workers.under_provisioned)}) ran with only 1 agent — likely under-provisioned.",
            details="These missions wrote many files but used single-agent mode. Parallel sub-agents could split the work.",
            action="Lower the SESSION_APPROVED complexity threshold or increase files_written trigger.",
            expected_gain_pct=10.0,
            confidence=0.45,
        ))

    # ── Memory utilization ───────────────────────────────────────────────────────
    if not memory.memory_subsystem_active:
        recs.append(Recommendation(
            priority="MEDIUM",
            category="MEMORY",
            text="Memory hit rate is 0% — no preload, experience, or knowledge graph hits observed.",
            details=(
                "All memory subsystems (speculative preload, experience engine, worker pool preload, "
                "knowledge graph) reported zero hits across all missions."
            ),
            action="Verify speculative_preload thread is firing; check experience engine is writing after successful missions.",
            expected_gain_pct=8.0,
            confidence=0.55,
        ))
    elif memory.speculative_hit_rate < 0.3:
        recs.append(Recommendation(
            priority="LOW",
            category="MEMORY",
            text=f"Speculative preload active on only {memory.speculative_hit_rate*100:.0f}% of missions.",
            details=f"Average {memory.avg_preload_files:.1f} preloaded files per mission.",
            action="Expand keyword-to-file mapping in speculative preload; add domain patterns for auth, API, React.",
            expected_gain_pct=5.0,
            confidence=0.40,
        ))

    # ── LLM context bloat ────────────────────────────────────────────────────────
    if llm.context_bloat_detected:
        recs.append(Recommendation(
            priority="MEDIUM",
            category="LLM",
            text=f"Context ratio is {llm.avg_context_ratio*100:.0f}% — context window is growing too large.",
            details=(
                f"Average {llm.avg_context_ratio*100:.0f}% of tokens are input context vs {(1-llm.avg_context_ratio)*100:.0f}% generation. "
                "Large context windows increase latency and cost per turn."
            ),
            action="Trigger context compaction earlier; prune old tool results from session history.",
            expected_gain_pct=6.0,
            confidence=0.60,
        ))

    # ── Silent turns ─────────────────────────────────────────────────────────────
    if llm.high_silent_turns:
        recs.append(Recommendation(
            priority="LOW",
            category="LLM",
            text=f"Avg {llm.avg_silent_turns:.1f} silent/planning turns per mission — LLM describing work instead of doing it.",
            details="Silent turns (text with no tool calls) add latency without producing output.",
            action="Strengthen no-idle rule in system prompt: dispatch tools immediately, no preamble.",
            expected_gain_pct=4.0,
            confidence=0.50,
        ))

    # ── Exploration overhead ─────────────────────────────────────────────────────
    if profile.avg_exploration_pct > 30:
        recs.append(Recommendation(
            priority="LOW",
            category="PLANNING",
            text=f"Exploration phase averages {profile.avg_exploration_pct:.1f}% of mission time — too much reading before writing.",
            details=f"Avg {profile.avg_exploration_ms/1000:.1f}s reading before first write (target: <20% of mission time).",
            action="Use speculative preload to front-load file context; prompt the agent to write immediately on simple tasks.",
            expected_gain_pct=5.0,
            confidence=0.45,
        ))

    # ── Stream errors ────────────────────────────────────────────────────────────
    if tools.stream_error_rate > 0.1:
        recs.append(Recommendation(
            priority="LOW",
            category="TOOL",
            text=f"Streaming syntax error rate is {tools.stream_error_rate*100:.0f}% — mid-stream corrections occurring.",
            details=f"{tools.total_syntax_errors} syntax errors caught across {tools.total_streams} streams.",
            action="Investigate chunk boundaries; the model may be splitting expressions across chunks.",
            expected_gain_pct=2.0,
            confidence=0.40,
        ))

    rset = RecommendationSet(recommendations=recs)
    if recs:
        high = [r for r in recs if r.rank <= 1]
        # Cap total gain at 40% (improvements compound but aren't additive)
        rset.total_expected_gain_pct = min(sum(r.expected_gain_pct for r in high) * 0.7, 40.0)
        rset.avg_confidence = sum(r.confidence for r in recs) / len(recs)
    return rset
