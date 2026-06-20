"""KRYTH Autonomous Benchmark Suite.

Executes real engineering missions and measures every subsystem:
  - Worker pool utilization and idle time
  - Streaming file generation vs normal writes
  - Speculative loading latency reduction
  - Background validation effectiveness
  - Parallel agent throughput
  - Memory / experience store reuse
  - Recovery engine success rate

Usage:
    python benchmark/run_benchmark.py
    python benchmark/run_benchmark.py --missions 1,2,3 --timeout 300
    python benchmark/run_benchmark.py --compare benchmark_history/run_001.json
"""
