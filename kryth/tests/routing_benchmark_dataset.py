"""KRYTH Routing Benchmark — FROZEN CONTRACT.

This dataset is a protected contract. Each entry pins the expected routing
behavior of a prompt. Any change to the classifier or agent loop that alters
these expectations MUST fail CI (tests/test_routing_benchmark.py).

If you intentionally change routing behavior, you must update this dataset in
the same commit and justify it in the PR — that is the audit trail.

Fields per entry
----------------
prompt          : the user input, verbatim
route           : "conversation" | "execution"
                  conversation → tool-less direct reply (no engine)
                  execution    → reaches the engineering engine
tool_count      : expected number of tool calls
                  (conversation: exactly 0; execution: model-decided → None,
                   not asserted exactly, but capability must be present)
planner         : expected planner (ask_planner) usage
executive       : expected orchestrator (orchestrate) usage
ual             : expected dynamic-builder / UAL usage
workers         : expected worker-pool spawn
file_writes     : expected file writes (conversation: 0)
commands        : expected command executions (conversation: 0)
approvals       : expected approval prompts (conversation: 0)

Determinism note
----------------
The benchmark runs with the LLM tiebreaker forced offline (as in CI with no
API key), so every prompt below classifies deterministically. None of the
execution prompts are "complex", so none engage the planner / executive / UAL.
"""

# route, planner, executive, ual default helpers keep entries terse.
def _conv(prompt):
    return {
        "prompt": prompt,
        "route": "conversation",
        "tool_count": 0,
        "planner": False,
        "executive": False,
        "ual": False,
        "workers": False,
        "file_writes": 0,
        "commands": 0,
        "approvals": 0,
    }


def _exec(prompt, *, workers=True):
    return {
        "prompt": prompt,
        "route": "execution",
        "tool_count": None,      # model-decided; capability must exist
        "planner": False,        # none of these are complex
        "executive": False,
        "ual": False,
        "workers": workers,      # spawn_early fires on the execution path
        "file_writes": None,     # model-decided
        "commands": None,        # model-decided
        "approvals": None,       # model-decided
    }


# ── CONVERSATION — must be tool-less, zero execution ───────────────────────
CONVERSATION = [
    _conv("hi"),
    _conv("hello"),
    _conv("helo"),
    _conv("hey"),
    _conv("thanks"),
    _conv("thank you"),
    _conv("ok"),
    _conv("okay"),
    _conv("yes"),
    _conv("no"),
    _conv("who are you"),
    _conv("what is python"),
    _conv("what is jwt"),
    _conv("hello what is python"),
    _conv("thanks what can you do"),
]

# ── EXECUTION — must reach the engine ──────────────────────────────────────
EXECUTION = [
    _exec("create hello.txt"),
    _exec("write a hello world script"),
    _exec("fix this function"),
    _exec("build jwt authentication"),
    _exec("deploy application"),
    _exec("edit main.py"),
]

# ── MIXED — greeting glued to a real request → execution (intent wins) ──────
MIXED = [
    _exec("hi and create a file"),
    _exec("hello write main.py"),
    _exec("what is python and create notes.txt"),
]

DATASET = CONVERSATION + EXECUTION + MIXED
