"""Demo: route one request per role and display streaming output + metrics.

Usage:
    export NVIDIA_API_KEY=nvapi-...
    python main.py

To test fallback behaviour, temporarily set an invalid primary model name
in config.py and re-run — the router will automatically advance to the next
model in the chain and log the failure.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import MODEL_CHAINS
from models import ModelRole
from router import NIMRouter


# ── Helpers ───────────────────────────────────────────────────────────────────

def _divider(title: str) -> None:
    print(f"\n{'=' * 64}")
    print(f"  {title}")
    print('=' * 64)


def _summary(result) -> None:
    level_label = ["primary", "fallback-1", "fallback-2"]
    fb = level_label[min(result.fallback_level, 2)]
    print(
        f"\n\n── result ────────────────────────────────────────────────\n"
        f"  model    : {result.model_used}\n"
        f"  fallback : level {result.fallback_level} ({fb})\n"
        f"  ttft     : {result.ttft_ms:.0f} ms\n"
        f"  latency  : {result.total_latency_ms:.0f} ms\n"
        f"  tokens   : {result.prompt_tokens} in / {result.completion_tokens} out"
        f" / {result.total_tokens} total"
    )
    if result.errors:
        print(f"  skipped  : {[e['error'] + ':' + e['model'] for e in result.errors]}")


# ── Role demos ────────────────────────────────────────────────────────────────

async def demo_main(router: NIMRouter) -> None:
    _divider("MAIN — general reasoning")
    messages = [
        {"role": "system", "content": "You are a concise, helpful assistant."},
        {
            "role": "user",
            "content": (
                "In three bullet points, explain how transformer attention works."
            ),
        },
    ]
    result = await router.route(
        ModelRole.MAIN,
        messages,
        on_chunk=lambda p: print(p, end="", flush=True),
        temperature=0.6,
        max_tokens=512,
    )
    _summary(result)


async def demo_planner(router: NIMRouter) -> None:
    _divider("PLANNER — task decomposition")
    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior software architect. "
                "Output a concise numbered plan — no prose preamble."
            ),
        },
        {
            "role": "user",
            "content": (
                "Plan the steps to build a production-ready REST API with "
                "JWT authentication in Python (FastAPI + PostgreSQL)."
            ),
        },
    ]
    result = await router.route(
        ModelRole.PLANNER,
        messages,
        on_chunk=lambda p: print(p, end="", flush=True),
        temperature=0.4,
        max_tokens=512,
    )
    _summary(result)


async def demo_summarizer(router: NIMRouter) -> None:
    _divider("SUMMARIZER — context compression")
    long_context = (
        "The user and assistant discussed building a distributed task queue. "
        "Topics covered: Redis Streams vs RabbitMQ, dead-letter queues, "
        "idempotency keys, at-least-once delivery guarantees, consumer groups, "
        "back-pressure strategies, and horizontal scaling with multiple workers. "
        "The user expressed a preference for Redis because they already operate it "
        "in production. The assistant recommended Redis Streams with consumer groups "
        "and suggested storing task state in PostgreSQL for durability."
    )
    messages = [
        {
            "role": "system",
            "content": "Summarize the conversation below in exactly one sentence.",
        },
        {"role": "user", "content": long_context},
    ]
    result = await router.route(
        ModelRole.SUMMARIZER,
        messages,
        on_chunk=lambda p: print(p, end="", flush=True),
        temperature=0.3,
        max_tokens=128,
    )
    _summary(result)


async def demo_vision(router: NIMRouter) -> None:
    _divider("VISION — visual reasoning (text prompt demo)")
    # Swap the content list for an image_url entry for real vision tasks:
    #   {"type": "image_url", "image_url": {"url": "https://example.com/shot.png"}}
    messages = [
        {
            "role": "user",
            "content": (
                "List five specific things you would check when auditing a "
                "login-page screenshot for WCAG 2.1 accessibility compliance."
            ),
        }
    ]
    result = await router.route(
        ModelRole.VISION,
        messages,
        on_chunk=lambda p: print(p, end="", flush=True),
        temperature=0.5,
        max_tokens=512,
    )
    _summary(result)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    if not os.environ.get("NVIDIA_API_KEY"):
        print("ERROR: NVIDIA_API_KEY is not set.", file=sys.stderr)
        print("  export NVIDIA_API_KEY=nvapi-...", file=sys.stderr)
        sys.exit(1)

    print("\nNVIDIA NIM Multi-Agent Router — demo")
    print(f"Endpoint : {__import__('config').BASE_URL}")
    print("Roles    :", ", ".join(MODEL_CHAINS))

    router = NIMRouter(log_level="INFO")

    await demo_main(router)
    await demo_planner(router)
    await demo_summarizer(router)
    await demo_vision(router)

    print("\n\n✓ All demos complete.")


if __name__ == "__main__":
    asyncio.run(main())
