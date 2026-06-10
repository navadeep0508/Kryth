"""Tests for the KRYTH Mission Graph & Workspace Intelligence System.

Covers:
- MissionDAG — CRUD, node management, progress, to_task_graph
- WorkspaceModel — upsert, query, tree, stats, clear
- ArtifactTracker — record, query by task/path/mission
- DependencyEngine — compute_impact, build_dependency_graph (mocked)
- CheckpointEngine — save, restore, list, is_risky, auto_checkpoint
- ImpactAnalyzer — analyze, schedule_verification
- MissionAnalytics — mission_report, project_report, export_summary
- MissionMemoryBridge — lifecycle events, no-op without MM
- MissionManager — full lifecycle (start/load/resume/pause/complete/abort) + task CRUD
- Tool handlers — JSON round-trip for all 8 tools
- Tool specs — schema structure + registration
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def dag(tmp_path):
    from agent.mission.mission_dag import MissionDAG
    return MissionDAG("testhash", db_path=tmp_path / "missions.db")


@pytest.fixture()
def workspace(tmp_path):
    from agent.mission.workspace_model import WorkspaceModel
    return WorkspaceModel("testhash", db_path=tmp_path / "workspace.db")


@pytest.fixture()
def tracker(tmp_path):
    from agent.mission.artifact_tracker import ArtifactTracker
    return ArtifactTracker("testhash", db_path=tmp_path / "artifacts.db")


@pytest.fixture()
def mgr(tmp_path):
    from agent.mission.mission_manager import MissionManager
    return MissionManager(
        project_hash="testhash",
        cwd=str(tmp_path),
        db_path=tmp_path / "missions.db",
    )


@pytest.fixture()
def mid(mgr):
    return mgr.start("Test Mission", goal="Test the system")


# ---------------------------------------------------------------------------
# MissionDAG
# ---------------------------------------------------------------------------

class TestMissionDAG:
    def test_create_and_get(self, dag):
        mid = dag.create("Alpha", goal="Do alpha things")
        result = dag.get(mid)
        assert result is not None
        assert result["name"] == "Alpha"
        assert result["goal"] == "Do alpha things"
        assert result["status"] == "pending"

    def test_list_missions_empty(self, dag):
        assert dag.list_missions() == []

    def test_list_missions(self, dag):
        dag.create("A")
        dag.create("B")
        missions = dag.list_missions()
        names = [m["name"] for m in missions]
        assert "A" in names
        assert "B" in names

    def test_update_status(self, dag):
        mid = dag.create("Status Test")
        from agent.mission.config import MissionStatus
        dag.update_status(mid, MissionStatus.ACTIVE)
        assert dag.get(mid)["status"] == "active"

    def test_update_metadata(self, dag):
        mid = dag.create("Meta Test")
        dag.update_metadata(mid, {"key": "val"})
        assert dag.get(mid)["metadata"]["key"] == "val"

    def test_add_and_get_node(self, dag):
        mid = dag.create("Node Test")
        dag.add_node(mid, {"id": "n1", "description": "do thing", "depends_on": []})
        node = dag.get_node(mid, "n1")
        assert node is not None
        assert node["description"] == "do thing"
        assert node["status"] == "pending"

    def test_update_node_status(self, dag):
        mid = dag.create("N")
        dag.add_node(mid, {"id": "n1"})
        dag.update_node(mid, "n1", status="completed", result={"out": "ok"})
        n = dag.get_node(mid, "n1")
        assert n["status"] == "completed"
        assert n["result"]["out"] == "ok"

    def test_get_nodes(self, dag):
        mid = dag.create("Multi")
        dag.add_node(mid, {"id": "n1"})
        dag.add_node(mid, {"id": "n2", "depends_on": ["n1"]})
        nodes = dag.get_nodes(mid)
        assert len(nodes) == 2

    def test_progress_all_pending(self, dag):
        mid = dag.create("P")
        dag.add_node(mid, {"id": "n1"})
        dag.add_node(mid, {"id": "n2"})
        p = dag.progress(mid)
        assert p["total"] == 2
        assert p["percent"] == 0.0

    def test_progress_partial_complete(self, dag):
        mid = dag.create("PP")
        dag.add_node(mid, {"id": "n1"})
        dag.add_node(mid, {"id": "n2"})
        dag.update_node(mid, "n1", status="completed")
        p = dag.progress(mid)
        assert p["completed"] == 1
        assert p["percent"] == 50.0

    def test_get_resumable(self, dag):
        mid1 = dag.create("Resume1")
        mid2 = dag.create("Resume2")
        from agent.mission.config import MissionStatus
        dag.update_status(mid1, MissionStatus.ACTIVE)
        dag.update_status(mid2, MissionStatus.COMPLETED)
        resumable = dag.get_resumable()
        ids = [m["id"] for m in resumable]
        assert mid1 in ids
        assert mid2 not in ids

    def test_get_returns_none_for_unknown(self, dag):
        assert dag.get("nonexistent") is None

    def test_get_node_returns_none_for_unknown(self, dag):
        mid = dag.create("X")
        assert dag.get_node(mid, "no-node") is None

    def test_to_task_graph(self, dag):
        mid = dag.create("TG")
        dag.add_node(mid, {"id": "n1", "tool": "shell", "action": "run"})
        dag.add_node(mid, {"id": "n2", "depends_on": ["n1"]})
        tg = dag.to_task_graph(mid)
        if tg is not None:  # graceful if agent.task_graph not importable
            assert "n1" in tg.nodes
            assert "n2" in tg.nodes

    def test_node_depends_on_persisted(self, dag):
        mid = dag.create("Deps")
        dag.add_node(mid, {"id": "a"})
        dag.add_node(mid, {"id": "b", "depends_on": ["a"]})
        n = dag.get_node(mid, "b")
        assert "a" in n["depends_on"]


# ---------------------------------------------------------------------------
# WorkspaceModel
# ---------------------------------------------------------------------------

class TestWorkspaceModel:
    def test_upsert_and_query(self, workspace):
        workspace.upsert_entity("function", "my_func", "src/main.py", line_no=10)
        results = workspace.query(kind="function")
        assert any(e["name"] == "my_func" for e in results)

    def test_query_by_name_pattern(self, workspace):
        workspace.upsert_entity("class", "AuthService", "src/auth.py")
        workspace.upsert_entity("class", "UserService", "src/user.py")
        results = workspace.query(name_pattern="Auth")
        assert len(results) == 1
        assert results[0]["name"] == "AuthService"

    def test_query_by_path_prefix(self, workspace):
        workspace.upsert_entity("function", "f1", "src/api/routes.py")
        workspace.upsert_entity("function", "f2", "src/models/user.py")
        results = workspace.query(path_prefix="src/api")
        assert len(results) == 1

    def test_remove_entity(self, workspace):
        workspace.upsert_entity("file", "main.py", "src/main.py")
        workspace.remove_entity("src/main.py")
        results = workspace.query(path_prefix="src/main.py")
        assert results == []

    def test_get_tree(self, workspace):
        workspace.upsert_entity("function", "f1", "src/api.py", parent_path="src")
        workspace.upsert_entity("function", "f2", "src/models.py", parent_path="src")
        tree = workspace.get_tree("src")
        assert "src" in tree

    def test_stats(self, workspace):
        workspace.upsert_entity("function", "f1", "a.py")
        workspace.upsert_entity("class", "C1", "b.py")
        stats = workspace.stats()
        assert stats["total"] == 2
        assert "function" in stats["by_kind"]

    def test_clear(self, workspace):
        workspace.upsert_entity("function", "f1", "a.py")
        workspace.clear()
        assert workspace.stats()["total"] == 0

    def test_upsert_deduplicates(self, workspace):
        workspace.upsert_entity("function", "f1", "a.py", line_no=1)
        workspace.upsert_entity("function", "f1", "a.py", line_no=2)
        results = workspace.query(kind="function", name_pattern="f1")
        assert len(results) == 1

    def test_sync_does_not_crash(self, workspace, tmp_path):
        # sync() calls repo_index which may or may not find Python files — should never raise
        workspace.sync(str(tmp_path))


# ---------------------------------------------------------------------------
# ArtifactTracker
# ---------------------------------------------------------------------------

class TestArtifactTracker:
    def test_record_and_get_by_task(self, tracker):
        tracker.record("m1", "t1", "src/auth.py", kind="file", operation="created")
        arts = tracker.get_by_task("t1")
        assert len(arts) == 1
        assert arts[0]["path"] == "src/auth.py"
        assert arts[0]["operation"] == "created"

    def test_get_by_path(self, tracker):
        tracker.record("m1", "t1", "src/auth.py", operation="created")
        tracker.record("m1", "t2", "src/auth.py", operation="modified")
        history = tracker.get_by_path("src/auth.py")
        assert len(history) == 2
        operations = [h["operation"] for h in history]
        assert "created" in operations
        assert "modified" in operations

    def test_get_mission_artifacts_grouped(self, tracker):
        tracker.record("m1", "t1", "src/auth.py", kind="file")
        tracker.record("m1", "t1", "api/auth", kind="api_route")
        grouped = tracker.get_mission_artifacts("m1")
        assert "file" in grouped
        assert "api_route" in grouped

    def test_paths_modified_by_mission(self, tracker):
        tracker.record("m1", "t1", "src/a.py")
        tracker.record("m1", "t2", "src/b.py")
        tracker.record("m2", "t3", "src/c.py")
        paths = tracker.paths_modified_by_mission("m1")
        assert "src/a.py" in paths
        assert "src/b.py" in paths
        assert "src/c.py" not in paths

    def test_last_task_for_path(self, tracker):
        tracker.record("m1", "t1", "src/main.py", operation="created")
        time.sleep(0.01)
        tracker.record("m1", "t2", "src/main.py", operation="modified")
        last = tracker.last_task_for_path("src/main.py")
        assert last is not None
        assert last["task_node_id"] == "t2"

    def test_last_task_returns_none_for_unknown(self, tracker):
        assert tracker.last_task_for_path("nonexistent.py") is None

    def test_record_returns_int_id(self, tracker):
        row_id = tracker.record("m1", "t1", "a.py")
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_default_name_from_path(self, tracker):
        tracker.record("m1", "t1", "src/controllers/auth.py")
        arts = tracker.get_by_task("t1")
        assert arts[0]["name"] == "auth.py"


# ---------------------------------------------------------------------------
# DependencyEngine
# ---------------------------------------------------------------------------

class TestDependencyEngine:
    def test_compute_impact_no_repo(self, tmp_path):
        from agent.mission.dependency_engine import DependencyEngine
        engine = DependencyEngine()
        result = engine.compute_impact("src/auth.py", root=str(tmp_path))
        assert result.changed_path == "src/auth.py"
        assert isinstance(result.affected_files, list)

    def test_compute_impact_returns_dataclass(self, tmp_path):
        from agent.mission.dependency_engine import DependencyEngine, ImpactResult
        engine = DependencyEngine()
        result = engine.compute_impact("x.py", root=str(tmp_path))
        assert isinstance(result, ImpactResult)

    def test_mark_dependent_tasks_dirty_no_components(self, tmp_path):
        from agent.mission.dependency_engine import DependencyEngine
        engine = DependencyEngine()
        reset = engine.mark_dependent_tasks_dirty("m1", "src/auth.py", root=str(tmp_path))
        assert reset == []

    def test_build_dependency_graph_empty_dir(self, tmp_path):
        from agent.mission.dependency_engine import DependencyEngine
        engine = DependencyEngine()
        graph = engine.build_dependency_graph(root=str(tmp_path))
        assert isinstance(graph, dict)

    def test_impact_result_to_dict(self, tmp_path):
        from agent.mission.dependency_engine import DependencyEngine
        engine = DependencyEngine()
        result = engine.compute_impact("a.py", root=str(tmp_path))
        d = result.to_dict()
        assert "changed_path" in d
        assert "affected_files" in d
        assert "dependent_task_ids" in d


# ---------------------------------------------------------------------------
# CheckpointEngine
# ---------------------------------------------------------------------------

class TestCheckpointEngine:
    def test_save_creates_directory(self, tmp_path, dag):
        from agent.mission.checkpoint_engine import CheckpointEngine
        engine = CheckpointEngine("testhash", mission_dag=dag)
        engine._base = tmp_path / "checkpoints" / "testhash"
        mid = dag.create("Ckpt")
        path = engine.save(mid, label="test")
        assert path.exists()
        assert (path / "checkpoint_meta.json").exists()

    def test_list_checkpoints_empty(self, tmp_path, dag):
        from agent.mission.checkpoint_engine import CheckpointEngine
        engine = CheckpointEngine("testhash", mission_dag=dag)
        engine._base = tmp_path / "checkpoints" / "testhash"
        assert engine.list_checkpoints("nonexistent") == []

    def test_list_checkpoints_after_save(self, tmp_path, dag):
        from agent.mission.checkpoint_engine import CheckpointEngine
        engine = CheckpointEngine("testhash", mission_dag=dag)
        engine._base = tmp_path / "ckpt"
        mid = dag.create("CkptList")
        engine.save(mid, label="a")
        engine.save(mid, label="b")
        assert len(engine.list_checkpoints(mid)) == 2

    def test_is_risky_true(self, dag):
        from agent.mission.checkpoint_engine import CheckpointEngine
        engine = CheckpointEngine("testhash")
        assert engine.is_risky("rm -rf /tmp/test") is True
        assert engine.is_risky("git reset --hard HEAD") is True

    def test_is_risky_false(self, dag):
        from agent.mission.checkpoint_engine import CheckpointEngine
        engine = CheckpointEngine("testhash")
        assert engine.is_risky("pytest tests/") is False
        assert engine.is_risky("python -m build") is False

    def test_auto_checkpoint_risky(self, tmp_path, dag):
        from agent.mission.checkpoint_engine import CheckpointEngine
        engine = CheckpointEngine("testhash", mission_dag=dag)
        engine._base = tmp_path / "ckpt"
        mid = dag.create("Auto")
        path = engine.auto_checkpoint_if_risky("rm -rf /tmp/old", mid)
        assert path is not None and path.exists()

    def test_auto_checkpoint_safe(self, tmp_path, dag):
        from agent.mission.checkpoint_engine import CheckpointEngine
        engine = CheckpointEngine("testhash", mission_dag=dag)
        engine._base = tmp_path / "ckpt"
        mid = dag.create("Safe")
        path = engine.auto_checkpoint_if_risky("pytest tests/", mid)
        assert path is None

    def test_restore_nonexistent_returns_false(self, tmp_path, dag):
        from agent.mission.checkpoint_engine import CheckpointEngine
        engine = CheckpointEngine("testhash", mission_dag=dag)
        result = engine.restore(tmp_path / "nonexistent")
        assert result is False

    def test_checkpoint_meta_content(self, tmp_path, dag):
        from agent.mission.checkpoint_engine import CheckpointEngine
        engine = CheckpointEngine("testhash", mission_dag=dag)
        engine._base = tmp_path / "ckpt"
        mid = dag.create("MetaCheck")
        path = engine.save(mid, label="mycheck")
        meta = json.loads((path / "checkpoint_meta.json").read_text())
        assert meta["mission_id"] == mid
        assert meta["label"] == "mycheck"

    def test_prune_keeps_max_checkpoints(self, tmp_path, dag):
        from agent.mission.checkpoint_engine import CheckpointEngine
        from agent.mission.config import MAX_CHECKPOINTS_PER_MISSION
        engine = CheckpointEngine("testhash", mission_dag=dag)
        engine._base = tmp_path / "ckpt"
        mid = dag.create("Prune")
        for i in range(MAX_CHECKPOINTS_PER_MISSION + 3):
            time.sleep(0.01)
            engine.save(mid, label=str(i))
        assert len(engine.list_checkpoints(mid)) <= MAX_CHECKPOINTS_PER_MISSION


# ---------------------------------------------------------------------------
# ImpactAnalyzer
# ---------------------------------------------------------------------------

class TestImpactAnalyzer:
    def test_analyze_returns_report(self, tmp_path):
        from agent.mission.impact_analyzer import ImpactAnalyzer, ImpactReport
        analyzer = ImpactAnalyzer()
        report = analyzer.analyze(["src/auth.py"], root=str(tmp_path))
        assert isinstance(report, ImpactReport)
        assert report.changed_paths == ["src/auth.py"]

    def test_analyze_multiple_paths(self, tmp_path):
        from agent.mission.impact_analyzer import ImpactAnalyzer
        analyzer = ImpactAnalyzer()
        report = analyzer.analyze(["a.py", "b.py"], root=str(tmp_path))
        assert len(report.changed_paths) == 2

    def test_report_to_dict(self, tmp_path):
        from agent.mission.impact_analyzer import ImpactAnalyzer
        analyzer = ImpactAnalyzer()
        report = analyzer.analyze(["x.py"], root=str(tmp_path))
        d = report.to_dict()
        assert "changed_paths" in d
        assert "affected_modules" in d
        assert "recommended_actions" in d

    def test_report_summary_string(self, tmp_path):
        from agent.mission.impact_analyzer import ImpactAnalyzer
        analyzer = ImpactAnalyzer()
        report = analyzer.analyze(["main.py"], root=str(tmp_path))
        summary = report.summary()
        assert "Changed:" in summary

    def test_schedule_verification_no_dag(self, tmp_path):
        from agent.mission.impact_analyzer import ImpactAnalyzer, ImpactReport
        analyzer = ImpactAnalyzer(mission_dag=None)
        report = ImpactReport(changed_paths=["x.py"], affected_tests=["tests/test_x.py"])
        added = analyzer.schedule_verification(report, "m1")
        assert added == []

    def test_analyze_with_dep_engine(self, tmp_path):
        from agent.mission.impact_analyzer import ImpactAnalyzer
        from agent.mission.dependency_engine import DependencyEngine
        engine = DependencyEngine()
        analyzer = ImpactAnalyzer(dep_engine=engine)
        report = analyzer.analyze(["src/auth.py"], root=str(tmp_path))
        assert isinstance(report.affected_modules, list)


# ---------------------------------------------------------------------------
# MissionAnalytics
# ---------------------------------------------------------------------------

class TestMissionAnalytics:
    def test_mission_report_basic(self, dag):
        from agent.mission.mission_analytics import MissionAnalytics
        analytics = MissionAnalytics(dag)
        mid = dag.create("Analytics Test", goal="check metrics")
        dag.add_node(mid, {"id": "n1"})
        dag.add_node(mid, {"id": "n2"})
        dag.update_node(mid, "n1", status="completed")
        report = analytics.mission_report(mid)
        assert report["name"] == "Analytics Test"
        assert report["tasks_total"] == 2
        assert report["tasks_completed"] == 1
        assert report["progress_percent"] == 50.0

    def test_mission_report_unknown(self, dag):
        from agent.mission.mission_analytics import MissionAnalytics
        analytics = MissionAnalytics(dag)
        report = analytics.mission_report("unknown")
        assert "error" in report

    def test_project_report_empty(self, dag):
        from agent.mission.mission_analytics import MissionAnalytics
        analytics = MissionAnalytics(dag)
        report = analytics.project_report()
        assert report["total_missions"] == 0

    def test_project_report_with_missions(self, dag):
        from agent.mission.mission_analytics import MissionAnalytics
        from agent.mission.config import MissionStatus
        analytics = MissionAnalytics(dag)
        mid1 = dag.create("M1")
        mid2 = dag.create("M2")
        dag.update_status(mid1, MissionStatus.COMPLETED)
        report = analytics.project_report()
        assert report["total_missions"] == 2
        assert report["completed_missions"] == 1

    def test_export_summary_markdown(self, dag):
        from agent.mission.mission_analytics import MissionAnalytics
        analytics = MissionAnalytics(dag)
        mid = dag.create("Summary", goal="Generate markdown")
        dag.add_node(mid, {"id": "n1", "description": "step one"})
        md = analytics.export_summary(mid)
        assert "# Mission:" in md
        assert "step one" in md

    def test_export_summary_shows_progress_bar(self, dag):
        from agent.mission.mission_analytics import MissionAnalytics
        analytics = MissionAnalytics(dag)
        mid = dag.create("Progress Bar")
        dag.add_node(mid, {"id": "n1"})
        dag.add_node(mid, {"id": "n2"})
        dag.update_node(mid, "n1", status="completed")
        md = analytics.export_summary(mid)
        assert "%" in md

    def test_export_summary_unknown_mission(self, dag):
        from agent.mission.mission_analytics import MissionAnalytics
        analytics = MissionAnalytics(dag)
        md = analytics.export_summary("nonexistent")
        assert "nonexistent" in md


# ---------------------------------------------------------------------------
# MissionMemoryBridge
# ---------------------------------------------------------------------------

class TestMissionMemoryBridge:
    def test_no_op_without_mm(self):
        from agent.mission.mission_memory_bridge import MissionMemoryBridge
        bridge = MissionMemoryBridge(memory_manager=None)
        bridge.record_mission_start("m1", "Test", "Do something")
        bridge.record_task_complete("m1", "t1", "desc", success=True)
        bridge.record_checkpoint("m1", "/tmp/ckpt")

    def test_complete_with_success_does_not_crash(self):
        from agent.mission.mission_memory_bridge import MissionMemoryBridge
        bridge = MissionMemoryBridge(memory_manager=None)
        bridge.record_mission_complete("m1", "Test", "goal", ["t1", "t2"], True, 10.0)

    def test_complete_with_failure_does_not_crash(self):
        from agent.mission.mission_memory_bridge import MissionMemoryBridge
        bridge = MissionMemoryBridge(memory_manager=None)
        bridge.record_mission_complete("m1", "Test", "goal", [], False, 5.0)

    def test_infer_intent(self):
        from agent.mission.mission_memory_bridge import _infer_intent
        assert _infer_intent("run tests for the backend") == "testing"
        assert _infer_intent("deploy to production") == "deployment"
        assert _infer_intent("fix the login bug") == "repair"
        assert _infer_intent("build the UI components") == "build"
        assert _infer_intent("some unrecognized goal") == "general"


# ---------------------------------------------------------------------------
# MissionManager
# ---------------------------------------------------------------------------

class TestMissionManager:
    def test_start_returns_id(self, mgr):
        mid = mgr.start("My Mission", goal="Do stuff")
        assert isinstance(mid, str)
        assert len(mid) > 0

    def test_start_with_tasks(self, mgr):
        mid = mgr.start("With Tasks", tasks=[
            {"id": "t1", "description": "step one"},
            {"id": "t2", "description": "step two", "depends_on": ["t1"]},
        ])
        nodes = mgr.dag.get_nodes(mid)
        assert len(nodes) == 2

    def test_load_returns_mission(self, mgr, mid):
        m = mgr.load(mid)
        assert m["name"] == "Test Mission"
        assert "progress" in m
        assert "nodes" in m

    def test_load_raises_for_unknown(self, mgr):
        with pytest.raises(KeyError):
            mgr.load("unknown-id")

    def test_resume_returns_state(self, mgr, mid):
        result = mgr.resume(mid)
        assert result["id"] == mid

    def test_resume_latest_no_missions(self, tmp_path):
        from agent.mission.mission_manager import MissionManager
        fresh = MissionManager(project_hash="empty", db_path=tmp_path / "m.db")
        result = fresh.resume()
        assert "error" in result

    def test_pause(self, mgr, mid):
        mgr.pause(mid)
        m = mgr.dag.get(mid)
        assert m["status"] == "paused"

    def test_complete(self, mgr, mid):
        mgr.complete(mid)
        m = mgr.dag.get(mid)
        assert m["status"] == "completed"

    def test_abort(self, mgr, mid):
        mgr.abort(mid, reason="cancelled by user")
        m = mgr.dag.get(mid)
        assert m["status"] == "aborted"
        assert "abort_reason" in m["metadata"]

    def test_add_task(self, mgr, mid):
        nid = mgr.add_task(mid, {"id": "custom-t", "description": "custom step"})
        assert nid == "custom-t"
        node = mgr.dag.get_node(mid, "custom-t")
        assert node is not None

    def test_add_task_auto_id(self, mgr, mid):
        nid = mgr.add_task(mid, {"description": "auto id step"})
        assert len(nid) > 0
        node = mgr.dag.get_node(mid, nid)
        assert node is not None

    def test_block_task(self, mgr, mid):
        mgr.add_task(mid, {"id": "blocker", "description": "to block"})
        mgr.block_task(mid, "blocker", reason="waiting for dependency")
        node = mgr.dag.get_node(mid, "blocker")
        assert node["status"] == "blocked"

    def test_finish_task(self, mgr, mid):
        mgr.add_task(mid, {"id": "ft1", "description": "finish me"})
        mgr.finish_task(mid, "ft1", result={"output": "done"})
        node = mgr.dag.get_node(mid, "ft1")
        assert node["status"] == "completed"
        assert node["result"]["output"] == "done"

    def test_finish_task_with_artifacts(self, mgr, mid):
        task_id = f"art-task-{mid}"  # unique per test run
        mgr.add_task(mid, {"id": task_id})
        mgr.finish_task(mid, task_id, artifacts=[
            {"path": "src/new_file.py", "kind": "file", "operation": "created"}
        ])
        arts = mgr.artifacts.get_by_task(task_id)
        assert len(arts) == 1
        assert arts[0]["path"] == "src/new_file.py"

    def test_status_dict(self, mgr, mid):
        s = mgr.status(mid)
        assert s["mission_id"] == mid
        assert "progress" in s
        assert "nodes" in s

    def test_status_unknown_mission(self, mgr):
        s = mgr.status("unknown")
        assert "error" in s

    def test_summary_returns_markdown(self, mgr, mid):
        mgr.add_task(mid, {"id": "s1", "description": "step one"})
        md = mgr.summary(mid)
        assert "# Mission:" in md

    def test_list_missions(self, mgr):
        mgr.start("Alpha")
        mgr.start("Beta")
        missions = mgr.list_missions()
        names = [m["name"] for m in missions]
        assert "Alpha" in names
        assert "Beta" in names

    def test_checkpoint_creates_dir(self, mgr, mid, tmp_path):
        mgr.checkpoints._base = tmp_path / "ckpt"
        path = mgr.checkpoint(mid, label="pre-test")
        assert path.exists()

    def test_impact_analysis_returns_report(self, mgr, mid, tmp_path):
        mgr.cwd = str(tmp_path)
        report = mgr.impact_analysis(["src/auth.py"])
        from agent.mission.impact_analyzer import ImpactReport
        assert isinstance(report, ImpactReport)


# ---------------------------------------------------------------------------
# Tool handlers (JSON round-trip)
# ---------------------------------------------------------------------------

class TestToolHandlers:
    def _patch(self, monkeypatch, tmp_path):
        from agent.mission import mission_manager as mm_mod
        monkeypatch.setattr(
            "agent.tools._mission._mgr",
            lambda cwd=".": mm_mod.MissionManager(
                project_hash="testhash",
                cwd=str(tmp_path),
                db_path=tmp_path / "missions.db",
            ),
        )

    def test_mission_start(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._mission import mission_start
        r = json.loads(mission_start("Test", goal="Do it"))
        assert r["status"] == "started"
        assert "mission_id" in r

    def test_mission_resume_no_missions(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._mission import mission_resume
        r = json.loads(mission_resume())
        assert "error" in r

    def test_mission_resume_with_active(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._mission import mission_start, mission_resume
        start_r = json.loads(mission_start("Resume Me", goal="test"))
        mid = start_r["mission_id"]
        resume_r = json.loads(mission_resume(mid))
        assert resume_r["status"] == "resumed"

    def test_mission_status(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._mission import mission_start, mission_status
        mid = json.loads(mission_start("StatusTest"))["mission_id"]
        r = json.loads(mission_status(mid))
        assert r["mission_id"] == mid
        assert "progress" in r

    def test_mission_add_task(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._mission import mission_start, mission_add_task
        mid = json.loads(mission_start("AddTask"))["mission_id"]
        r = json.loads(mission_add_task(mid, description="a new step", task_id="new-t"))
        assert r["status"] == "added"
        assert r["task_id"] == "new-t"

    def test_mission_finish_task(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._mission import mission_start, mission_add_task, mission_finish_task
        mid = json.loads(mission_start("FinishTask"))["mission_id"]
        mission_add_task(mid, description="step", task_id="finish-me")
        r = json.loads(mission_finish_task(mid, "finish-me", result={"ok": True}))
        assert r["status"] == "completed"

    def test_mission_checkpoint(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._mission import mission_start, mission_checkpoint
        mid = json.loads(mission_start("Ckpt"))["mission_id"]

        # Point checkpoint engine to tmp_path
        from agent.mission.mission_manager import MissionManager
        from agent.mission.config import CHECKPOINTS_DIR
        import agent.mission.checkpoint_engine as ce_mod
        original_base = None

        def patched_mgr(cwd="."):
            m = MissionManager(project_hash="testhash", cwd=str(tmp_path), db_path=tmp_path / "missions.db")
            m.checkpoints._base = tmp_path / "ckpt"
            return m
        monkeypatch.setattr("agent.tools._mission._mgr", patched_mgr)

        r = json.loads(mission_checkpoint(mid, label="test"))
        assert r["status"] == "saved"

    def test_mission_impact_analysis(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._mission import mission_impact_analysis
        r = json.loads(mission_impact_analysis(["src/auth.py", "src/user.py"]))
        assert "changed_paths" in r

    def test_mission_summary(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._mission import mission_start, mission_add_task, mission_summary
        mid = json.loads(mission_start("Summary"))["mission_id"]
        mission_add_task(mid, description="test step", task_id="s1")
        md = mission_summary(mid)
        assert "Mission" in md


# ---------------------------------------------------------------------------
# Tool specs
# ---------------------------------------------------------------------------

class TestToolSpecs:
    def test_all_specs_have_required_fields(self):
        from agent.tools._mission_specs import MISSION_TOOL_SPECS
        for spec in MISSION_TOOL_SPECS:
            assert spec["type"] == "function"
            fn = spec["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_spec_names_match_tool_handlers(self):
        from agent.tools._mission_specs import MISSION_TOOL_SPECS
        from agent.tools._mission import MISSION_TOOLS
        spec_names = {s["function"]["name"] for s in MISSION_TOOL_SPECS}
        handler_names = set(MISSION_TOOLS.keys())
        assert spec_names == handler_names

    def test_specs_registered_in_tool_specs(self):
        from agent.tools import TOOL_SPECS
        names = {s["function"]["name"] for s in TOOL_SPECS}
        assert "mission_start" in names
        assert "mission_resume" in names
        assert "mission_checkpoint" in names
        assert "mission_impact_analysis" in names
        assert "mission_summary" in names

    def test_tools_registered_in_tools_dict(self):
        from agent.tools import TOOLS
        assert "mission_start" in TOOLS
        assert "mission_finish_task" in TOOLS
        assert "mission_impact_analysis" in TOOLS

    def test_read_only_tools_registered(self):
        from agent.tools import READ_ONLY_TOOLS
        assert "mission_status" in READ_ONLY_TOOLS
        assert "mission_summary" in READ_ONLY_TOOLS
        assert "mission_impact_analysis" in READ_ONLY_TOOLS
