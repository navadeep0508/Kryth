"""KRYTH production layer — additive coordination & observability.

This package contains the V1 production-completion modules. Every module here
is *additive*: it observes, aggregates, or advises on top of the existing
subsystems (Mission OS, Executive, Scheduler, Replay, Confidence Engine) without
replacing any of them. Nothing here mutates scheduler or execution behavior;
the controllers return *recommendations* the existing systems may consult.

Modules
-------
context_shard       — per-domain context partitioning + hierarchical compression
reputation          — persistent tool/executor success tracking + leaderboard
adaptive            — runtime worker-count controller (queue depth / idle ratio)
bottleneck          — root-cause analysis over the critical path
execution_profiles  — FAST / BALANCED / MAXIMUM_QUALITY knob bundles
telemetry           — unified metric aggregation + JSON/Markdown export
readiness           — production-readiness report + score
"""
