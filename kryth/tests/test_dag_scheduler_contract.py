"""DAG Scheduler — PROTECTED CONTRACT.

Freezes the correctness of TaskDAG.layers() (topological layering used for
parallel scheduling). The historical bug: `completed` was mutated mid-pass, so
a dependent node could be merged into its dependency's layer — collapsing
layer count and breaking parallel-scheduling correctness (it was hash-order
dependent, hence "occasionally 1 layer instead of 2").

The contract is validated against an INDEPENDENT reference layering computed
by a longest-path (critical-path) algorithm. Matching the reference proves
two things at once:
  • correctness  — every node sits one layer below its deepest dependency
  • max parallelism — layer count equals the critical-path length, i.e. the
    minimum number of sequential waves possible. A fix that serialized work
    would inflate the layer count and fail here.

Covers: linear, parallel fan-in, diamond, complex multi-root/multi-leaf, and
200+ random DAGs. Plus invariants: no duplicate execution, no missing nodes,
deterministic output, termination on cycles, and cycle rejection at parse.
"""

import random
from collections import deque

import pytest

from agent.orchestration.task_dag import TaskDAG, TaskNode, _parse_llm_nodes


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_dag(edges):
    """edges: dict id -> list_of_dependency_ids. Insertion order = sorted ids
    unless an explicit order list is given by the caller building it."""
    dag = TaskDAG(name="t")
    for nid, deps in edges.items():
        dag.add(TaskNode(
            id=nid, name=nid.upper(), description="",
            capabilities_required=["x"], dependencies=list(deps),
        ))
    return dag


def _reference_layer_index(edges):
    """Independent longest-path layering. Returns {id: layer_index}.
    layer(root)=0; layer(n)=1+max(layer(dep))."""
    indeg = {n: 0 for n in edges}
    adj = {n: [] for n in edges}
    for n, deps in edges.items():
        for d in deps:
            adj[d].append(n)
            indeg[n] += 1
    q = deque([n for n in edges if indeg[n] == 0])
    order = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    assert len(order) == len(edges), "test DAG is cyclic — fix the generator"
    layer = {}
    for u in order:
        layer[u] = 0 if not edges[u] else 1 + max(layer[d] for d in edges[u])
    return layer


def _assert_valid_layering(dag, edges):
    layers = dag.layers()

    # Map each node to the layer index it appears in.
    pos = {}
    for i, layer in enumerate(layers):
        for node in layer:
            assert node.id not in pos, f"DUPLICATE: {node.id} in layers {pos[node.id]} and {i}"
            pos[node.id] = i

    # No missing nodes.
    assert set(pos) == set(edges), (
        f"MISSING/EXTRA nodes: layered={set(pos)} expected={set(edges)}"
    )

    # Every dependency is in a STRICTLY earlier layer (ordering correctness).
    for nid, deps in edges.items():
        for d in deps:
            assert pos[d] < pos[nid], (
                f"ORDERING: {nid} (layer {pos[nid]}) depends on {d} (layer {pos[d]})"
            )

    # Matches the independent reference → correctness + maximum parallelism.
    ref = _reference_layer_index(edges)
    assert max(ref.values(), default=0) + 1 == len(layers), (
        f"LAYER COUNT: got {len(layers)}, critical-path needs {max(ref.values(), default=0) + 1}"
    )
    for nid in edges:
        assert pos[nid] == ref[nid], (
            f"LAYER ASSIGNMENT: {nid} in layer {pos[nid]}, reference says {ref[nid]}"
        )
    return layers


# ── Canonical shapes ───────────────────────────────────────────────────────

def test_linear_chain():
    # A -> B -> C  → three sequential layers, one node each.
    edges = {"a": [], "b": ["a"], "c": ["b"]}
    layers = _assert_valid_layering(_make_dag(edges), edges)
    assert len(layers) == 3
    assert all(len(l) == 1 for l in layers)


def test_parallel_fan_in():
    # A, B, C independent → all in layer 0; D depends on all three → layer 1.
    edges = {"a": [], "b": [], "c": [], "d": ["a", "b", "c"]}
    layers = _assert_valid_layering(_make_dag(edges), edges)
    assert len(layers) == 2
    assert {n.id for n in layers[0]} == {"a", "b", "c"}
    assert {n.id for n in layers[1]} == {"d"}


def test_diamond():
    # A → (B, C) → D
    edges = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}
    layers = _assert_valid_layering(_make_dag(edges), edges)
    assert len(layers) == 3
    assert {n.id for n in layers[0]} == {"a"}
    assert {n.id for n in layers[1]} == {"b", "c"}
    assert {n.id for n in layers[2]} == {"d"}


