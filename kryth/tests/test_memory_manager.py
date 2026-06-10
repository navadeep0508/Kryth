"""
Comprehensive pytest test suite for the KRYTH cognitive memory system.

Run with:
    pytest kryth/tests/test_memory_manager.py -v
"""

from __future__ import annotations

import sys
import time
import uuid
import pathlib

# Ensure the src directory is importable without installation
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    """Return a short unique hex string for isolated project hashes."""
    return uuid.uuid4().hex[:8]


# ===========================================================================
# test_memory_score
# ===========================================================================

class TestMemoryScore:
    """Tests for MemoryScore dataclass and Scorer utility class."""

    def test_composite_score_in_unit_interval(self):
        from agent.memory.scoring import MemoryScore
        ms = MemoryScore(
            entry_id="abc",
            importance=0.8,
            confidence=0.7,
            access_count=5,
            success_rate=0.9,
        )
        score = ms.composite_score()
        assert 0.0 <= score <= 1.0, f"Expected score in [0, 1] but got {score}"

    def test_composite_score_low_quality(self):
        from agent.memory.scoring import MemoryScore
        ms = MemoryScore(
            entry_id="low",
            importance=0.0,
            confidence=0.0,
            access_count=0,
            success_rate=0.0,
        )
        assert ms.composite_score() < 0.5

    def test_composite_score_high_quality(self):
        from agent.memory.scoring import MemoryScore
        ms = MemoryScore(
            entry_id="high",
            importance=1.0,
            confidence=1.0,
            access_count=100,
            success_rate=1.0,
        )
        assert ms.composite_score() > 0.5

    def test_is_expired_no_ttl(self):
        from agent.memory.scoring import MemoryScore
        ms = MemoryScore(entry_id="x", ttl=None)
        assert not ms.is_expired()

    def test_is_expired_future_ttl(self):
        from agent.memory.scoring import MemoryScore
        ms = MemoryScore(entry_id="x", ttl=9999.0, created_at=time.time())
        assert not ms.is_expired()

    def test_is_expired_past_ttl(self):
        from agent.memory.scoring import MemoryScore
        # Created 100 seconds ago with a ttl of 1 second → expired
        ms = MemoryScore(
            entry_id="x",
            ttl=1.0,
            created_at=time.time() - 100,
        )
        assert ms.is_expired()

    def test_should_promote_true(self):
        from agent.memory.scoring import MemoryScore
        ms = MemoryScore(
            entry_id="promo",
            importance=0.9,
            confidence=0.9,
            access_count=50,
            success_rate=0.9,
            ttl=None,
        )
        assert ms.should_promote()

    def test_should_promote_false_low_score(self):
        from agent.memory.scoring import MemoryScore
        ms = MemoryScore(
            entry_id="nopromo",
            importance=0.1,
            confidence=0.1,
            access_count=0,
            success_rate=0.1,
        )
        assert not ms.should_promote()

    def test_should_promote_false_expired(self):
        from agent.memory.scoring import MemoryScore
        ms = MemoryScore(
            entry_id="expired",
            importance=1.0,
            confidence=1.0,
            access_count=100,
            success_rate=1.0,
            ttl=1.0,
            created_at=time.time() - 100,
        )
        assert not ms.should_promote()

    def test_should_archive_true_low_score(self):
        from agent.memory.scoring import MemoryScore
        ms = MemoryScore(
            entry_id="archive_me",
            importance=0.0,
            confidence=0.0,
            access_count=0,
            success_rate=0.0,
        )
        assert ms.should_archive()

    def test_should_archive_true_expired(self):
        from agent.memory.scoring import MemoryScore
        ms = MemoryScore(
            entry_id="exp_arch",
            importance=0.5,
            confidence=0.5,
            ttl=1.0,
            created_at=time.time() - 100,
        )
        assert ms.should_archive()

    def test_should_archive_false_high_quality(self):
        from agent.memory.scoring import MemoryScore
        ms = MemoryScore(
            entry_id="keep",
            importance=1.0,
            confidence=1.0,
            access_count=100,
            success_rate=1.0,
        )
        assert not ms.should_archive()

    def test_scorer_rank_descending(self):
        from agent.memory.scoring import MemoryScore, Scorer
        low = MemoryScore(entry_id="low", importance=0.1, confidence=0.1, success_rate=0.1)
        high = MemoryScore(entry_id="high", importance=0.9, confidence=0.9, success_rate=0.9, access_count=50)
        ranked = Scorer.rank([low, high])
        assert ranked[0].entry_id == "high"
        assert ranked[1].entry_id == "low"

    def test_scorer_filter_live_removes_expired(self):
        from agent.memory.scoring import MemoryScore, Scorer
        live = MemoryScore(entry_id="live", ttl=9999.0, created_at=time.time())
        expired = MemoryScore(entry_id="dead", ttl=1.0, created_at=time.time() - 100)
        result = Scorer.filter_live([live, expired])
        ids = [s.entry_id for s in result]
        assert "live" in ids
        assert "dead" not in ids

    def test_scorer_candidates_for_promotion(self):
        from agent.memory.scoring import MemoryScore, Scorer
        good = MemoryScore(entry_id="g", importance=0.9, confidence=0.9, access_count=50, success_rate=0.9)
        bad = MemoryScore(entry_id="b", importance=0.1, confidence=0.1, access_count=0, success_rate=0.1)
        promo = Scorer.candidates_for_promotion([good, bad])
        ids = [s.entry_id for s in promo]
        assert "g" in ids
        assert "b" not in ids

    def test_scorer_candidates_for_archive(self):
        from agent.memory.scoring import MemoryScore, Scorer
        low = MemoryScore(entry_id="low", importance=0.0, confidence=0.0, access_count=0, success_rate=0.0)
        high = MemoryScore(entry_id="high", importance=1.0, confidence=1.0, access_count=100, success_rate=1.0)
        arch = Scorer.candidates_for_archive([low, high])
        ids = [s.entry_id for s in arch]
        assert "low" in ids
        assert "high" not in ids


