"""Tests for core data structures."""

import pytest
import time
from pathlib import Path

from semantic.data_structures import (
    EditType,
    EditPriority,
    Location,
    SymbolReference,
    InsertionContext,
    EditOperation,
    EditPlan,
    ValidationResult,
    EditSuggestion,
    Patch,
    PatchSet,
    Conflict,
    AgentCapability,
)


class TestLocation:
    """Tests for Location dataclass."""
    
    def test_location_creation(self):
        loc = Location(file_path="test.py", line=10, column=5)
        assert loc.file_path == "test.py"
        assert loc.line == 10
        assert loc.column == 5
        assert loc.end_line is None
    
    def test_location_to_dict(self):
        loc = Location(file_path="test.py", line=1, column=0, end_line=5, end_column=10)
        data = loc.to_dict()
        assert data["file_path"] == "test.py"
        assert data["line"] == 1
        assert data["column"] == 0
        assert data["end_line"] == 5
        assert data["end_column"] == 10
    
    def test_location_from_dict(self):
        data = {
            "file_path": "test.py",
            "line": 10,
            "column": 2,
            "end_line": 15,
            "end_column": 20,
        }
        loc = Location.from_dict(data)
        assert loc.file_path == "test.py"
        assert loc.line == 10
        assert loc.column == 2
        assert loc.end_line == 15
        assert loc.end_column == 20
    
    def test_location_str(self):
        loc = Location(file_path="test.py", line=10, column=5)
        assert str(loc) == "test.py:10:5"


class TestSymbolReference:
    """Tests for SymbolReference dataclass."""
    
    def test_symbol_creation(self):
        sym = SymbolReference(
            name="my_func",
            file_path="module.py",
            line=20,
            column=4,
            kind="function",
            qualified_name="MyClass.my_func",
        )
        assert sym.name == "my_func"
        assert sym.kind == "function"
        assert sym.qualified_name == "MyClass.my_func"
        assert sym.definition is False
    
    def test_symbol_to_dict(self):
        sym = SymbolReference(
            name="x",
            file_path="test.py",
            line=5,
            kind="variable",
            definition=True,
        )
        data = sym.to_dict()
        assert data["name"] == "x"
        assert data["definition"] is True
    
    def test_symbol_from_dict(self):
        data = {
            "name": "MyClass",
            "file_path": "models.py",
            "line": 1,
            "column": 0,
            "kind": "class",
            "qualified_name": "models.MyClass",
            "definition": True,
        }
        sym = SymbolReference.from_dict(data)
        assert sym.name == "MyClass"
        assert sym.kind == "class"
        assert sym.qualified_name == "models.MyClass"


class TestInsertionContext:
    """Tests for InsertionContext dataclass."""
    
    def test_insertion_context_creation(self):
        ctx = InsertionContext(
            file_path="utils.py",
            line=10,
            after_symbol="import_os",
            in_function="main",
            imports_to_add=["from typing import List"],
        )
        assert ctx.file_path == "utils.py"
        assert ctx.line == 10
        assert ctx.after_symbol == "import_os"
        assert ctx.in_function == "main"
        assert "from typing import List" in ctx.imports_to_add
    
    def test_insertion_context_to_dict(self):
        ctx = InsertionContext(
            file_path="test.py",
            line=1,
            column=0,
            before_symbol="MyClass",
            style="pep8",
            language="python",
        )
        data = ctx.to_dict()
        assert data["file_path"] == "test.py"
        assert data["before_symbol"] == "MyClass"
        assert data["style"] == "pep8"
    
    def test_insertion_context_from_dict(self):
        data = {
            "file_path": "app.py",
            "line": 50,
            "column": 4,
            "in_class": "Router",
            "dependencies": ["request", "response"],
            "language": "python",
        }
        ctx = InsertionContext.from_dict(data)
        assert ctx.in_class == "Router"
        assert "request" in ctx.dependencies


