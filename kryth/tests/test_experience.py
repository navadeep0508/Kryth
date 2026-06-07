"""Full test suite for the KRYTH Experience Engine.

Covers:
  - ExperienceStore (SQLite, FTS, CRUD, scoring)
  - SimilarTaskEngine
  - TeamExperienceEngine
  - WorkflowExperienceEngine
  - RepairExperienceEngine
  - TerminalExperienceEngine
  - DecisionExperienceEngine
  - ArchExperienceEngine
  - ParallelExperienceEngine
  - Prediction Engine
  - ExperienceManager (unified API)
  - Dashboard rendering (no crash)
  - Integration with agent_loop imports
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path, suffix: str = "test"):
    from agent.experience.store import ExperienceStore
    store = ExperienceStore(f"test{suffix}")
    # Override the DB directory so each test gets an isolated file
    import agent.experience.store as _store_mod
    test_dir = tmp_path / "experience"
    test_dir.mkdir(parents=True, exist_ok=True)
    store._path = str(test_dir / f"test{suffix}.db")
    store._conn = None  # force reconnect to new path
    return store


# ---------------------------------------------------------------------------
# ExperienceStore
# ---------------------------------------------------------------------------


class TestExperienceStore:
    def test_insert_and_retrieve(self, tmp_path):
        from agent.experience.store import ExperienceRecord
        store = _make_store(tmp_path)
        rec = ExperienceRecord(
            id=None, project_hash="test", kind="task",
            title="Build a SaaS", summary="Full-stack SaaS app",
            tags=["saas", "fullstack"], source_refs={},
            outcome="success", importance=0.8, confidence=0.9,
            success_rate=1.0, reuse_count=0, novelty=0.5,
            recency_ts=time.time(), created_at=time.time(),
            composite_score=0.0, extra={},
        )
        rid = store.insert(rec)
        assert rid > 0

        retrieved = store.get_by_id(rid)
        assert retrieved is not None
        assert retrieved.title == "Build a SaaS"
        assert retrieved.kind == "task"
        assert retrieved.outcome == "success"
        store.close()

    def test_fts_search_finds_by_keyword(self, tmp_path):
        from agent.experience.store import ExperienceRecord
        store = _make_store(tmp_path, "fts")
        ph = store._ph  # use the store's actual project hash

        for i, title in enumerate(["Build payment system", "Create auth flow", "Fix database bug"]):
            rec = ExperienceRecord(
                id=None, project_hash=ph, kind="task",
                title=title, summary=f"Task {i}",
                tags=[], source_refs={}, outcome="success",
                importance=0.6, confidence=0.7, success_rate=1.0,
                reuse_count=0, novelty=0.5,
                recency_ts=time.time(), created_at=time.time(),
                composite_score=0.0, extra={},
            )
            store.insert(rec)

        results = store.search_fts("payment", limit=5)
        titles = [r.title for r in results]
        assert any("payment" in t.lower() for t in titles)
        store.close()

    def test_fts_search_empty_returns_empty(self, tmp_path):
        store = _make_store(tmp_path, "empty")
        results = store.search_fts("nonexistent_xyz_query_12345")
        assert results == []
        store.close()

    def test_fts_bad_query_does_not_raise(self, tmp_path):
        store = _make_store(tmp_path, "badq")
        results = store.search_fts('(((', limit=5)
        assert isinstance(results, list)
        store.close()

    def test_update_reuse(self, tmp_path):
        from agent.experience.store import ExperienceRecord
        store = _make_store(tmp_path, "reuse")
        rec = ExperienceRecord(
            id=None, project_hash="test", kind="task",
            title="test", summary="test",
            tags=[], source_refs={}, outcome="success",
            importance=0.5, confidence=0.5, success_rate=1.0,
            reuse_count=0, novelty=0.5,
            recency_ts=time.time(), created_at=time.time(),
            composite_score=0.0, extra={},
        )
        rid = store.insert(rec)
        store.update_reuse(rid)
        retrieved = store.get_by_id(rid)
        assert retrieved.reuse_count == 1
        store.close()

    def test_stats_returns_dict(self, tmp_path):
        store = _make_store(tmp_path, "stats")
        stats = store.stats()
        assert "total" in stats
        assert "by_kind" in stats
        assert "avg_composite_score" in stats
        store.close()

    def test_get_by_kind(self, tmp_path):
        from agent.experience.store import ExperienceRecord
        store = _make_store(tmp_path, "bykind")
        for kind in ("task", "team", "repair"):
            rec = ExperienceRecord(
                id=None, project_hash="test", kind=kind,
                title=f"{kind} record", summary="",
                tags=[], source_refs={}, outcome="success",
                importance=0.5, confidence=0.5, success_rate=1.0,
                reuse_count=0, novelty=0.5,
                recency_ts=time.time(), created_at=time.time(),
                composite_score=0.0, extra={},
            )
            store.insert(rec)

        tasks = store.get_by_kind("task", limit=10)
        assert all(r.kind == "task" for r in tasks)
        store.close()

    def test_composite_score_positive(self, tmp_path):
        from agent.experience.store import ExperienceRecord, _compute_score
        rec = ExperienceRecord(
            id=None, project_hash="test", kind="task",
            title="x", summary="",
            tags=[], source_refs={}, outcome="success",
            importance=0.8, confidence=0.9, success_rate=1.0,
            reuse_count=5, novelty=0.3,
            recency_ts=time.time(), created_at=time.time() - 86400,
            composite_score=0.0, extra={},
        )
        score = _compute_score(rec)
        assert 0.0 < score <= 1.0


# ---------------------------------------------------------------------------
# SimilarTaskEngine
# ---------------------------------------------------------------------------


class TestSimilarTaskEngine:
    def test_search_returns_result_object(self, tmp_path):
        from agent.experience.similar_task import SimilarTaskEngine
        store = _make_store(tmp_path, "sim")
        engine = SimilarTaskEngine(store)
        result = engine.search("build a todo app")
        assert hasattr(result, "matches")
        assert hasattr(result, "confidence")
        assert hasattr(result, "searched_systems")
        assert isinstance(result.matches, list)
        store.close()

    def test_text_similarity_identical(self):
        from agent.experience.similar_task import SimilarTaskEngine
        sim = SimilarTaskEngine._text_similarity("build payment", "build payment")
        assert sim == 1.0

    def test_text_similarity_disjoint(self):
        from agent.experience.similar_task import SimilarTaskEngine
        sim = SimilarTaskEngine._text_similarity("abc def", "xyz uvw")
        assert sim == 0.0

    def test_text_similarity_partial(self):
        from agent.experience.similar_task import SimilarTaskEngine
        sim = SimilarTaskEngine._text_similarity("build auth", "build payments")
        assert 0.0 < sim < 1.0

    def test_has_high_confidence_false_when_empty(self, tmp_path):
        from agent.experience.similar_task import SimilarTaskEngine
        store = _make_store(tmp_path, "noconf")
        engine = SimilarTaskEngine(store)
        result = engine.search("unknown task xyz")
        assert not result.has_high_confidence(0.70)
        store.close()

    def test_search_with_stored_record(self, tmp_path):
        from agent.experience.store import ExperienceRecord
        from agent.experience.similar_task import SimilarTaskEngine
        store = _make_store(tmp_path, "simrec")
        rec = ExperienceRecord(
            id=None, project_hash=store._ph, kind="task",
            title="build stripe payment integration",
            summary="Stripe checkout and webhooks",
            tags=["stripe", "payment"], source_refs={},
            outcome="success", importance=0.8, confidence=0.9,
            success_rate=1.0, reuse_count=3, novelty=0.3,
            recency_ts=time.time(), created_at=time.time(),
            composite_score=0.0, extra={},
        )
        store.insert(rec)
        engine = SimilarTaskEngine(store)
        result = engine.search("stripe payment")
        assert len(result.matches) > 0
        assert result.matches[0].title == "build stripe payment integration"
        store.close()


# ---------------------------------------------------------------------------
# TeamExperienceEngine
# ---------------------------------------------------------------------------


class TestTeamExperienceEngine:
    def test_record_and_recommend(self, tmp_path):
        from agent.experience.team_experience import TeamExperienceEngine
        store = _make_store(tmp_path, "team")
        eng = TeamExperienceEngine(store)
        eng.record(
            task_description="Build SaaS with auth and payments",
            roles=["backend specialist", "auth specialist", "payment specialist"],
            strategy="parallel",
            execution_turns=120,
            repair_count=0,
            merge_conflicts=0,
            success=True,
        )
        rec = eng.recommend("SaaS auth payments")
        assert rec is not None
        assert rec.confidence > 0.0
        assert len(rec.roles) > 0
        store.close()

    def test_recommend_returns_none_when_empty(self, tmp_path):
        from agent.experience.team_experience import TeamExperienceEngine
        store = _make_store(tmp_path, "teamempty")
        eng = TeamExperienceEngine(store)
        rec = eng.recommend("build xyz with abc")
        assert rec is None
        store.close()

    def test_get_all_returns_list(self, tmp_path):
        from agent.experience.team_experience import TeamExperienceEngine
        store = _make_store(tmp_path, "teamall")
        eng = TeamExperienceEngine(store)
        assert isinstance(eng.get_all(), list)
        store.close()


# ---------------------------------------------------------------------------
# WorkflowExperienceEngine
# ---------------------------------------------------------------------------


class TestWorkflowExperienceEngine:
    def test_record_stores_experience(self, tmp_path):
        from agent.experience.workflow_experience import WorkflowExperienceEngine
        store = _make_store(tmp_path, "wf")
        eng = WorkflowExperienceEngine(store)
        eng.record("python-startup", ["uv sync", "uv run app.py"], "feature", True, 5.0)
        recs = store.get_by_kind("workflow", limit=10)
        assert len(recs) > 0
        store.close()

    def test_recommend_reads_back(self, tmp_path):
        from agent.experience.workflow_experience import WorkflowExperienceEngine
        store = _make_store(tmp_path, "wfrec")
        eng = WorkflowExperienceEngine(store)
        eng.record("deploy-workflow", ["npm run build", "vercel deploy"], "deployment", True)
        rec = eng.recommend("deployment")
        assert rec is not None
        assert len(rec.steps) > 0
        store.close()


# ---------------------------------------------------------------------------
# RepairExperienceEngine
# ---------------------------------------------------------------------------


class TestRepairExperienceEngine:
    def test_record_and_lookup(self, tmp_path):
        from agent.experience.repair_experience import RepairExperienceEngine
        store = _make_store(tmp_path, "repair")
        eng = RepairExperienceEngine(store)
        eng.record(
            error_type="ModuleNotFoundError",
            error_message="No module named 'dotenv'",
            repair_commands=["pip install python-dotenv"],
            success=True,
        )
        result = eng.lookup("ModuleNotFoundError", "No module named 'dotenv'")
        # May return None if FailureMemory/ExecutionMemory not available;
        # but the experience store should have it
        recs = store.get_by_kind("repair", limit=5)
        assert len(recs) > 0
        assert recs[0].success_rate == 1.0
        store.close()

    def test_lookup_returns_none_when_no_match(self, tmp_path):
        from agent.experience.repair_experience import RepairExperienceEngine
        store = _make_store(tmp_path, "repairempty")
        eng = RepairExperienceEngine(store)
        result = eng.lookup("UnknownXYZError", "something obscure")
        assert result is None
        store.close()


# ---------------------------------------------------------------------------
# TerminalExperienceEngine
# ---------------------------------------------------------------------------


class TestTerminalExperienceEngine:
    def test_record_and_lookup(self, tmp_path):
        from agent.experience.terminal_experience import TerminalExperienceEngine
        store = _make_store(tmp_path, "term")
        eng = TerminalExperienceEngine(store)
        eng.record("build", "python", ["uv sync", "python -m build"], True)
        # Experience store should have the record
        recs = store.get_by_kind("terminal", limit=5)
        assert len(recs) > 0
        assert recs[0].kind == "terminal"
        store.close()

    def test_get_canonical_returns_dict(self, tmp_path):
        from agent.experience.terminal_experience import TerminalExperienceEngine
        store = _make_store(tmp_path, "termcan")
        eng = TerminalExperienceEngine(store)
        eng.record("test", "python", ["pytest"], True)
        canonical = eng.get_canonical("python")
        assert isinstance(canonical, dict)
        store.close()


# ---------------------------------------------------------------------------
# DecisionExperienceEngine
# ---------------------------------------------------------------------------


class TestDecisionExperienceEngine:
    def test_record_and_lookup(self, tmp_path):
        from agent.experience.decision_experience import DecisionExperienceEngine
        store = _make_store(tmp_path, "dec")
        eng = DecisionExperienceEngine(store)
        eng.record("auth_library", "JWT", "API compatibility", ["OAuth2", "Session"], "successful")
        recs = store.get_by_kind("decision", limit=5)
        assert len(recs) > 0
        store.close()

    def test_lookup_from_store(self, tmp_path):
        from agent.experience.decision_experience import DecisionExperienceEngine
        store = _make_store(tmp_path, "declook")
        eng = DecisionExperienceEngine(store)
        eng.record("database_choice", "PostgreSQL", "ACID compliance", ["MySQL", "SQLite"])
        result = eng.lookup("database")
        assert result is not None
        store.close()


# ---------------------------------------------------------------------------
# ArchExperienceEngine
# ---------------------------------------------------------------------------


class TestArchExperienceEngine:
    def test_record_and_lookup(self, tmp_path):
        from agent.experience.arch_experience import ArchExperienceEngine
        store = _make_store(tmp_path, "arch")
        eng = ArchExperienceEngine(store)
        eng.record(
            project_type="saas",
            folder_structure=["src/", "api/", "frontend/", "tests/"],
            dependency_layout={"frontend": ["api"], "api": ["database"]},
            success=True,
            database_design="PostgreSQL with Prisma",
        )
        result = eng.lookup("saas")
        assert result is not None
        assert len(result.folder_structure) > 0
        store.close()


# ---------------------------------------------------------------------------
# ParallelExperienceEngine
# ---------------------------------------------------------------------------


class TestParallelExperienceEngine:
    def test_record_and_recommend(self, tmp_path):
        from agent.experience.parallel_experience import ParallelExperienceEngine
        store = _make_store(tmp_path, "par")
        eng = ParallelExperienceEngine(store)

        # Record multiple runs with different agent counts
        for agents, success in [(3, True), (3, True), (5, False), (3, True)]:
            eng.record(
                task_type="saas build",
                agent_count=agents,
                parallel_depth=2,
                execution_turns=100,
                conflict_count=0,
                merge_problems=0 if success else 1,
                success=success,
            )

        ac, conf = eng.recommend_agent_count("saas build")
        assert ac > 0
        assert 0.0 <= conf <= 1.0
        store.close()

    def test_recommend_returns_1_when_empty(self, tmp_path):
        from agent.experience.parallel_experience import ParallelExperienceEngine
        store = _make_store(tmp_path, "parempty")
        eng = ParallelExperienceEngine(store)
        ac, conf = eng.recommend_agent_count("unknown task")
        assert ac == 1
        assert conf == 0.0
        store.close()


# ---------------------------------------------------------------------------
# Prediction Engine
# ---------------------------------------------------------------------------


class TestPredictionEngine:
    def test_predict_with_no_similar(self):
        from agent.experience.similar_task import SimilarTaskSearchResult
        from agent.experience.prediction import predict
        result = SimilarTaskSearchResult(
            query="build something",
            matches=[],
            confidence=0.0,
            searched_systems=[],
            elapsed_ms=0.0,
        )
        pred = predict("build something", result)
        assert pred.success_probability == 0.70
        assert pred.confidence == 0.30
        assert isinstance(pred.likely_repairs, list)
        assert pred.recommended_agent_count >= 1

    def test_predict_with_high_confidence_similar(self, tmp_path):
        from agent.experience.similar_task import SimilarTask, SimilarTaskSearchResult
        from agent.experience.prediction import predict
        top = SimilarTask(
            title="build saas dashboard",
            summary="Fullstack SaaS",
            kind="task",
            similarity_score=0.85,
            historical_success_rate=0.95,
            best_workflow_steps=["npm install", "npm run build", "deploy"],
            best_team_roles=["backend specialist", "frontend specialist"],
            known_repairs=["npm install --legacy-peer-deps"],
            known_risks=[],
        )
        result = SimilarTaskSearchResult(
            query="saas dashboard",
            matches=[top],
            confidence=0.85,
            searched_systems=["experience_store"],
            elapsed_ms=1.0,
            best_match=top,
        )
        pred = predict("saas dashboard", result)
        assert pred.success_probability > 0.70
        assert pred.confidence > 0.50
        assert len(pred.recommended_workflow) > 0


# ---------------------------------------------------------------------------
# ExperienceManager (unified API)
# ---------------------------------------------------------------------------


class TestExperienceManager:
    def _make_manager(self, tmp_path):
        from agent.experience import ExperienceManager
        import agent.experience.store as _store_mod

        # Monkey-patch get_store to use tmp_path
        test_dir = tmp_path / "experience"
        test_dir.mkdir(parents=True, exist_ok=True)

        from agent.experience.store import ExperienceStore
        ph = "testmanager"
        store = ExperienceStore(ph)
        store._path = str(test_dir / f"{ph}.db")
        store._conn = None
        _store_mod._stores[ph] = store

        mgr = ExperienceManager.__new__(ExperienceManager)
        mgr._root = str(tmp_path)
        mgr._store = store
        from agent.experience.similar_task import SimilarTaskEngine
        from agent.experience.team_experience import TeamExperienceEngine
        from agent.experience.workflow_experience import WorkflowExperienceEngine
        from agent.experience.repair_experience import RepairExperienceEngine
        from agent.experience.terminal_experience import TerminalExperienceEngine
        from agent.experience.decision_experience import DecisionExperienceEngine
        from agent.experience.arch_experience import ArchExperienceEngine
        from agent.experience.parallel_experience import ParallelExperienceEngine
        mgr._similar = SimilarTaskEngine(store)
        mgr._team = TeamExperienceEngine(store)
        mgr._workflow = WorkflowExperienceEngine(store)
        mgr._repair = RepairExperienceEngine(store)
        mgr._terminal = TerminalExperienceEngine(store)
        mgr._decision = DecisionExperienceEngine(store)
        mgr._arch = ArchExperienceEngine(store)
        mgr._parallel = ParallelExperienceEngine(store)
        return mgr

    def test_search_returns_result(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.search("build a web app")
        assert hasattr(result, "matches")
        assert hasattr(result, "confidence")

    def test_predict_returns_prediction(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        pred = mgr.predict("build a SaaS platform")
        assert hasattr(pred, "success_probability")
        assert hasattr(pred, "recommended_agent_count")
        assert 0.0 <= pred.success_probability <= 1.0

    def test_rank_empty_list(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr.rank([]) == []

    def test_rank_sorts_by_score(self, tmp_path):
        from agent.experience.store import ExperienceRecord
        mgr = self._make_manager(tmp_path)
        a = ExperienceRecord(id=1, project_hash="t", kind="task", title="a",
                             summary="", tags=[], source_refs={}, outcome="success",
                             importance=0.1, confidence=0.1, success_rate=0.1,
                             reuse_count=0, novelty=0.9, recency_ts=0,
                             created_at=time.time(), composite_score=0.1, extra={})
        b = ExperienceRecord(id=2, project_hash="t", kind="task", title="b",
                             summary="", tags=[], source_refs={}, outcome="success",
                             importance=0.9, confidence=0.9, success_rate=1.0,
                             reuse_count=10, novelty=0.1, recency_ts=0,
                             created_at=time.time(), composite_score=0.9, extra={})
        ranked = mgr.rank([a, b])
        assert ranked[0].composite_score > ranked[1].composite_score

    def test_learn_task(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.learn("task", title="test task", summary="did something", success=True)
        recs = mgr._store.get_by_kind("task", limit=5)
        assert len(recs) > 0

    def test_learn_team(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.learn("team", task_description="build X", roles=["backend"], strategy="single",
                  execution_turns=50, repair_count=0, merge_conflicts=0, success=True)
        recs = mgr._store.get_by_kind("team", limit=5)
        assert len(recs) > 0

    def test_learn_repair(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.learn("repair", error_type="ModuleNotFoundError",
                  error_message="No module named 'foo'",
                  repair_commands=["pip install foo"], success=True)
        recs = mgr._store.get_by_kind("repair", limit=5)
        assert len(recs) > 0

    def test_learn_parallel(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.learn("parallel", task_type="saas", agent_count=3, parallel_depth=2,
                  execution_turns=100, conflict_count=0, merge_problems=0, success=True)
        recs = mgr._store.get_by_kind("parallel", limit=5)
        assert len(recs) > 0

    def test_report_returns_dict(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        report = mgr.report(render=False)
        assert "stats" in report

    def test_report_with_query_returns_dict(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        report = mgr.report(query="build something", render=False)
        assert "stats" in report
        assert "prediction" in report

    def test_recommend_returns_none_for_unknown(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.recommend("nonexistent_kind_xyz", "query")
        assert result is None

    def test_recommend_parallel_returns_dict(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.recommend("parallel", "build saas")
        assert isinstance(result, dict)
        assert "recommended_agent_count" in result


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    def test_render_does_not_crash(self, tmp_path):
        from agent.experience.similar_task import SimilarTask, SimilarTaskSearchResult
        from agent.experience.prediction import Prediction
        from agent.experience.dashboard import render_dashboard

        top = SimilarTask(
            title="build app", summary="",
            kind="task", similarity_score=0.80,
            historical_success_rate=0.92,
            best_workflow_steps=["step1", "step2"],
            best_team_roles=["backend"],
            known_repairs=["pip install x"],
            known_risks=["High complexity"],
        )
        similar = SimilarTaskSearchResult(
            query="build app", matches=[top], confidence=0.80,
            searched_systems=["experience_store"],
            elapsed_ms=2.5, best_match=top,
        )
        pred = Prediction(
            success_probability=0.92,
            likely_repairs=["pip install x"],
            likely_build_time_turns=80,
            recommended_agent_count=2,
            recommended_workflow=["step1", "step2"],
            risk_factors=["High complexity"],
            confidence=0.80,
            reasoning="Based on 1 similar task.",
            evidence_count=1,
        )
        # Should not raise
        render_dashboard(similar, pred)

    def test_plain_dashboard_does_not_crash(self):
        from agent.experience.similar_task import SimilarTaskSearchResult
        from agent.experience.prediction import Prediction
        from agent.experience.dashboard import _plain_dashboard

        similar = SimilarTaskSearchResult(
            query="x", matches=[], confidence=0.0,
            searched_systems=[], elapsed_ms=0.0,
        )
        pred = Prediction(
            success_probability=0.70, likely_repairs=[],
            likely_build_time_turns=80, recommended_agent_count=1,
            recommended_workflow=[], risk_factors=[],
            confidence=0.30, reasoning="default", evidence_count=0,
        )
        _plain_dashboard(similar, pred)


# ---------------------------------------------------------------------------
# Integration: imports don't break agent_loop
# ---------------------------------------------------------------------------


class TestAgentLoopIntegration:
    def test_experience_import_in_agent_loop(self):
        """Verify the agent_loop module can be imported without crashing."""
        import importlib
        import agent.agent_loop
        importlib.reload(agent.agent_loop)

    def test_get_experience_returns_manager(self, tmp_path, monkeypatch):
        import agent.experience.store as _s
        monkeypatch.setattr(_s, "_stores", {})

        from agent.experience import get_experience
        mgr = get_experience(str(tmp_path))
        assert mgr is not None
        assert hasattr(mgr, "search")
        assert hasattr(mgr, "recommend")
        assert hasattr(mgr, "predict")
        assert hasattr(mgr, "learn")
        assert hasattr(mgr, "report")

    def test_experience_search_graceful_degradation(self, tmp_path):
        """Experience search should never raise even when sub-systems are absent."""
        from agent.experience import get_experience
        import agent.experience.store as _s
        _s._stores.clear()
        mgr = get_experience(str(tmp_path))
        result = mgr.search("build something complex")
        assert isinstance(result.matches, list)

    def test_experience_learn_does_not_raise(self, tmp_path):
        from agent.experience import get_experience
        import agent.experience.store as _s
        _s._stores.clear()
        mgr = get_experience(str(tmp_path))
        # None of these should raise
        mgr.learn("task", title="t", summary="s", success=True)
        mgr.learn("team", task_description="x", roles=["r"], strategy="single",
                  execution_turns=10, repair_count=0, merge_conflicts=0, success=True)
        mgr.learn("repair", error_type="E", error_message="m",
                  repair_commands=["c"], success=True)
        mgr.learn("parallel", task_type="t", agent_count=2, parallel_depth=1,
                  execution_turns=50, conflict_count=0, merge_problems=0, success=True)