# ===========================================================================
# test_execution_memory
# ===========================================================================

class TestExecutionMemory:
    """Tests for ExecutionMemory — command history and repair patterns."""

    @pytest.fixture()
    def em(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        from agent.memory import execution_memory as _em_mod
        # Force reimport so home_dir() picks up the patched env
        import importlib
        importlib.reload(_em_mod)
        from agent.memory.execution_memory import ExecutionMemory
        return ExecutionMemory(project_hash=f"test_exec_{_uid()}")

    def test_record_success_returns_int(self, em):
        row_id = em.record("pytest", "test", "success")
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_record_failure(self, em):
        row_id = em.record(
            "bad_cmd", "shell", "failure",
            error_type="RuntimeError",
            error_message="something broke",
        )
        assert isinstance(row_id, int)

    def test_get_success_rate_all_success(self, em):
        em.record("cmd1", "build", "success")
        em.record("cmd2", "build", "success")
        rate = em.get_success_rate("build")
        assert rate == 1.0

    def test_get_success_rate_mixed(self, em):
        em.record("a", "shell", "success")
        em.record("b", "shell", "failure")
        rate = em.get_success_rate("shell")
        assert rate == pytest.approx(0.5)

    def test_get_success_rate_empty(self, em):
        assert em.get_success_rate("nonexistent") == 0.0

    def test_get_failure_patterns(self, em):
        em.record("x", "shell", "failure", error_type="ImportError")
        em.record("y", "shell", "failure", error_type="ImportError")
        em.record("z", "shell", "failure", error_type="TypeError")
        patterns = em.get_failure_patterns()
        assert patterns["ImportError"] == 2
        assert patterns["TypeError"] == 1

    def test_get_project_commands_most_frequent(self, em):
        # Record "pytest" 3 times and "nosetests" 1 time for 'test' type
        em.record("pytest", "test", "success")
        em.record("pytest", "test", "success")
        em.record("pytest", "test", "success")
        em.record("nosetests", "test", "success")
        cmds = em.get_project_commands()
        assert cmds.get("test") == "pytest"

    def test_learn_repair_pattern_and_get_strategy(self, em):
        em.learn_repair_pattern(
            error_type="ModuleNotFoundError",
            error_pattern="No module named",
            repair_command="pip install missing_pkg",
        )
        strategy = em.get_repair_strategy(
            "ModuleNotFoundError", "No module named 'missing_pkg'"
        )
        assert "pip install missing_pkg" in strategy

    def test_get_repair_strategy_empty(self, em):
        result = em.get_repair_strategy("UnknownError")
        assert result == []


# ===========================================================================
# test_workflow_memory
# ===========================================================================

class TestWorkflowMemory:
    """Tests for WorkflowMemory — workflow pattern recording and retrieval."""

    @pytest.fixture()
    def wm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        import importlib
        import agent.memory.workflow_memory as wm_mod
        importlib.reload(wm_mod)
        from agent.memory.workflow_memory import WorkflowMemory
        return WorkflowMemory(project_hash=f"test_wf_{_uid()}")

    def test_register_workflow_returns_id(self, wm):
        wf_id = wm.register_workflow("deploy", ["step1", "step2"], intent_type="deploy")
        assert isinstance(wf_id, int)
        assert wf_id > 0

    def test_register_workflow_upsert(self, wm):
        id1 = wm.register_workflow("w", ["a"])
        id2 = wm.register_workflow("w", ["b"])
        # Upsert: same name should keep same id
        assert id1 == id2

    def test_record_run_success_increments_count(self, wm):
        wf_id = wm.register_workflow("build", ["compile", "link"])
        wm.record_run(wf_id, "success", duration_sec=1.5)
        wm.record_run(wf_id, "success", duration_sec=2.0)
        wfs = wm.list_workflows()
        match = [w for w in wfs if w.name == "build"]
        assert match and match[0].success_count == 2

    def test_record_run_failure_increments_fail_count(self, wm):
        wf_id = wm.register_workflow("flaky", ["step"])
        wm.record_run(wf_id, "failure")
        wfs = wm.list_workflows()
        match = [w for w in wfs if w.name == "flaky"]
        assert match and match[0].fail_count == 1

    def test_get_best_workflow_requires_min_2_runs(self, wm):
        wf_id = wm.register_workflow("single_run", ["s1"])
        wm.record_run(wf_id, "success")
        # Only 1 run → should not be returned
        best = wm.get_best_workflow()
        assert best is None or best.name != "single_run"

    def test_get_best_workflow_respects_min_success_rate(self, wm):
        wf_id = wm.register_workflow("bad_wf", ["s"])
        wm.record_run(wf_id, "failure")
        wm.record_run(wf_id, "failure")
        best = wm.get_best_workflow(min_success_rate=0.5)
        assert best is None

    def test_get_best_workflow_returns_highest_rate(self, wm):
        id1 = wm.register_workflow("good", ["s"], intent_type="build")
        wm.record_run(id1, "success")
        wm.record_run(id1, "success")
        id2 = wm.register_workflow("mediocre", ["s"], intent_type="build")
        wm.record_run(id2, "success")
        wm.record_run(id2, "failure")
        best = wm.get_best_workflow(intent_type="build", min_success_rate=0.5)
        assert best is not None
        assert best.name == "good"

    def test_list_workflows_filtering(self, wm):
        wm.register_workflow("build_wf", ["b1"], intent_type="build")
        wm.register_workflow("test_wf", ["t1"], intent_type="test")
        build = wm.list_workflows(intent_type="build")
        assert all(w.intent_type == "build" for w in build)

    def test_get_stats_keys(self, wm):
        stats = wm.get_stats()
        assert "total_workflows" in stats
        assert "total_runs" in stats
        assert "avg_success_rate" in stats

    def test_workflow_pattern_success_rate_property(self, wm):
        from agent.memory.workflow_memory import WorkflowPattern
        wp = WorkflowPattern(name="x", steps=[], created_at=time.time(),
                             success_count=3, fail_count=1)
        assert wp.success_rate == pytest.approx(0.75)


# ===========================================================================
# test_agent_memory
# ===========================================================================

class TestAgentMemory:
    """Tests for AgentMemory — per-agent private memory store."""

    @pytest.fixture()
    def am(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        import importlib
        import agent.memory.agent_memory as am_mod
        importlib.reload(am_mod)
        from agent.memory.agent_memory import AgentMemory
        return AgentMemory(agent_id=f"agent_{_uid()}", project_hash="proj_test")

    def test_remember_and_recall(self, am):
        am.remember("key1", "value1")
        assert am.recall("key1") == "value1"

    def test_recall_missing_key_returns_none(self, am):
        assert am.recall("no_such_key") is None

    def test_forget_removes_key(self, am):
        am.remember("del_me", 42)
        am.forget("del_me")
        assert am.recall("del_me") is None

    def test_recall_by_category(self, am):
        am.remember("c1", "alpha", category="code")
        am.remember("c2", "beta", category="code")
        am.remember("d1", "delta", category="docs")
        result = am.recall_by_category("code")
        assert set(result.keys()) == {"c1", "c2"}

    def test_ttl_expiry(self, am):
        # Store with a 0.01s TTL, then wait briefly
        am.remember("exp_key", "will_expire", ttl=0.01)
        time.sleep(0.05)
        assert am.recall("exp_key") is None

    def test_merge_into_copies_high_access_count(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        from agent.memory.agent_memory import AgentMemory
        source = AgentMemory(agent_id=f"src_{_uid()}", project_hash="p")
        target = AgentMemory(agent_id=f"tgt_{_uid()}", project_hash="p")

        source.remember("k1", "v1")
        # Boost access_count to 2 by recalling twice
        source.recall("k1")
        source.recall("k1")

        source.remember("k2", "v2")  # access_count stays 0

        copied = source.merge_into(target)
        assert copied >= 1
        assert target.recall("k1") == "v1"
        # k2 should NOT have been copied (access_count < 2)
        assert target.recall("k2") is None

    def test_get_summary_structure(self, am):
        am.remember("x", 1, category="cat_a")
        summary = am.get_summary()
        assert "agent_id" in summary
        assert "entry_count" in summary
        assert "categories" in summary
        assert "top_keys" in summary
        assert summary["entry_count"] >= 1


# ===========================================================================
# test_team_memory
# ===========================================================================

class TestTeamMemory:
    """Tests for TeamMemory — shared parallel-agent memory."""

    @pytest.fixture()
    def tm(self):
        from agent.memory.agent_memory import TeamMemory
        return TeamMemory(project_hash=f"team_{_uid()}")

    def test_broadcast_and_query(self, tm):
        tm.broadcast("agent-1", "found_file", "/src/main.py")
        assert tm.query("found_file") == "/src/main.py"

    def test_query_missing_key_returns_none(self, tm):
        assert tm.query("nothing_here") is None

    def test_has_dedup_prevention(self, tm):
        assert not tm.has("new_key")
        tm.broadcast("agent-1", "new_key", "value")
        assert tm.has("new_key")

    def test_get_all_discoveries_latest_wins(self, tm):
        tm.broadcast("agent-1", "key", "first")
        tm.broadcast("agent-2", "key", "second")
        discoveries = tm.get_all_discoveries()
        assert discoveries["key"] == "second"

    def test_get_agent_contributions(self, tm):
        tm.broadcast("agent-1", "k1", "v1")
        tm.broadcast("agent-1", "k2", "v2")
        tm.broadcast("agent-2", "k3", "v3")
        contribs = tm.get_agent_contributions()
        assert contribs["agent-1"] == 2
        assert contribs["agent-2"] == 1

    def test_query_by_category(self, tm):
        tm.broadcast("agent-1", "file1", "/a.py", category="files")
        tm.broadcast("agent-1", "service1", "auth", category="services")
        files = tm.query_by_category("files")
        assert len(files) == 1
        assert files[0]["key"] == "file1"


# ===========================================================================
# test_failure_memory
# ===========================================================================

class TestFailureMemory:
    """Tests for FailureMemory — failure records and repair strategies."""

    @pytest.fixture()
    def fm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        import importlib
        import agent.memory.failure_memory as fm_mod
        importlib.reload(fm_mod)
        from agent.memory.failure_memory import FailureMemory
        return FailureMemory(project_hash=f"test_fail_{_uid()}")

    def test_record_failure_returns_id(self, fm):
        fid = fm.record_failure("build", error_type="CompileError",
                                error_message="undefined symbol")
        assert isinstance(fid, int) and fid > 0

    def test_record_repair_attempt(self, fm):
        fid = fm.record_failure("test", error_type="AssertionError")
        fm.record_repair_attempt(fid, "pytest --tb=short", "success")
        recent = fm.get_recent_failures()
        # The failure was not yet resolved, so it appears in recent
        # (repair attempt is logged on the record)
        ids = [r.id for r in recent]
        assert fid in ids

    def test_mark_resolved(self, fm):
        fid = fm.record_failure("build", error_type="LinkError")
        fm.mark_resolved(fid, "Fixed linker flags")
        recent = fm.get_recent_failures()
        ids = [r.id for r in recent]
        assert fid not in ids  # resolved → not in unresolved list

    def test_get_repair_strategy_from_resolved(self, fm):
        fid = fm.record_failure("runtime", error_type="SegFault",
                                error_message="segmentation fault")
        fm.record_repair_attempt(fid, "valgrind ./app", "success")
        fm.mark_resolved(fid, "Used valgrind to find the bug")
        strategy = fm.get_repair_strategy("SegFault")
        assert "valgrind ./app" in strategy

    def test_get_failure_patterns_resolution_rate(self, fm):
        fid1 = fm.record_failure("build", error_type="TimeoutError")
        fm.mark_resolved(fid1, "Fixed")
        fm.record_failure("build", error_type="TimeoutError")
        patterns = fm.get_failure_patterns()
        assert "TimeoutError" in patterns
        info = patterns["TimeoutError"]
        assert info["count"] == 2
        assert info["resolved_count"] == 1
        assert info["resolution_rate"] == pytest.approx(0.5)

    def test_get_recent_failures_limit(self, fm):
        for i in range(5):
            fm.record_failure("type", error_type=f"Err{i}")
        recent = fm.get_recent_failures(limit=3)
        assert len(recent) <= 3


# ===========================================================================
# test_decision_memory
# ===========================================================================

class TestDecisionMemory:
    """Tests for DecisionMemory — architectural/library decision records."""

    @pytest.fixture()
    def dm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        import importlib
        import agent.memory.decision_memory as dm_mod
        importlib.reload(dm_mod)
        from agent.memory.decision_memory import DecisionMemory
        return DecisionMemory(project_hash=f"test_dm_{_uid()}")

    def test_record_and_get_by_title(self, dm):
        dm.record(
            title="Auth Method",
            choice="JWT",
            rationale="Stateless for API",
            decision_type="architecture",
        )
        d = dm.get("Auth Method")
        assert d is not None
        assert d.choice == "JWT"

    def test_get_case_insensitive(self, dm):
        dm.record("MyDecision", "ChoiceX", "Reason", decision_type="other")
        d = dm.get("mydecision")
        assert d is not None

    def test_get_missing_returns_none(self, dm):
        assert dm.get("NoSuchDecision") is None

    def test_list_decisions_all(self, dm):
        dm.record("D1", "C1", "R1", decision_type="library")
        dm.record("D2", "C2", "R2", decision_type="architecture")
        decisions = dm.list_decisions()
        titles = [d.title for d in decisions]
        assert "D1" in titles and "D2" in titles

    def test_list_decisions_filtered(self, dm):
        dm.record("LibChoice", "pandas", "data analysis", decision_type="library")
        dm.record("ArchChoice", "monolith", "simplicity", decision_type="architecture")
        libs = dm.list_decisions(decision_type="library")
        assert all(d.decision_type == "library" for d in libs)

    def test_supersede(self, dm):
        old_id = dm.record("Framework", "Flask", "Simple", decision_type="framework")
        new_id = dm.record("Framework v2", "FastAPI", "Async support", decision_type="framework")
        dm.supersede(old_id, new_id)
        decisions = dm.list_decisions()
        ids = [d.id for d in decisions]
        assert old_id not in ids

    def test_search_substring(self, dm):
        dm.record("Caching Strategy", "Redis", "Fast in-memory cache", decision_type="architecture")
        results = dm.search("redis")
        assert any(d.choice.lower() == "redis" for d in results)

    def test_get_context_summary_markdown(self, dm):
        dm.record("DB Choice", "PostgreSQL", "ACID compliance", decision_type="architecture")
        summary = dm.get_context_summary()
        assert "##" in summary
        assert "PostgreSQL" in summary

    def test_get_context_summary_empty(self, dm):
        summary = dm.get_context_summary()
        assert "No decisions" in summary


# ===========================================================================
# test_knowledge_graph
# ===========================================================================

class TestKnowledgeGraph:
    """Tests for KnowledgeGraph — concept relationship graph."""

    @pytest.fixture()
    def kg(self):
        from agent.memory.knowledge_graph import KnowledgeGraph
        return KnowledgeGraph(project_hash=f"test_kg_{_uid()}")

    def test_add_node_and_get_node(self, kg):
        kg.add_node("n1", label="File1", node_type="file")
        node = kg.get_node("n1")
        assert node is not None
        assert node.label == "File1"
        assert node.node_type == "file"

    def test_get_node_missing_returns_none(self, kg):
        assert kg.get_node("nope") is None

    def test_add_edge_and_neighbors(self, kg):
        kg.add_node("a", label="A", node_type="symbol")
        kg.add_node("b", label="B", node_type="symbol")
        kg.add_edge("a", "b", edge_type="calls")
        nbrs = kg.neighbors("a")
        assert "b" in nbrs

    def test_neighbors_edge_type_filter(self, kg):
        kg.add_node("x", label="X")
        kg.add_node("y", label="Y")
        kg.add_node("z", label="Z")
        kg.add_edge("x", "y", edge_type="imports")
        kg.add_edge("x", "z", edge_type="calls")
        imports = kg.neighbors("x", edge_type="imports")
        assert "y" in imports
        assert "z" not in imports

    def test_path_connected(self, kg):
        kg.add_node("s", label="Start")
        kg.add_node("m", label="Middle")
        kg.add_node("e", label="End")
        kg.add_edge("s", "m", edge_type="related_to")
        kg.add_edge("m", "e", edge_type="related_to")
        p = kg.path("s", "e")
        assert isinstance(p, list)
        assert len(p) >= 2
        assert p[0] == "s"
        assert p[-1] == "e"

    def test_path_not_connected_returns_empty(self, kg):
        kg.add_node("a", label="A")
        kg.add_node("b", label="B")
        assert kg.path("a", "b") == []

    def test_subgraph_depth(self, kg):
        kg.add_node("root", label="Root")
        kg.add_node("child", label="Child")
        kg.add_node("grandchild", label="Grandchild")
        kg.add_edge("root", "child", edge_type="uses")
        kg.add_edge("child", "grandchild", edge_type="uses")
        sub = kg.subgraph(["root"], depth=2)
        assert isinstance(sub, dict)
        assert "root" in sub
        assert "child" in sub

    def test_find_by_type(self, kg):
        kg.add_node("f1", label="file1", node_type="file")
        kg.add_node("f2", label="file2", node_type="file")
        kg.add_node("s1", label="sym1", node_type="symbol")
        files = kg.find_by_type("file")
        types = {n.node_type for n in files}
        assert types == {"file"}
        assert len(files) == 2

    def test_find_by_edge_type(self, kg):
        kg.add_node("p", label="P")
        kg.add_node("q", label="Q")
        kg.add_node("r", label="R")
        kg.add_edge("p", "q", edge_type="implements")
        kg.add_edge("p", "r", edge_type="extends")
        impls = kg.find_by_edge_type("implements")
        assert ("p", "q") in impls

    def test_stats_structure(self, kg):
        kg.add_node("n", label="N", node_type="concept")
        stats = kg.stats()
        assert "node_count" in stats
        assert "edge_count" in stats
        assert "node_types" in stats
        assert "edge_types" in stats
        assert stats["node_count"] >= 1

    def test_to_dict_from_dict_round_trip(self, kg):
        kg.add_node("src", label="Source", node_type="file")
        kg.add_node("dst", label="Dest", node_type="file")
        kg.add_edge("src", "dst", edge_type="imports", weight=2.0)
        data = kg.to_dict()
        from agent.memory.knowledge_graph import KnowledgeGraph
        restored = KnowledgeGraph.from_dict(data)
        assert restored.get_node("src") is not None
        assert restored.get_node("dst") is not None
        assert "dst" in restored.neighbors("src")

    def test_remove_node(self, kg):
        kg.add_node("gone", label="Gone")
        kg.remove_node("gone")
        assert kg.get_node("gone") is None

    def test_add_edge_autocreates_nodes(self, kg):
        kg.add_edge("auto_a", "auto_b", edge_type="depends_on")
        assert kg.get_node("auto_a") is not None
        assert kg.get_node("auto_b") is not None


# ===========================================================================
# test_analytics
# ===========================================================================

class TestAnalytics:
    """Tests for MemoryAnalytics — counters and reporting."""

    @pytest.fixture()
    def anl(self):
        from agent.memory.analytics import MemoryAnalytics
        a = MemoryAnalytics(project_hash=f"test_anl_{_uid()}")
        a.reset()
        return a

    def test_cache_hit_rate_all_hits(self, anl):
        anl.record_cache_hit("fast")
        anl.record_cache_hit("fast")
        assert anl.get_cache_hit_rate("fast") == 1.0

    def test_cache_hit_rate_mixed(self, anl):
        anl.record_cache_hit("c")
        anl.record_cache_miss("c")
        assert anl.get_cache_hit_rate("c") == pytest.approx(0.5)

    def test_cache_hit_rate_empty(self, anl):
        assert anl.get_cache_hit_rate("nonexistent") == 0.0

    def test_memory_hit_rate_specific_layer(self, anl):
        anl.record_memory_hit("session")
        anl.record_memory_hit("session")
        anl.record_memory_miss("session")
        rate = anl.get_memory_hit_rate("session")
        assert rate == pytest.approx(2 / 3)

    def test_memory_hit_rate_aggregate(self, anl):
        anl.record_memory_hit("working")
        anl.record_memory_miss("working")
        anl.record_memory_hit("project")
        # 2 hits, 1 miss → 2/3
        rate = anl.get_memory_hit_rate()
        assert rate == pytest.approx(2 / 3)

    def test_repair_success_rate(self, anl):
        anl.record_repair_attempt(success=True)
        anl.record_repair_attempt(success=True)
        anl.record_repair_attempt(success=False)
        assert anl.get_repair_success_rate() == pytest.approx(2 / 3)

    def test_workflow_success_rate(self, anl):
        anl.record_workflow_run(success=True)
        anl.record_workflow_run(success=False)
        assert anl.get_workflow_success_rate() == pytest.approx(0.5)

    def test_get_report_structure(self, anl):
        anl.record_cache_hit()
        anl.record_memory_stored("working", size_bytes=100)
        report = anl.get_report()
        assert "cache_hit_rates" in report
        assert "memory_hit_rates" in report
        assert "repair_success_rate" in report
        assert "workflow_success_rate" in report
        assert "memory_growth" in report
        assert "total_operations" in report
        assert "generated_at" in report

    def test_reset_clears_all_counters(self, anl):
        anl.record_cache_hit()
        anl.record_memory_hit("x")
        anl.record_repair_attempt(success=True)
        anl.record_workflow_run(success=False)
        anl.reset()
        assert anl.get_cache_hit_rate() == 0.0
        assert anl.get_memory_hit_rate() == 0.0
        assert anl.get_repair_success_rate() == 0.0
        assert anl.get_workflow_success_rate() == 0.0

    def test_memory_archived_counter(self, anl):
        anl.record_memory_stored("session", size_bytes=200)
        anl.record_memory_archived("session", size_bytes=200)
        report = anl.get_report()
        assert report["memory_growth"]["archived"] >= 1


# ===========================================================================
# test_approval
# ===========================================================================

class TestApproval:
    """Tests for ask_approval, AlwaysAllowRegistry, ask_approval_if_needed."""

    def test_ask_approval_y_approves(self):
        from agent.memory.approval import ApprovalRequest, ask_approval
        req = ApprovalRequest(
            memory_key="k1", memory_value="v1",
            scope="project", reason="test", importance=0.8,
        )
        result = ask_approval(req, ui_callback=lambda _: "Y")
        assert result.approved is True
        assert result.always_allow is False
        assert result.scope == "project"

    def test_ask_approval_n_rejects(self):
        from agent.memory.approval import ApprovalRequest, ask_approval
        req = ApprovalRequest(
            memory_key="k2", memory_value="v2",
            scope="project", reason="test", importance=0.5,
        )
        result = ask_approval(req, ui_callback=lambda _: "N")
        assert result.approved is False

    def test_ask_approval_s_session_scope(self):
        from agent.memory.approval import ApprovalRequest, ask_approval
        req = ApprovalRequest(
            memory_key="k3", memory_value="v3",
            scope="project", reason="test", importance=0.5,
        )
        result = ask_approval(req, ui_callback=lambda _: "S")
        assert result.approved is True
        assert result.scope == "session"

    def test_ask_approval_a_always_allow(self):
        from agent.memory.approval import ApprovalRequest, ask_approval
        req = ApprovalRequest(
            memory_key="k4", memory_value="v4",
            scope="project", reason="test", importance=0.9,
        )
        result = ask_approval(req, ui_callback=lambda _: "A")
        assert result.approved is True
        assert result.always_allow is True

    def test_ask_approval_no_callback_defaults_session(self):
        from agent.memory.approval import ApprovalRequest, ask_approval
        req = ApprovalRequest(
            memory_key="k5", memory_value="v5",
            scope="project", reason="test", importance=0.5,
        )
        result = ask_approval(req, ui_callback=None)
        assert result.approved is True
        assert result.scope == "session"

    def test_always_allow_registry_register_and_is_allowed(self, tmp_path):
        from agent.memory.approval import AlwaysAllowRegistry
        reg = AlwaysAllowRegistry(project_hash=f"reg_{_uid()}")
        reg.register("my_key")
        assert reg.is_always_allowed("my_key")

    def test_always_allow_registry_revoke(self, tmp_path):
        from agent.memory.approval import AlwaysAllowRegistry
        reg = AlwaysAllowRegistry(project_hash=f"reg_{_uid()}")
        reg.register("del_key")
        reg.revoke("del_key")
        assert not reg.is_always_allowed("del_key")

    def test_always_allow_registry_unknown_key(self, tmp_path):
        from agent.memory.approval import AlwaysAllowRegistry
        reg = AlwaysAllowRegistry(project_hash=f"reg_{_uid()}")
        assert not reg.is_always_allowed("never_registered")

    def test_ask_approval_if_needed_skips_prompt_for_preapproved(self):
        from agent.memory.approval import AlwaysAllowRegistry, ask_approval_if_needed, _registries
        ph = f"aain_{_uid()}"
        reg = AlwaysAllowRegistry(ph)
        reg.register("pre_approved_key")
        _registries[ph] = reg

        prompt_called = []

        def callback(prompt):
            prompt_called.append(prompt)
            return "N"

        result = ask_approval_if_needed(
            memory_key="pre_approved_key",
            memory_value="some value",
            scope="project",
            reason="already allowed",
            importance=0.9,
            project_hash=ph,
            ui_callback=callback,
        )
        assert result.approved is True
        assert result.always_allow is True
        assert len(prompt_called) == 0  # prompt was NOT shown


# ===========================================================================
# test_consolidation
# ===========================================================================

class TestConsolidation:
    """Tests for ConsolidationEngine — layer promotion and archival."""

    @pytest.fixture()
    def ce(self):
        from agent.memory.consolidation import ConsolidationEngine
        return ConsolidationEngine(project_hash=f"test_ce_{_uid()}")

    def _high_entry(self, entry_id="high"):
        return {
            "entry_id": entry_id,
            "importance": 0.9,
            "confidence": 0.9,
            "access_count": 20,
            "success_rate": 0.9,
            "scope": "working",
            "created_at": time.time(),
        }

    def _low_entry(self, entry_id="low"):
        return {
            "entry_id": entry_id,
            "importance": 0.05,
            "confidence": 0.05,
            "access_count": 0,
            "success_rate": 0.05,
            "scope": "working",
            "created_at": time.time(),
        }

    def test_build_score_from_dict(self, ce):
        from agent.memory.scoring import MemoryScore
        entry = self._high_entry()
        score = ce.build_score(entry)
        assert isinstance(score, MemoryScore)
        assert 0.0 <= score.composite_score() <= 1.0

    def test_build_score_defaults(self, ce):
        score = ce.build_score({"entry_id": "minimal"})
        assert score.importance == 0.5
        assert score.confidence == 0.5

    def test_consolidate_working_to_session_drops_low_score(self, ce):
        entries = [self._high_entry("good"), self._low_entry("bad")]
        kept = ce.consolidate_working_to_session(entries)
        kept_ids = [e["entry_id"] for e in kept]
        assert "good" in kept_ids
        assert "bad" not in kept_ids

    def test_consolidate_working_to_session_updates_scope(self, ce):
        entries = [self._high_entry()]
        kept = ce.consolidate_working_to_session(entries)
        assert kept[0]["scope"] == "session"

    def test_consolidate_working_to_session_drops_expired(self, ce):
        entry = self._high_entry("expired")
        entry["ttl"] = 1.0
        entry["created_at"] = time.time() - 100
        kept = ce.consolidate_working_to_session([entry])
        assert not any(e["entry_id"] == "expired" for e in kept)

    def test_archive_old_entries_splits_correctly(self, ce):
        old_low = {
            "entry_id": "old_low",
            "importance": 0.05,
            "confidence": 0.05,
            "access_count": 0,
            "success_rate": 0.05,
            "created_at": time.time() - (100 * 86400),  # 100 days ago
        }
        old_high = {
            "entry_id": "old_high",
            "importance": 0.9,
            "confidence": 0.9,
            "access_count": 50,
            "success_rate": 0.9,
            "created_at": time.time() - (100 * 86400),
        }
        new_entry = {
            "entry_id": "new",
            "importance": 0.1,
            "confidence": 0.1,
            "access_count": 0,
            "success_rate": 0.1,
            "created_at": time.time(),
        }
        kept, archived = ce.archive_old_entries(
            [old_low, old_high, new_entry], max_age_days=90
        )
        archived_ids = [e["entry_id"] for e in archived]
        kept_ids = [e["entry_id"] for e in kept]
        assert "old_low" in archived_ids
        assert "old_high" in kept_ids  # old but high score → keep
        assert "new" in kept_ids       # not old enough → keep

    def test_archive_sets_archive_scope(self, ce):
        old_low = {
            "entry_id": "x",
            "importance": 0.0,
            "confidence": 0.0,
            "access_count": 0,
            "success_rate": 0.0,
            "created_at": time.time() - (200 * 86400),
        }
        _, archived = ce.archive_old_entries([old_low], max_age_days=90)
        if archived:
            assert archived[0]["scope"] == "archive"


# ===========================================================================
# test_reflection_engine
# ===========================================================================

class TestReflectionEngine:
    """Tests for ReflectionEngine — post-task analysis."""

    @pytest.fixture()
    def re_engine(self):
        from agent.memory.reflection_engine import ReflectionEngine
        return ReflectionEngine(project_hash=f"test_re_{_uid()}")

    def _make_call(self, tool, success=True, result="", args=None):
        return {
            "tool": tool,
            "success": success,
            "result": result,
            "args": args or {},
        }

    def test_reflect_returns_reflection_result(self, re_engine):
        from agent.memory.reflection_engine import ReflectionResult
        calls = [self._make_call("bash", args={"command": "pytest"})]
        result = re_engine.reflect("Run tests", calls)
        assert isinstance(result, ReflectionResult)

    def test_reflect_captures_successes(self, re_engine):
        calls = [
            self._make_call("write_file", success=True, args={"path": "out.py"}),
            self._make_call("bash", success=True, args={"command": "pytest"}),
        ]
        result = re_engine.reflect("Write and test", calls)
        assert len(result.successes) >= 1

    def test_reflect_captures_failures(self, re_engine):
        calls = [
            self._make_call("bash", success=False, result="error: command not found"),
        ]
        result = re_engine.reflect("Run missing cmd", calls)
        assert len(result.failures) >= 1

    def test_extract_explicit_remembers_finds_directive(self, re_engine):
        texts = ["Please remember: always run pytest before committing"]
        lessons = re_engine._extract_explicit_remembers(texts)
        assert len(lessons) >= 1
        assert any("user_fact:" in l["key"] for l in lessons)

    def test_extract_explicit_remembers_note_that(self, re_engine):
        texts = ["Note that the API key must be set in .env file"]
        lessons = re_engine._extract_explicit_remembers(texts)
        assert len(lessons) >= 1

    def test_extract_explicit_remembers_empty_text(self, re_engine):
        lessons = re_engine._extract_explicit_remembers([""])
        assert lessons == []

    def test_detect_repair_patterns_failure_then_fix(self, re_engine):
        calls = [
            self._make_call(
                "bash", success=False,
                result="error: No such file or directory",
                args={"command": "cat missing.txt"},
            ),
            self._make_call(
                "bash", success=True,
                result="fixed: created the file",
                args={"command": "touch missing.txt"},
            ),
        ]
        patterns = re_engine._detect_repair_patterns(calls)
        assert len(patterns) >= 1
        assert any("repair:" in p["key"] for p in patterns)

    def test_detect_repair_patterns_no_failure(self, re_engine):
        calls = [
            self._make_call("bash", success=True, result="All good"),
        ]
        patterns = re_engine._detect_repair_patterns(calls)
        assert patterns == []

    def test_summarize_task_non_empty(self, re_engine):
        calls = [
            self._make_call("write_file", args={"path": "a.py"}),
            self._make_call("bash", args={"command": "pytest"}),
        ]
        summary = re_engine._summarize_task("Build feature X", calls)
        assert isinstance(summary, str) and len(summary) > 0

    def test_summarize_task_includes_description(self, re_engine):
        summary = re_engine._summarize_task("Fix authentication bug", [])
        assert "Fix authentication bug" in summary or "Fix authentication" in summary

    def test_reflect_reflection_time_positive(self, re_engine):
        result = re_engine.reflect("simple task", [])
        assert result.reflection_time >= 0.0


# ===========================================================================
# test_memory_manager
# ===========================================================================

class TestMemoryManager:
    """Tests for MemoryManager — unified API and layer routing."""

    @pytest.fixture()
    def mm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        # Reload all memory sub-modules to pick up the patched env
        import importlib
        import agent.memory.execution_memory as _em
        import agent.memory.workflow_memory as _wm
        import agent.memory.failure_memory as _fm
        import agent.memory.decision_memory as _dm
        importlib.reload(_em)
        importlib.reload(_wm)
        importlib.reload(_fm)
        importlib.reload(_dm)
        from agent.memory.memory_manager import MemoryManager
        return MemoryManager(project_hash=f"test_mm_{_uid()}", cwd=str(tmp_path))

    # WorkingMemory inner class -------------------------------------------

    def test_working_memory_set_get(self, mm):
        mm.working.set("wk_key", "wk_value")
        assert mm.working.get("wk_key") == "wk_value"

    def test_working_memory_get_default(self, mm):
        assert mm.working.get("missing", "fallback") == "fallback"

    def test_working_memory_clear(self, mm):
        mm.working.set("tmp", "data")
        mm.working.clear()
        assert mm.working.get("tmp") is None

    def test_working_memory_all_returns_copy(self, mm):
        mm.working.set("a", 1)
        mm.working.set("b", 2)
        snapshot = mm.working.all()
        assert "a" in snapshot and "b" in snapshot

    def test_working_memory_set_task(self, mm):
        mm.working.set_task("task_001")
        assert mm.working._task_id == "task_001"

    # store() routing ---------------------------------------------------

    def test_store_working_scope(self, mm):
        mm.store("my_key", "my_val", scope="working")
        assert mm.working.get("my_key") == "my_val"

    def test_store_session_scope_tags_entry(self, mm):
        mm.store("sess_key", "sess_val", scope="session")
        assert mm.working.get("sess_key") == "sess_val"
        assert mm.working.get("__scope__sess_key") == "session"

    def test_store_returns_key(self, mm):
        returned = mm.store("retkey", "retval")
        assert returned == "retkey"

    # recall() ----------------------------------------------------------

    def test_recall_finds_working_entry(self, mm):
        mm.working.set("recall_key", "recall_val")
        assert mm.recall("recall_key") == "recall_val"

    def test_recall_missing_returns_none(self, mm):
        assert mm.recall("absent_key") is None

    # search() ----------------------------------------------------------

    def test_search_returns_results(self, mm):
        mm.store("search_target", "hello world", scope="working")
        results = mm.search("search_target")
        assert isinstance(results, list)

    def test_search_finds_key_substring(self, mm):
        mm.working.set("interesting_result", "value123")
        results = mm.search("interesting_result")
        keys = [r["key"] for r in results]
        assert "interesting_result" in keys

    def test_search_result_schema(self, mm):
        mm.working.set("schema_test", "some content")
        results = mm.search("schema_test")
        if results:
            r = results[0]
            assert "key" in r
            assert "value" in r
            assert "scope" in r
            assert "score" in r
            assert "source" in r

    # clear_working() ---------------------------------------------------

    def test_clear_working_empties_store(self, mm):
        mm.working.set("x1", "v1")
        mm.working.set("x2", "v2")
        mm.clear_working()
        assert mm.working.all() == {}

    # get_context_for_task() --------------------------------------------

    def test_get_context_for_task_expected_keys(self, mm):
        ctx = mm.get_context_for_task("build the feature")
        assert "working" in ctx
        assert "project_commands" in ctx
        assert "best_workflow" in ctx
        assert "relevant_decisions" in ctx
        assert "repair_strategies" in ctx
        assert "analytics" in ctx

    def test_get_context_for_task_working_is_dict(self, mm):
        mm.working.set("ctx_key", "ctx_val")
        ctx = mm.get_context_for_task()
        assert isinstance(ctx["working"], dict)

    # get_stats() -------------------------------------------------------

    def test_get_stats_returns_dict(self, mm):
        stats = mm.get_stats()
        assert isinstance(stats, dict)

    def test_get_stats_has_project_hash(self, mm):
        stats = mm.get_stats()
        assert "project_hash" in stats
        assert stats["project_hash"] == mm.project_hash

    def test_get_stats_has_working_memory_keys(self, mm):
        mm.working.set("s1", "v1")
        stats = mm.get_stats()
        assert "working_memory_keys" in stats
        assert stats["working_memory_keys"] >= 1

    def test_get_stats_has_expected_subsections(self, mm):
        stats = mm.get_stats()
        assert "workflow" in stats
        assert "knowledge_graph" in stats
        assert "analytics" in stats

    # promote() / archive() ---------------------------------------------

    def test_promote_absent_key_returns_false(self, mm):
        result = mm.promote("no_such_key", from_scope="working", to_scope="session")
        assert result is False

    def test_archive_present_key_returns_true(self, mm):
        mm.working.set("arch_key", "arch_val")
        result = mm.archive("arch_key")
        assert result is True
        assert mm.working.get("arch_key") is None

    def test_archive_absent_key_returns_false(self, mm):
        assert mm.archive("not_there") is False

    # reflect() ---------------------------------------------------------

    def test_reflect_returns_dict_with_expected_keys(self, mm):
        result = mm.reflect("Test task", [])
        assert isinstance(result, dict)
        for key in ("task_summary", "successes", "failures", "lessons", "promoted", "archived"):
            assert key in result
