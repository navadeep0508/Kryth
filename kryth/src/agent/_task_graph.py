"""Task-DAG runner: a thin orchestrator on top of spawn_agents_parallel.

Lets the agent decompose a goal into a directed acyclic graph of
sub-tasks, declare dependencies, and have the runner execute them in
topological order — fanning out independent siblings in parallel and
threading upstream outputs into downstream prompts.

Task shape (each entry):

    {
      "id":         "kebab-case-id",       # required, unique
      "prompt":     "the subagent's instructions",  # required
      "description":"one-line label",       # required
      "depends_on": ["id1", "id2"],         # optional list of upstream ids
      "max_turns":  6,                      # optional, default 6
    }

Within a prompt, ``{{id}}`` placeholders are substituted with the
upstream task's final output before dispatch. The runner refuses
duplicate ids, unknown dependencies, and cycles up front so a misformed
graph fails fast with a clear error.

NOTE: file-system / git operations across siblings share the same cwd
and are not serialised. Fan-out is best for READS; sequential edges
should encode any write ordering.
"""

from __future__ import annotations

from agent.tools._results import err


_PLACEHOLDER_OPEN = "{{"
_PLACEHOLDER_CLOSE = "}}"


def _validate_graph(tasks):
    """Check shape, dedupe ids, validate dep edges, detect cycles."""
    if not isinstance(tasks, list) or not tasks:
        return err("BAD_ARGS", "tasks must be a non-empty list")

    seen_ids: set[str] = set()
    nodes: list[dict] = []
    for i, raw in enumerate(tasks):
        if not isinstance(raw, dict):
            return err("BAD_ARGS", f"task {i} must be an object")
        tid = raw.get("id")
        prompt = raw.get("prompt")
        desc = raw.get("description")
        deps = raw.get("depends_on", [])
        if not isinstance(tid, str) or not tid.strip():
            return err("BAD_ARGS", f"task {i} missing non-empty 'id'")
        if tid in seen_ids:
            return err("BAD_ARGS", f"duplicate task id: {tid!r}")
        seen_ids.add(tid)
        if not isinstance(prompt, str) or not prompt.strip():
            return err("BAD_ARGS", f"task {tid!r} missing non-empty 'prompt'")
        if not isinstance(desc, str) or not desc.strip():
            return err("BAD_ARGS", f"task {tid!r} missing non-empty 'description'")
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            return err("BAD_ARGS", f"task {tid!r} 'depends_on' must be a list of strings")
        mt_raw = raw.get("max_turns", 6)
        try:
            mt = max(1, min(int(mt_raw), 30))
        except (TypeError, ValueError):
            return err("BAD_ARGS", f"task {tid!r} max_turns must be an integer")
        nodes.append({
            "id": tid.strip(),
            "prompt": prompt,
            "description": desc.strip(),
            "depends_on": list(deps),
            "max_turns": mt,
        })

    # Validate edges + topo-sort (Kahn's algorithm); cycle detection
    # falls out of "any node still unscheduled after the loop terminates".
    id_to_node = {n["id"]: n for n in nodes}
    for n in nodes:
        for d in n["depends_on"]:
            if d not in id_to_node:
                return err(
                    "BAD_ARGS",
                    f"task {n['id']!r} depends on unknown id {d!r}",
                )
            if d == n["id"]:
                return err("BAD_ARGS", f"task {n['id']!r} depends on itself")

    indegree = {n["id"]: len(n["depends_on"]) for n in nodes}
    order: list[list[dict]] = []
    remaining = list(nodes)
    while remaining:
        layer = [n for n in remaining if indegree[n["id"]] == 0]
        if not layer:
            stuck = [n["id"] for n in remaining]
            return err(
                "BAD_ARGS",
                f"cycle detected among tasks: {stuck}",
            )
        order.append(layer)
        layer_ids = {n["id"] for n in layer}
        for n in remaining:
            if n["id"] not in layer_ids:
                for d in n["depends_on"]:
                    if d in layer_ids:
                        indegree[n["id"]] -= 1
        remaining = [n for n in remaining if n["id"] not in layer_ids]

    return order  # list of layers (each layer = list of nodes)


def _substitute(prompt: str, results: dict[str, str]) -> str:
    """Replace ``{{id}}`` placeholders with the (truncated) output of
    that upstream task. Unknown placeholders are left as-is so the
    subagent can surface that fact in its summary."""
    if _PLACEHOLDER_OPEN not in prompt:
        return prompt
    out = prompt
    for tid, value in results.items():
        token = f"{_PLACEHOLDER_OPEN}{tid}{_PLACEHOLDER_CLOSE}"
        if token in out:
            # Cap each substitution so a single huge upstream output
            # can't blow past the subagent's context budget.
            out = out.replace(token, value[:6000])
    return out


def run_task_graph(tasks, max_concurrency: int = 3):
    """Execute a DAG of subagent tasks.

    Returns one aggregated string ordered by input position. Each task's
    block is prefixed with its id + description so the agent can pick
    out individual outputs.
    """
    validated = _validate_graph(tasks)
    if isinstance(validated, str):
        return validated  # error envelope

    from agent.tools._subagent import spawn_agents_parallel

    results: dict[str, str] = {}
    for layer in validated:
        if len(layer) == 1:
            node = layer[0]
            prompt = _substitute(node["prompt"], results)
            outcome = spawn_agents_parallel(
                [{
                    "description": node["description"],
                    "prompt": prompt,
                    "max_turns": node["max_turns"],
                }],
                max_concurrency=1,
            )
            # spawn_agents_parallel returns a single aggregated string.
            # Each layer-of-one becomes a one-task fan-out; we strip the
            # "=== [0] ===" header to recover the raw output.
            results[node["id"]] = _strip_single_header(outcome)
            continue

        batch = [
            {
                "description": n["description"],
                "prompt": _substitute(n["prompt"], results),
                "max_turns": n["max_turns"],
            }
            for n in layer
        ]
        outcome = spawn_agents_parallel(batch, max_concurrency=max_concurrency)
        per_task = _split_parallel_output(outcome, [n["id"] for n in layer])
        for n in layer:
            results[n["id"]] = per_task.get(n["id"], "")

    parts: list[str] = []
    for raw in tasks:
        tid = raw["id"]
        desc = raw.get("description", "")
        body = results.get(tid, "(no result)")
        parts.append(f"### task: {tid} — {desc}\n{body}")
    return "\n\n".join(parts)


def _strip_single_header(text: str) -> str:
    """spawn_agents_parallel emits ``=== [0] description ===\\n<body>``;
    for a single-task fan-out, that header is noise."""
    if not text.startswith("=== ["):
        return text
    newline = text.find("\n")
    return text[newline + 1:] if newline >= 0 else text


def _split_parallel_output(text: str, ids_in_order: list[str]) -> dict[str, str]:
    """Map the aggregated ``=== [i] desc ===`` blocks back to task ids
    using positional order (spawn_agents_parallel preserves input order).
    """
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("=== [") and "===" in line[5:]:
            if current:
                blocks.append("\n".join(current).rstrip())
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).rstrip())

    out: dict[str, str] = {}
    for idx, tid in enumerate(ids_in_order):
        out[tid] = blocks[idx] if idx < len(blocks) else ""
    return out


__all__ = ["run_task_graph"]