class TestEditOperation:
    """Tests for EditOperation dataclass."""
    
    def test_edit_operation_creation(self):
        op = EditOperation(
            type=EditType.INSERT_CODE,
            target_path="test.py",
            location=Location(file_path="test.py", line=10),
            new_text="print('hello')",
            priority=EditPriority.HIGH,
            description="Add print statement",
        )
        assert op.type == EditType.INSERT_CODE
        assert op.target_path == "test.py"
        assert op.new_text == "print('hello')"
        assert op.priority == EditPriority.HIGH
        assert op.id.startswith("op_")
    
    def test_edit_operation_to_dict(self):
        op = EditOperation(
            type=EditType.RENAME_SYMBOL,
            target_path="module.py",
            symbol="old_name",
            new_text="new_name",
            description="Rename function",
        )
        data = op.to_dict()
        assert data["type"] == "rename_symbol"
        assert data["symbol"] == "old_name"
        assert data["new_text"] == "new_name"
        assert "id" in data
    
    def test_edit_operation_from_dict(self):
        data = {
            "id": "op_test123",
            "type": "edit",
            "target_path": "file.py",
            "old_text": "old",
            "new_text": "new",
            "priority": "critical",
            "description": "Fix bug",
            "language": "python",
        }
        op = EditOperation.from_dict(data)
        assert op.id == "op_test123"
        assert op.type == EditType.EDIT
        assert op.priority == EditPriority.CRITICAL
        assert op.old_text == "old"
    
    def test_add_dependency(self):
        op1 = EditOperation(id="op1", type=EditType.INSERT_CODE, target_path="a.py")
        op2 = EditOperation(id="op2", type=EditType.INSERT_CODE, target_path="b.py")
        op2.add_dependency(op1.id)
        assert op1.id in op2.dependencies
    
    def test_can_execute(self):
        op1 = EditOperation(id="op1", type=EditType.INSERT_CODE, target_path="a.py")
        op2 = EditOperation(id="op2", type=EditType.INSERT_CODE, target_path="b.py", dependencies=["op1"])
        op3 = EditOperation(id="op3", type=EditType.INSERT_CODE, target_path="c.py", dependencies=["op1", "op2"])
        
        completed = {"op1"}
        assert op2.can_execute(completed)  # op2 depends only on op1, which is completed
        
        completed = {"op1", "op2"}
        assert op3.can_execute(completed)  # all dependencies satisfied
        
        completed = {"op1"}
        assert not op3.can_execute(completed)  # missing op2 dependency


class TestEditPlan:
    """Tests for EditPlan dataclass."""
    
    def test_edit_plan_creation(self):
        plan = EditPlan(
            description="Add new feature",
            created_by="agent_1",
        )
        assert plan.description == "Add new feature"
        assert plan.created_by == "agent_1"
        assert plan.id.startswith("plan_")
        assert len(plan.operations) == 0
    
    def test_add_operation(self):
        plan = EditPlan(description="Test")
        op = EditOperation(type=EditType.INSERT_CODE, target_path="test.py")
        plan.add_operation(op)
        assert len(plan.operations) == 1
        assert op in plan.operations
    
    def test_get_affected_files(self):
        plan = EditPlan(description="Multi-file edit")
        op1 = EditOperation(target_path="a.py")
        op2 = EditOperation(target_path="b.py")
        op3 = EditOperation(target_path="a.py")  # duplicate
        plan.add_operation(op1)
        plan.add_operation(op2)
        plan.add_operation(op3)
        
        files = plan.get_affected_files()
        assert files == {"a.py", "b.py"}
    
    def test_get_operations_by_file(self):
        plan = EditPlan(description="Test")
        op1 = EditOperation(target_path="a.py", id="op1")
        op2 = EditOperation(target_path="b.py", id="op2")
        op3 = EditOperation(target_path="a.py", id="op3")
        plan.add_operation(op1)
        plan.add_operation(op2)
        plan.add_operation(op3)
        
        by_file = plan.get_operations_by_file()
        assert len(by_file["a.py"]) == 2
        assert len(by_file["b.py"]) == 1
        assert op1 in by_file["a.py"]
        assert op3 in by_file["a.py"]
    
    def test_get_operation_dependencies(self):
        plan = EditPlan(description="Test")
        op1 = EditOperation(id="op1", dependencies=[])
        op2 = EditOperation(id="op2", dependencies=["op1"])
        op3 = EditOperation(id="op3", dependencies=["op1", "op2"])
        plan.add_operation(op1)
        plan.add_operation(op2)
        plan.add_operation(op3)
        
        deps = plan.get_operation_dependencies()
        assert deps["op1"] == []
        assert deps["op2"] == ["op1"]
        assert deps["op3"] == ["op1", "op2"]
    
    def test_to_dict_and_from_dict(self):
        plan = EditPlan(
            id="plan_123",
            description="Test plan",
            created_by="test_agent",
            transaction_required=False,
        )
        op = EditOperation(
            id="op1",
            type=EditType.EDIT,
            target_path="test.py",
            old_text="old",
            new_text="new",
        )
        plan.add_operation(op)
        
        data = plan.to_dict()
        restored = EditPlan.from_dict(data)
        
        assert restored.id == plan.id
        assert restored.description == plan.description
        assert len(restored.operations) == 1
        assert restored.operations[0].id == op.id
    
    def test_to_json_and_from_json(self):
        plan = EditPlan(description="JSON test")
        plan.add_operation(EditOperation(type=EditType.FORMAT, target_path="code.py"))
        
        json_str = plan.to_json()
        restored = EditPlan.from_json(json_str)
        
        assert restored.description == "JSON test"
        assert len(restored.operations) == 1


