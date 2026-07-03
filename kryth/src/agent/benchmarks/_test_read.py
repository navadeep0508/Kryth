import sys, time
print("START", flush=True)

from agent.benchmarks.suite import run_benchmarks, print_report, get_prompts, run_single

print("Loaded suite module", flush=True)

# Run just the first READ prompt
p = get_prompts("READ")[0]
print(f"Prompt: {p.name} ({p.prompt})", flush=True)

print("Running setup...", flush=True)
p.setup()
print("Setup done", flush=True)

t0 = time.monotonic()
r = run_single(p)
elapsed = time.monotonic() - t0
e = getattr(r, "error", "none")
print(f"Status: {r.status}, Error: {e}, Latency: {r.latency_s}s, Real: {elapsed:.1f}s", flush=True)
