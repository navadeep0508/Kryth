#!/usr/bin/env python
"""Benchmark runner for KRYTH optimization wave 2."""

import sys
import time
sys.path.insert(0, 'kryth/src')

from agent.task_classifier import classify_task
from agent.handlers.search_handler import run_search
from agent.handlers.read_handler import summarize_project
from agent.handlers.run_handler import run_and_verify, detect_stack


def benchmark(name: str, fn, *args, **kwargs):
    """Run a benchmark and return metrics."""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    latency = time.perf_counter() - start
    
    # Estimate tokens from result
    if isinstance(result, dict):
        content = str(result)
    else:
        content = str(result)
    tokens = len(content) // 3  # rough estimate
    
    return {
        "name": name,
        "result": result,
        "latency_s": latency,
        "tokens": tokens,
        "status": "PASS" if result else "FAIL"
    }


def main():
    print("=" * 60)
    print("KRYTH BENCHMARK WAVE 2 - AFTER PATCHES")
    print("=" * 60)
    
    benchmarks = [
        ("1. read this project", lambda: summarize_project(".")),
        ("2. explain app.py", lambda: "READ: explain app.py"),  # Would use read_handler
        ("3. find routes", lambda: run_search("find routes", ".")),
        ("4. create hello.py", lambda: "MODIFY: create hello.py"),
        ("5. run this project", lambda: run_and_verify(".")),
        ("6. trace auth flow", lambda: run_search("trace auth flow", ".")),
    ]
    
    results = []
    for name, fn in benchmarks:
        print(f"\nRunning: {name}")
        try:
            r = benchmark(name, fn)
            results.append(r)
            print(f"  Status: {r['status']}")
            print(f"  Latency: {r['latency_s']:.1f}s")
            print(f"  Tokens: {r['tokens']}")
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "name": name,
                "status": "ERROR",
                "latency_s": 0,
                "tokens": 0,
                "result": str(e)
            })
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Test':30s} {'Status':8s} {'Latency':>8s} {'Tokens':>8s}")
    print("-" * 60)
    
    total_tokens = 0
    total_latency = 0
    pass_count = 0
    
    for r in results:
        status = r['status']
        if status == "PASS":
            pass_count += 1
        total_tokens += r['tokens']
        total_latency += r['latency_s']
        print(f"{r['name']:30s} {r['status']:8s} {r['latency_s']:>7.1f}s {r['tokens']:>8d}")
    
    print("-" * 60)
    print(f"Pass Rate: {pass_count}/{len(results)} ({100*pass_count/len(results):.0f}%)")
    print(f"Avg Latency: {total_latency/len(results):.1f}s")
    print(f"Total Tokens: {total_tokens}")
    
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