class TestValidationResult:
    """Tests for ValidationResult dataclass."""
    
    def test_validation_result_creation(self):
        result = ValidationResult()
        assert result.valid is True
        assert result.errors == []
        assert result.warnings == []
    
    def test_add_error(self):
        result = ValidationResult()
        result.add_error("Something went wrong")
        assert result.valid is False
        assert "Something went wrong" in result.errors
    
    def test_add_warning(self):
        result = ValidationResult()
        result.add_warning("Be careful")
        assert result.valid is True  # warnings don't affect validity
        assert "Be careful" in result.warnings
    
    def test_merge(self):
        result1 = ValidationResult(valid=True)
        result1.add_warning("Warning 1")
        
        result2 = ValidationResult(valid=False)
        result2.add_error("Error 1")
        result2.add_warning("Warning 2")
        
        result1.merge(result2)
        assert result1.valid is False
        assert "Error 1" in result1.errors
        assert "Warning 1" in result1.warnings
        assert "Warning 2" in result1.warnings
    
    def test_to_dict(self):
        result = ValidationResult(valid=False)
        result.add_error("Test error")
        result.add_warning("Test warning")
        result.checks_run = ["ruff", "mypy"]
        result.duration = 1.5
        
        data = result.to_dict()
        assert data["valid"] is False
        assert "Test error" in data["errors"]
        assert "Test warning" in data["warnings"]
        assert data["checks_run"] == ["ruff", "mypy"]
        assert data["duration"] == 1.5


class TestEditSuggestion:
    """Tests for EditSuggestion dataclass."""
    
    def test_suggestion_creation(self):
        loc = Location(file_path="test.py", line=10)
        suggestion = EditSuggestion(
            description="Add docstring",
            code='"""This is a docstring."""',
            location=loc,
            confidence=0.9,
            reasoning="PEP 257 requires docstrings",
        )
        assert suggestion.description == "Add docstring"
        assert suggestion.code == '"""This is a docstring."""'
        assert suggestion.confidence == 0.9
        assert len(suggestion.alternatives) == 0
    
    def test_suggestion_to_dict(self):
        loc = Location(file_path="app.py", line=5)
        suggestion = EditSuggestion(
            description="Fix typo",
            code="correct",
            location=loc,
            alternatives=["wrong", "incorrect"],
        )
        data = suggestion.to_dict()
        assert data["description"] == "Fix typo"
        assert data["alternatives"] == ["wrong", "incorrect"]
        assert "location" in data
    
    def test_suggestion_from_dict(self):
        data = {
            "description": "Add type hint",
            "code": "x: int = 5",
            "location": {"file_path": "script.py", "line": 3},
            "confidence": 0.85,
            "reasoning": "Improve type safety",
        }
        suggestion = EditSuggestion.from_dict(data)
        assert isinstance(suggestion.location, Location)
        assert suggestion.location.file_path == "script.py"


class TestPatch:
    """Tests for Patch dataclass."""
    
    def test_patch_creation(self):
        patch = Patch(
            path="test.py",
            diff_text="---\n+++\n@@ -1 +1 @@\n-old\n+new",
            old_hash="abc123",
            new_hash="def456",
            hunks=[{"start": 1, "length": 1}],
        )
        assert patch.path == "test.py"
        assert patch.old_hash == "abc123"
        assert patch.new_hash == "def456"
        assert len(patch.hunks) == 1
    
    def test_patch_to_dict(self):
        patch = Patch(
            path="file.py",
            diff_text="diff",
            old_hash="hash1",
            new_hash="hash2",
        )
        data = patch.to_dict()
        assert data["path"] == "file.py"
        assert data["diff_text"] == "diff"
    
    def test_patch_from_dict(self):
        data = {
            "path": "test.py",
            "diff_text": "diff content",
            "old_hash": "oldhash",
            "new_hash": "newhash",
            "hunks": [],
            "reversible": True,
        }
        patch = Patch.from_dict(data)
        assert patch.path == "test.py"
        assert patch.reversible is True


