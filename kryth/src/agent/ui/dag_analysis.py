"""Critical-path & bottleneck analysis over a DAG node list (Phase 9).

Pure functions — NO scheduler/execution coupling. Input is the same node shape
the UI already receives via ``DAG_UPDATE``::

    {"id", "label", "status", "deps": [ids], "duration": "12s" | seconds, "owner"}

Output feeds a presentation panel. Nothing here mutates scheduler state; it only
reads the snapshot the scheduler already publishes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_DONE = {"complete", "done"}
_RUNNING = {"running", "active"}
_BLOCKED = {"blocked", "waiting", "pending"}


def _secs(node: dict) -> float:
    """Best-effort weight for a node. Accepts numeric seconds or '12s'/'1m30s'.
    Unknown/zero falls back to 1.0 so every node has non-zero weight."""
    d = node.get("duration")
    if isinstance(d, (int, float)):
        return float(d) or 1.0
    if isinstance(d, str):
        s = d.strip().lower()
        total = 0.0
        num = ""
        for ch in s:
            if ch.isdigit() or ch == ".":
                num += ch
            elif ch == "m":
                total += float(num or 0) * 60
                num = ""
            elif ch == "s":
                total += float(num or 0)
                num = ""
        if num:  # bare number, treat as seconds
            total += float(num)
        return total or 1.0
    return 1.0


@dataclass
class CriticalPath:
    path: list = field(default_factory=list)     # ordered node ids on the longest chain
    labels: list = field(default_factory=list)   # human labels for those ids
    length_s: float = 0.0                          # summed weight of the chain
    bottleneck_id: str = ""                        # node blocking the most successors
    bottleneck_label: str = ""
    blocked_workers: int = 0                        # nodes waiting (transitively) on the bottleneck
    impact: str = "low"                             # low | medium | high

    def to_dict(self) -> dict:
        return {
            "path": self.path, "labels": self.labels, "length_s": self.length_s,
            "bottleneck_id": self.bottleneck_id, "bottleneck_label": self.bottleneck_label,
            "blocked_workers": self.blocked_workers, "impact": self.impact,
        }


def _index(nodes: list) -> dict:
    return {n.get("id"): n for n in nodes if n.get("id") is not None}


def critical_path(nodes: list) -> CriticalPath:
    """Longest weighted path through the dependency DAG.

    Edges run dep → node. The critical path is the maximum-weight root-to-leaf
    chain; its length is the theoretical floor on wall-clock time. The
    bottleneck is the not-yet-complete node with the largest transitive
    successor set (the most work waiting behind it)."""
    nodes = [n for n in (nodes or []) if isinstance(n, dict)]
    if not nodes:
        return CriticalPath()
    by_id = _index(nodes)

    # children adjacency (dep -> [dependents])
    children: dict[Any, list] = {nid: [] for nid in by_id}
    for n in nodes:
        for dep in (n.get("deps") or []):
            if dep in children:
                children[dep].append(n.get("id"))

    # Longest path via memoized DFS over the DAG (deps assumed acyclic; a cycle
    # guard prevents infinite recursion if a bad snapshot slips through).
    best_len: dict[Any, float] = {}
    best_next: dict[Any, Any] = {}

    def longest(nid, stack):
        if nid in best_len:
            return best_len[nid]
        if nid in stack:  # cycle guard
            return 0.0
        stack.add(nid)
        w = _secs(by_id[nid])
        best = w
        nxt = None
        for c in children.get(nid, []):
            cand = w + longest(c, stack)
            if cand > best:
                best, nxt = cand, c
        stack.discard(nid)
        best_len[nid] = best
        best_next[nid] = nxt
        return best

    roots = [n.get("id") for n in nodes if not n.get("deps")]
    if not roots:
        roots = [nodes[0].get("id")]
    start = max(roots, key=lambda r: longest(r, set()))

    # reconstruct chain
    path = []
    cur = start
    seen = set()
    while cur is not None and cur not in seen:
        path.append(cur)
        seen.add(cur)
        cur = best_next.get(cur)
    labels = [str(by_id[p].get("label") or p) for p in path]

    # Bottleneck: incomplete node with the most transitive successors.
    def descendants(nid):
        out, stack = set(), list(children.get(nid, []))
        while stack:
            c = stack.pop()
            if c in out:
                continue
            out.add(c)
            stack.extend(children.get(c, []))
        return out

    bottleneck_id, blocked = "", 0
    for n in nodes:
        st = str(n.get("status", "")).lower()
        if st in _DONE:
            continue
        d = len(descendants(n.get("id")))
        if d > blocked:
            blocked, bottleneck_id = d, n.get("id")
    # If nothing has successors, the active node itself is the bottleneck.
    if not bottleneck_id:
        running = [n for n in nodes if str(n.get("status", "")).lower() in _RUNNING]
        if running:
            bottleneck_id = running[0].get("id")

    impact = "high" if blocked >= 3 else "medium" if blocked >= 1 else "low"
    return CriticalPath(
        path=path, labels=labels, length_s=round(best_len.get(start, 0.0), 1),
        bottleneck_id=bottleneck_id or "",
        bottleneck_label=str(by_id.get(bottleneck_id, {}).get("label") or bottleneck_id or ""),
        blocked_workers=blocked, impact=impact,
    )
