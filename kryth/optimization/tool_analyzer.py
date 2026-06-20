"""Tool analyzer — measures tool usage patterns and streaming efficiency."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from benchmark.benchmark_metrics import BenchmarkRun, MissionMetrics


@dataclass
class ToolMissionStats:
    mission_id: str
    total_tool_calls: int
    files_written: int
    files_read: int
    commands_run: int
    streams_started: int
    total_chunks: int
    total_lines_streamed: int
    syntax_errors_mid_stream: int
    finalized_streams: int
    # Ratios
    read_write_ratio: float
    stream_coverage: float      # streams_started / files_written (1.0 = all streamed)
    avg_chunk_size_lines: float


@dataclass
class ToolAnalysis:
    per_mission: List[ToolMissionStats] = field(default_factory=list)
    total_tool_calls: int = 0
    total_files_written: int = 0
    total_files_read: int = 0
    total_commands_run: int = 0
    total_streams: int = 0
    total_syntax_errors: int = 0
    avg_read_write_ratio: float = 0.0
    avg_stream_coverage: float = 0.0
    # Observations
    streaming_enabled: bool = False
    high_command_rate: bool = False     # many commands → extensive validation or troubleshooting
    stream_error_rate: float = 0.0      # syntax_errors / streams


def analyze_tools(run: BenchmarkRun) -> ToolAnalysis:
    stats = []
    for m in run.missions:
        s = m.streaming
        read_write = m.files_read / max(m.files_written, 1)
        stream_cov = s.streams_started / max(m.files_written, 1)
        avg_chunk = s.total_lines_streamed / max(s.total_chunks, 1)

        stats.append(ToolMissionStats(
            mission_id=m.mission_id,
            total_tool_calls=m.total_tool_calls,
            files_written=m.files_written,
            files_read=m.files_read,
            commands_run=m.commands_run,
            streams_started=s.streams_started,
            total_chunks=s.total_chunks,
            total_lines_streamed=s.total_lines_streamed,
            syntax_errors_mid_stream=s.syntax_errors_caught_mid_stream,
            finalized_streams=s.finalized_streams,
            read_write_ratio=read_write,
            stream_coverage=stream_cov,
            avg_chunk_size_lines=avg_chunk,
        ))

    analysis = ToolAnalysis(per_mission=stats)
    if not stats:
        return analysis

    n = len(stats)
    analysis.total_tool_calls = sum(s.total_tool_calls for s in stats)
    analysis.total_files_written = sum(s.files_written for s in stats)
    analysis.total_files_read = sum(s.files_read for s in stats)
    analysis.total_commands_run = sum(s.commands_run for s in stats)
    analysis.total_streams = sum(s.streams_started for s in stats)
    analysis.total_syntax_errors = sum(s.syntax_errors_mid_stream for s in stats)
    analysis.avg_read_write_ratio = sum(s.read_write_ratio for s in stats) / n
    analysis.avg_stream_coverage = sum(s.stream_coverage for s in stats) / n
    analysis.streaming_enabled = analysis.total_streams > 0
    analysis.high_command_rate = (
        analysis.total_commands_run / max(analysis.total_files_written, 1)
    ) > 1.5
    analysis.stream_error_rate = analysis.total_syntax_errors / max(analysis.total_streams, 1)

    return analysis
