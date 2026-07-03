import sys, time
print("START full benchmark suite", flush=True)

from agent.benchmarks.suite import run_benchmarks, print_report

t0 = time.monotonic()
r = run_benchmarks(verbose=True)
elapsed = time.monotonic() - t0

print(f"\nTotal time: {elapsed:.1f}s", flush=True)
print_report(r)
