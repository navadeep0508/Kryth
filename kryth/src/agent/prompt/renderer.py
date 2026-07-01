"""Prompt Renderer — renders exactly ONE system message from PromptContext.

Replaces: dynamic system message injection, sanitizer mutations, provider mutations.
"""

from __future__ import annotations

from agent.prompt.context_builder import PromptContext


# ── Core Prompt Templates ──────────────────────────────────────────────────

_SYSTEM_PROMPT = """KRYTH coding agent. React to input — match your response to what is actually asked.

RULES:
- Greetings/questions → answer directly in text. No tools needed.
- Read/fix/build/run tasks → use tools. Pick the most direct tool.
- Scale effort to task size: tiny=1-2 actions, large=persist until fully working.
- Output: no markdown fences, no XML tags, no hand-written JSON tool calls.
- Paths: forward slashes, relative to project root.
- Errors start with [ERROR CODE]. Retry with more context or surface to user.
- Do exactly what is asked. No extra files, no unsolicited refactors.

TOOL USAGE:
- Call ONE tool per response. Wait for result before next call.
- Serial: same-file writes, installs, git push.
- Read first if unsure about code. Never run code you haven't read.
- Verify after writes. For large builds: fix loop until it runs.

TASK TYPES:
tiny (quick answer, one file read): 1-2 actions, done.
simple/fix: minimum tools, verify, stop. No bonus changes.
build: plan → write_file(all) → run_command(verify) → fix loop until working → done.
Never cut corners on builds. Never over-engineer simple tasks.

STOP when done. Never add unrequested files, refactors, or tests.

BUILD QUALITY (skip for simple/fix):
- Web: html+css+js, real content, real styles, responsive. Run dev server, fix errors.
- Python: entrypoint + requirements.txt if deps used.
- Node: package.json with start script.
"""

_TRIVIAL_PROMPT = """KRYTH coding agent. Simple single-file task. No preamble.

PARALLEL RULE: Emit write_file AND run_command in SAME response if both needed.
PATH RULE: In run_command, always use FULL ABSOLUTE PATH from write_file.
One response = both tool calls together. Stop immediately after both succeed.
"""

_READ_ONLY_PROMPT = """KRYTH coding agent. JSON response only.

If the user asks to read a specific file (e.g. 'read hello.py'): call read_file(path=X) immediately.
Do NOT list files first. Do NOT glob. Do NOT search.
If they ask to read the project/directory (e.g. 'read this project' or 'read the repo'):
  call list_files(".") first to discover project structure.
If the file exists and was successfully read, then explain its contents in plain English.
If read fails (file not found, error), say so plainly.
"""


# ── Renderer ───────────────────────────────────────────────────────────────

def render_system_prompt(ctx: PromptContext) -> str:
    """Render exactly ONE system prompt string from context."""
    # Pick base template
    if ctx.is_trivial:
        if ctx.is_read_only:
            base = _READ_ONLY_PROMPT
        else:
            base = _TRIVIAL_PROMPT
    else:
        base = _SYSTEM_PROMPT

    parts = [base]

    # CWD
    parts.append(f"CWD: {ctx.cwd}")

    # Project doc
    if ctx.project_doc:
        parts.append(ctx.project_doc)

    # Git state
    if ctx.git_state:
        parts.append(ctx.git_state)

    # Project map
    if ctx.project_map:
        parts.append(f"Project files:\n{ctx.project_map}")

    # Experience (condensed)
    if ctx.experience_summary:
        parts.append(f"[Memory: similar past tasks]\n{ctx.experience_summary}")

    # File preload (list only)
    if ctx.file_preload:
        parts.append(f"[Preloaded relevant files]\n{ctx.file_preload}")

    # Graph context (condensed)
    if ctx.graph_context:
        parts.append(f"[Graph context]\n{ctx.graph_context}")

    # Browser hint
    if ctx.has_browser and not ctx.is_trivial:
        parts.append("BROWSER: use browser_use_task() for all multi-step web tasks (single call).")

    # Streaming hint
    if ctx.has_streaming and not ctx.is_trivial:
        parts.append("STREAMING (files >200 lines): use write_file_begin / write_file_chunk / write_file_finalize.")

    return "\n\n".join(parts)


def render_initial_messages(
    ctx: PromptContext,
    user_input: str,
) -> list[dict]:
    """Build the initial message list with EXACTLY ONE system message."""
    system_content = render_system_prompt(ctx)
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_input},
    ]


# ── Validation ─────────────────────────────────────────────────────────────

def validate_messages(messages: list[dict]) -> None:
    """Validate message schema. Raises ValueError on violation."""
    if not messages:
        return

    system_count = sum(1 for m in messages if m.get("role") == "system")
    if system_count != 1:
        raise ValueError(f"Exactly ONE system message required, found {system_count}")

    # First message must be system
    if messages[0].get("role") != "system":
        raise ValueError("First message must be system")

    # All messages must have role and content
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            raise ValueError(f"Message {i}: must be dict")
        if "role" not in m:
            raise ValueError(f"Message {i}: missing 'role'")
        if "content" not in m:
            raise ValueError(f"Message {i}: missing 'content'")
        if m["role"] not in ("system", "user", "assistant", "tool"):
            raise ValueError(f"Message {i}: invalid role '{m['role']}'")