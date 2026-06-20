"""Provider-compatibility regressions (found during live Groq validation).

1. The OpenAI `stop` parameter is capped at 4 items; sending 5 made Groq (and
   any strict gateway) 400 on EVERY call, forcing a slow compatibility-retry
   loop. _STOP_SEQUENCES must stay <= 4.
2. A 413 "request too large / tokens-per-minute" error is NOT time-recoverable
   (the tool-schema payload is fixed), so it must classify as a fast-fail
   'payload_too_large' rather than be slow-retried for ~60s.
"""

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import agent.llm as llm


# ── 1. stop-sequence cap ─────────────────────────────────────────────────────

def test_stop_sequences_within_openai_cap():
    # OpenAI/Groq/most gateways reject >4 stop sequences with a 400.
    assert len(llm._STOP_SEQUENCES) <= 4


def test_stop_sequences_nonempty_and_unique():
    assert llm._STOP_SEQUENCES
    assert len(set(llm._STOP_SEQUENCES)) == len(llm._STOP_SEQUENCES)


# ── 2. payload-too-large classification (fast-fail, non-retryable) ───────────

class _Err(Exception):
    def __init__(self, body):
        self.body = body
        super().__init__(body)


@pytest.mark.parametrize("body", [
    "Request too large for model `llama-3.3-70b-versatile` ... tokens per minute (TPM): Limit 12000, Requested 18079, please reduce your message size",
    "rate limit: tokens per minute (TPM) Limit 6000 exceeded",
    "413 request too large",
    "please reduce your message size and try again",
])
def test_payload_too_large_classified(body):
    assert llm._classify_400(_Err(body)) == "payload_too_large"


def test_context_overflow_still_classified():
    assert llm._classify_400(_Err("This model's maximum context length is 8192 ... reduce the length")) == "context_overflow"


def test_tools_unsupported_still_classified():
    assert llm._classify_400(_Err("this model does not support tool calling")) == "tools_unsupported"


def test_max_tokens_still_classified():
    assert llm._classify_400(_Err("max_tokens is too large: 32768")) == "max_tokens"


def test_generic_400_is_other():
    assert llm._classify_400(_Err("some unrelated bad request")) == "other"


def test_payload_too_large_not_in_loop_retry_reasons():
    # The agent loop slow-retries only these reasons; payload_too_large must
    # NOT be among them so it fails fast (no 60s retry storm).
    import inspect
    import agent.agent_loop as al
    src = inspect.getsource(al.run_inner_loop)
    assert 'reason in ("api_error", "stream_error", "timeout")' in src
    assert "payload_too_large" not in src  # loop never references it → falls to fast exit
