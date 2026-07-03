#!/usr/bin/env python
"""Full runtime benchmark for KRYTH optimization wave 2."""

import sys
import time
sys.path.insert(0, 'kryth/src')

from agent.runtime.agent_runtime import run_agent


def benchmark(name: str, user_input: str):
    """Run a benchmark through the full agent runtime."""
    print(f"\nRunning: {name}")
    start = time.perf_counter()
    try:
        state = run_agent(user_input)
        latency = time.perf_counter() - start
        
        tokens = state.stats.total_tokens_in + state.stats.total_tokens_out
        tool_calls = state.stats.tool_calls
        finish_reason = state.finish_reason
        
        return {
            "name": name,
            "latency_s": latency,
            "tokens": tokens,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "status": "PASS" if state.finished and state.finish_reason in ("completed", "token_budget_exceeded") else "FAIL"
        }
    except Exception as e:
        latency = time.perf_counter() - start
        print(f"  ERROR: {e}")
        return {
            "name": name,
            "latency_s": latency,
            "tokens": 0,
            "tool_calls": 0,
            "finish_reason": "error",
            "status": "ERROR"
        }


def main():
    print("=" * 60)
    print("KRYTH FULL RUNTIME BENCHMARK WAVE 2")
    print("=" * 60)
    
    benchmarks = [
        ("1. read this project", "read this project"),
        ("2. explain app.py", "explain app.py"),
        ("3. find routes", "find routes"),
        ("4. create hello.py", "create hello.py"),
        ("5. run this project", "run this project"),
        ("6. trace auth flow", "trace auth flow"),
    ]
    
    results = []
    for name, prompt in benchmarks:
        r = benchmark(name, prompt)
        results.append(r)
        print(f"  Status: {r['status']}")
        print(f"  Latency: {r['latency_s']:.1f}s")
        print(f"  Tokens: {r['tokens']}")
        print(f"  Tool calls: {r['tool_calls']}")
        print(f"  Finish: {r['finish_reason']}")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Test':30s} {'Status':8s} {'Latency':>8s} {'Tokens':>8s} {'Tools':>6s} {'Finish'}")
    print("-" * 80)
    
    total_tokens = 0
    total_latency = 0
    pass_count = 0
    
    for r in results:
        if r['status'] == "PASS":
            pass_count += 1
        total_tokens += r['tokens']
        total_latency += r['latency_s']
        print(f"{r['name']:30s} {r['status']:8s} {r['latency_s']:>7.1f}s {r['tokens']:>8d} {r['tool_calls']:>6d} {r['finish_reason']}")
    
    print("-" * 80)
    print(f"Pass Rate: {pass_count}/{len(results)} ({100*pass_count/len(results):.0f}%)")
    print(f"Avg Latency: {total_latency/len(results):.1f}s")
    print(f"Avg Tokens: {total_tokens//len(results)}")
    
    # Check success criteria
    print("\n" + "=" * 60)
    print("SUCCESS CRITERIA")
    print("=" * 60)
    print(f"PASS RATE >= 95%: {pass_count/len(results)*100:.0f}% - {'PASS' if pass_count/len(results) >= 0.95 else 'FAIL'}")
    print(f"AVG TOKENS < 10000: {total_tokens//len(results)} - {'PASS' if total_tokens//len(results) < 10000 else 'FAIL'}")
    print(f"AVG LATENCY < 20s: {total_latency/len(results):.1f}s - {'PASS' if total_latency/len(results) < 20 else 'FAIL'}")
    
    return results


if __name__ == "__main__":
    main()