def test_complex_multi_root_multi_leaf():
    # Two roots (a, b), shared middle, two leaves (e, f).
    edges = {
        "a": [], "b": [],
        "c": ["a"], "d": ["a", "b"],
        "e": ["c", "d"], "f": ["d"],
    }
    layers = _assert_valid_layering(_make_dag(edges), edges)
    assert {n.id for n in layers[0]} == {"a", "b"}
    assert {n.id for n in layers[1]} == {"c", "d"}
    assert {n.id for n in layers[2]} == {"e", "f"}


def test_all_independent_single_layer():
    edges = {x: [] for x in "abcdef"}
    layers = _assert_valid_layering(_make_dag(edges), edges)
    assert len(layers) == 1
    assert len(layers[0]) == 6  # full parallelism preserved


def test_single_node():
    edges = {"only": []}
    layers = _assert_valid_layering(_make_dag(edges), edges)
    assert len(layers) == 1 and layers[0][0].id == "only"


# ── The historical regression (a, b → c) — must be 2 layers, every run ─────

def test_regression_dependent_not_merged_into_dependency_layer():
    edges = {"a": [], "b": [], "c": ["a", "b"]}
    for _ in range(200):  # hammer: the old bug surfaced only on some hash orders
        dag = _make_dag(edges)
        layers = dag.layers()
        assert len(layers) == 2, f"REGRESSION: c merged into layer 0 → {[[n.id for n in l] for l in layers]}"
        assert {n.id for n in layers[0]} == {"a", "b"}
        assert {n.id for n in layers[1]} == {"c"}


# ── Determinism ────────────────────────────────────────────────────────────

def test_layering_is_deterministic_across_calls():
    edges = {"a": [], "b": [], "c": ["a"], "d": ["a", "b"], "e": ["c", "d"]}
    dag = _make_dag(edges)
    first = [[n.id for n in l] for l in dag.layers()]
    for _ in range(50):
        assert [[n.id for n in l] for l in dag.layers()] == first


# ── Random DAG fuzzing (200+) ──────────────────────────────────────────────

def _random_dag(rng, n):
    """Build a guaranteed-acyclic DAG: node i may depend only on nodes < i."""
    ids = [f"n{i}" for i in range(n)]
    edges = {}
    for i, nid in enumerate(ids):
        if i == 0:
            edges[nid] = []
            continue
        k = rng.randint(0, min(i, 4))
        deps = rng.sample(ids[:i], k)
        edges[nid] = deps
    return edges


@pytest.mark.parametrize("seed", range(220))
def test_random_dags(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 14)
    edges = _random_dag(rng, n)
    _assert_valid_layering(_make_dag(edges), edges)


# ── Termination / cycle safety ─────────────────────────────────────────────

def test_layers_terminate_on_cycle_failsafe():
    # layers() must never infinite-loop even if a cycle slips in. The fail-safe
    # emits one node per pass; all nodes must still appear exactly once.
    dag = TaskDAG(name="cyclic")
    dag.add(TaskNode(id="x", name="X", description="", capabilities_required=["c"], dependencies=["y"]))
    dag.add(TaskNode(id="y", name="Y", description="", capabilities_required=["c"], dependencies=["x"]))
    layers = dag.layers()
    seen = [n.id for l in layers for n in l]
    assert sorted(seen) == ["x", "y"]          # no missing, no duplicate
    assert len(seen) == len(set(seen))


def test_missing_dependency_does_not_hang():
    # Dependency on a non-existent node: must terminate and include the node.
    dag = TaskDAG(name="m")
    dag.add(TaskNode(id="a", name="A", description="", capabilities_required=["c"], dependencies=["ghost"]))
    layers = dag.layers()
    assert [n.id for l in layers for n in l] == ["a"]


def test_parser_rejects_cycles():
    # _parse_llm_nodes must reject a cyclic node set (returns None → fixed DAG).
    cyclic = [
        {"id": "a", "name": "A", "description": "", "depends_on": ["b"]},
        {"id": "b", "name": "B", "description": "", "depends_on": ["a"]},
    ]
    assert _parse_llm_nodes(cyclic, ["backend"]) is None


def test_parser_accepts_valid_dag():
    valid = [
        {"id": "a", "name": "A", "description": "", "depends_on": []},
        {"id": "b", "name": "B", "description": "", "depends_on": ["a"]},
    ]
    dag = _parse_llm_nodes(valid, ["backend"])
    assert dag is not None
    edges = {n.id: n.dependencies for n in dag.nodes.values()}
    _assert_valid_layering(dag, edges)