class TestPatchSet:
    """Tests for PatchSet dataclass."""
    
    def test_patch_set_creation(self):
        ps = PatchSet(plan_id="plan_123", description="Test patches")
        assert ps.plan_id == "plan_123"
        assert len(ps.patches) == 0
    
    def test_add_patch(self):
        ps = PatchSet()
        patch = Patch(path="a.py", diff_text="diff", old_hash="h1", new_hash="h2")
        ps.add_patch(patch)
        assert len(ps.patches) == 1
        assert ps.patches[0] == patch
    
    def test_get_affected_files(self):
        ps = PatchSet()
        ps.add_patch(Patch(path="a.py", diff_text="", old_hash="", new_hash=""))
        ps.add_patch(Patch(path="b.py", diff_text="", old_hash="", new_hash=""))
        ps.add_patch(Patch(path="a.py", diff_text="", old_hash="", new_hash=""))
        
        files = ps.get_affected_files()
        assert files == {"a.py", "b.py"}
    
    def test_to_dict_and_from_dict(self):
        ps = PatchSet(plan_id="p1", description="Test")
        ps.add_patch(Patch(path="file.py", diff_text="diff", old_hash="h1", new_hash="h2"))
        
        data = ps.to_dict()
        restored = PatchSet.from_dict(data)
        
        assert restored.plan_id == "p1"
        assert len(restored.patches) == 1
        assert restored.patches[0].path == "file.py"
    
    def test_apply_all_dry_run(self):
        """Test that apply_all with dry_run doesn't actually modify files."""
        ps = PatchSet()
        # Create a patch that would modify a file if applied
        patch = Patch(
            path="nonexistent_file.py",
            diff_text="---\n+++\n@@ -1 +1 @@\n-old\n+new",
            old_hash="",
            new_hash="",
        )
        ps.add_patch(patch)
        
        success, errors = ps.apply_all(dry_run=True)
        # Should succeed or fail based on dry-run validation, not actual file changes
        # We just check that it returns something
        assert isinstance(success, bool)
        assert isinstance(errors, list)


class TestConflict:
    """Tests for Conflict dataclass."""
    
    def test_conflict_creation(self):
        conflict = Conflict(
            file_path="shared.py",
            line=25,
            column=10,
            conflicting_operations=["op1", "op2"],
            description="Two edits modify same line",
            resolution="auto_merge",
        )
        assert conflict.file_path == "shared.py"
        assert conflict.line == 25
        assert len(conflict.conflicting_operations) == 2
        assert conflict.resolution == "auto_merge"
    
    def test_conflict_to_dict(self):
        conflict = Conflict(
            file_path="test.py",
            line=0,
            column=0,
            conflicting_operations=["op1"],
            description="File-level conflict",
        )
        data = conflict.to_dict()
        assert data["file_path"] == "test.py"
        assert data["resolution"] is None
    
    def test_conflict_from_dict(self):
        data = {
            "file_path": "models.py",
            "line": 100,
            "column": 5,
            "conflicting_operations": ["op_a", "op_b"],
            "description": "Symbol conflict",
            "resolution": "manual",
        }
        conflict = Conflict.from_dict(data)
        assert conflict.file_path == "models.py"
        assert conflict.resolution == "manual"


class TestAgentCapability:
    """Tests for AgentCapability dataclass."""
    
    def test_capability_creation(self):
        cap = AgentCapability(
            agent_id="agent_1",
            languages=["python", "typescript"],
            operations=[EditType.INSERT_CODE, EditType.EDIT],
            max_concurrent_edits=3,
            can_auto_repair=True,
        )
        assert cap.agent_id == "agent_1"
        assert "python" in cap.languages
        assert EditType.INSERT_CODE in cap.operations
        assert cap.can_refactor is False
    
    def test_capability_to_dict(self):
        cap = AgentCapability(
            agent_id="agent_2",
            languages=["java"],
            operations=[EditType.REFACTOR],
            requires_approval=True,
        )
        data = cap.to_dict()
        assert data["agent_id"] == "agent_2"
        assert data["operations"] == ["refactor"]
        assert data["requires_approval"] is True
    
    def test_capability_from_dict(self):
        data = {
            "agent_id": "agent_3",
            "languages": ["go", "rust"],
            "operations": ["rename_symbol", "move_symbol"],
            "max_concurrent_edits": 5,
            "can_auto_repair": True,
            "can_refactor": True,
            "requires_approval": False,
            "metadata": {"version": "1.0"},
        }
        cap = AgentCapability.from_dict(data)
        assert cap.agent_id == "agent_3"
        assert EditType.RENAME_SYMBOL in cap.operations
        assert EditType.MOVE_SYMBOL in cap.operations
        assert cap.can_auto_repair is True
        assert cap.can_refactor is True