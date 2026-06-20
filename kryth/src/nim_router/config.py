"""NVIDIA NIM router configuration — model chains and per-level timeouts."""

import os
from dataclasses import dataclass


BASE_URL = "https://integrate.api.nvidia.com/v1"


def get_api_key() -> str:
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not key:
        raise EnvironmentError(
            "NVIDIA_API_KEY is not set.\n"
            "  export NVIDIA_API_KEY=nvapi-..."
        )
    return key


@dataclass(frozen=True)
class ModelEntry:
    """One model candidate in the fallback chain."""
    name: str
    timeout_s: float  # seconds to wait for first token before falling back


@dataclass(frozen=True)
class ModelChain:
    """Ordered tuple of models for a role: primary → fallback1 → fallback2."""
    role: str
    purpose: str
    models: tuple[ModelEntry, ...]

    def __iter__(self):
        return iter(self.models)

    def __len__(self):
        return len(self.models)


# ── Model chains ──────────────────────────────────────────────────────────────

MODEL_CHAINS: dict[str, ModelChain] = {
    "main": ModelChain(
        role="main",
        purpose="User conversations, final responses, general reasoning, multi-agent coordination",
        models=(
            ModelEntry("moonshotai/kimi-k2.6",              timeout_s=5.0),
            ModelEntry("openai/gpt-oss-20b",                timeout_s=3.0),
            ModelEntry("qwen/qwen3-next-80b-a3b-instruct",  timeout_s=8.0),
        ),
    ),
    "planner": ModelChain(
        role="planner",
        purpose="Task decomposition, agent planning, workflow generation, long-horizon reasoning",
        models=(
            ModelEntry("moonshotai/kimi-k2.6",                      timeout_s=5.0),
            ModelEntry("qwen/qwen3-next-80b-a3b-instruct",          timeout_s=8.0),
            ModelEntry("nvidia/llama-3.3-nemotron-super-49b-v1.5",  timeout_s=3.0),
        ),
    ),
    "summarizer": ModelChain(
        role="summarizer",
        purpose="Context compression, conversation summarization, memory reduction",
        models=(
            ModelEntry("stepfun-ai/step-3.5-flash",  timeout_s=3.0),
            ModelEntry("google/gemma-3n-e2b-it",     timeout_s=3.0),
            ModelEntry("google/gemma-3n-e4b-it",     timeout_s=3.0),
        ),
    ),
    "vision": ModelChain(
        role="vision",
        purpose="Image understanding, screenshot analysis, UI analysis, OCR and visual reasoning",
        models=(
            ModelEntry("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",  timeout_s=5.0),
            ModelEntry("meta/llama-3.2-11b-vision-instruct",             timeout_s=3.0),
            ModelEntry("nvidia/llama-3.1-nemotron-nano-vl-8b-v1",       timeout_s=3.0),
        ),
    ),
}
