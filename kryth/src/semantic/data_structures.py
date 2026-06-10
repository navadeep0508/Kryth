"""Core data structures for semantic editing.

This module defines the fundamental types and dataclasses used throughout
KRYTH's semantic editing system. All modules depend on these structures.

Key types:
- EditOperation: A single atomic edit (insert, edit, delete, rename, etc.)
- EditPlan: A collection of operations with dependencies
- EditPriority: Priority levels for operations
- ValidationResult: Results from validation pipeline
- SymbolReference: Reference to a code symbol
- Location: File position (line, column)
- InsertionContext: Context for intelligent insertion
- EditSuggestion: A suggested edit with reasoning
- Patch: Unified diff representation
- PatchSet: Collection of patches

These structures are designed to be:
- Serializable (JSON-friendly)
- Language-agnostic
- Extensible (via metadata fields)
- Immutable where possible
"""

from __future__ import annotations

import enum
import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union


class EditType(enum.Enum):
    """Types of edit operations."""
    INSERT_CODE = "insert_code"
    EDIT = "edit"
    DELETE = "delete"
    RENAME_SYMBOL = "rename_symbol"
    MOVE_SYMBOL = "move_symbol"
    EXTRACT = "extract"
    INLINE = "inline"
    FORMAT = "format"
    FIX = "fix"
    REFACTOR = "refactor"
    WRITE = "write"  # complete file overwrite


class EditPriority(enum.Enum):
    """Priority levels for operations."""
    CRITICAL = "critical"  # must succeed
    HIGH = "high"  # important
    NORMAL = "normal"  # standard
    LOW = "low"  # optional
    BACKGROUND = "background"  # can be deferred


@dataclass
class Location:
    """A specific location in a file."""
    file_path: str
    line: int  # 1-indexed
    column: int = 0
    end_line: Optional[int] = None
    end_column: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Location:
        return cls(**data)
    
    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}:{self.column}"


@dataclass
class SymbolReference:
    """A reference to a symbol (function, class, variable, etc.)."""
    name: str
    file_path: str
    line: int
    column: int = 0
    kind: str = "unknown"  # "function", "class", "variable", "module", etc.
    qualified_name: Optional[str] = None
    definition: bool = False  # is this a definition or usage?
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SymbolReference:
        return cls(**data)


@dataclass
class InsertionContext:
    """Context for intelligent code insertion."""
    file_path: str
    line: int  # preferred line (1-indexed)
    column: int = 0
    before_symbol: Optional[str] = None  # insert before this symbol
    after_symbol: Optional[str] = None  # insert after this symbol
    in_class: Optional[str] = None  # class name if inside class
    in_function: Optional[str] = None  # function name if inside function
    imports_to_add: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)  # symbols this code depends on
    style: Optional[str] = None  # "google", "pep8", "airbnb", etc.
    language: str = "python"
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InsertionContext:
        return cls(**data)


@dataclass
class EditOperation:
    """A single atomic edit operation.
    
    EditOperation is the fundamental unit of change. It represents one
    coherent modification to a file. Operations can have dependencies
    on other operations, forming a DAG for execution order.
    
    Types:
    - INSERT_CODE: Insert code at a location
    - EDIT: Replace old_text with new_text
    - DELETE: Remove code
    - RENAME_SYMBOL: Rename a symbol across files
    - MOVE_SYMBOL: Move symbol to new location
    - EXTRACT: Extract code block into new function/class
    - INLINE: Inline a function/variable
    - FORMAT: Apply formatting
    - FIX: Apply an auto-fix
    - REFACTOR: Complex refactoring (multiple sub-ops)
    - WRITE: Complete file overwrite
    
    Each operation has:
    - Unique ID (auto-generated if not provided)
    - Type
    - Target file path
    - Location (line/column) if applicable
    - Text content (new_text, old_text)
    - Dependencies (list of operation IDs that must run first)
    - Priority (for ordering when dependencies allow)
    - Metadata (language-specific, tool-specific, etc.)
    """
    
    id: str = field(default_factory=lambda: f"op_{uuid.uuid4().hex[:8]}")
    type: EditType = EditType.EDIT
    target_path: str = ""
    location: Optional[Location] = None
    old_text: Optional[str] = None
    new_text: Optional[str] = None
    symbol: Optional[str] = None  # symbol being renamed/moved
    dependencies: List[str] = field(default_factory=list)
    priority: EditPriority = EditPriority.NORMAL
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    language: str = "python"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data["type"] = self.type.value
        data["priority"] = self.priority.value
        if self.location:
            data["location"] = self.location.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EditOperation:
        """Create from dictionary."""
        # Convert enum strings to enums
        if "type" in data:
            data["type"] = EditType(data["type"])
        if "priority" in data:
            data["priority"] = EditPriority(data["priority"])
        if "location" in data and isinstance(data["location"], dict):
            data["location"] = Location.from_dict(data["location"])
        return cls(**data)
    
    def add_dependency(self, op_id: str) -> None:
        """Add a dependency on another operation."""
        if op_id not in self.dependencies:
            self.dependencies.append(op_id)
    
    def can_execute(self, completed_ops: Set[str]) -> bool:
        """Check if all dependencies are satisfied."""
        return all(dep in completed_ops for dep in self.dependencies)
    
    def __str__(self) -> str:
        return f"{self.type.value} on {self.target_path}"


@dataclass
class EditPlan:
    """A collection of edit operations with a common goal.
    
    An EditPlan represents a complete, coherent change to the codebase.
    It contains multiple operations that may span multiple files and
    have dependencies between them.
    
    Properties:
    - id: Unique identifier
    - description: Human-readable description
    - operations: List of EditOperations
    - created_by: Agent or user who created the plan
    - created_at: Timestamp
    - estimated_impact: Metrics about the change
    - transaction_required: Whether atomic transaction is needed
    - metadata: Additional information
    
    The plan lifecycle:
    1. Create plan with operations
    2. Validate (syntax, types, tests)
    3. Check for conflicts
    4. Execute (in dependency order)
    5. Validate again
    6. Commit or rollback
    
    Plans can be:
    - Serialized to JSON for storage/transmission
    - Stored in EditMemory for learning
    - Reviewed by humans before execution
    - Applied atomically via TransactionManager
    """
    
    id: str = field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:8]}")
    description: str = ""
    operations: List[EditOperation] = field(default_factory=list)
    created_by: str = "user"
    created_at: float = field(default_factory=__import__('time').time)
    estimated_impact: Dict[str, Any] = field(default_factory=dict)
    transaction_required: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_operation(self, operation: EditOperation) -> None:
        """Add an operation to the plan."""
        self.operations.append(operation)
    
    def get_affected_files(self) -> Set[str]:
        """Get set of all files affected by this plan."""
        files = set()
        for op in self.operations:
            if op.target_path:
                files.add(op.target_path)
        return files
    
    def get_operations_by_file(self) -> Dict[str, List[EditOperation]]:
        """Group operations by target file."""
        by_file: Dict[str, List[EditOperation]] = {}
        for op in self.operations:
            by_file.setdefault(op.target_path, []).append(op)
        return by_file
    
    def get_operation_dependencies(self) -> Dict[str, List[str]]:
        """Get dependency graph (op_id -> list of required op_ids)."""
        deps: Dict[str, List[str]] = {}
        for op in self.operations:
            deps[op.id] = op.dependencies.copy()
        return deps
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data["operations"] = [op.to_dict() for op in self.operations]
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EditPlan:
        """Create from dictionary."""
        operations = [EditOperation.from_dict(op) for op in data.pop("operations", [])]
        plan = cls(**data)
        plan.operations = operations
        return plan
    
    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict(), indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> EditPlan:
        """Deserialize from JSON."""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    def __str__(self) -> str:
        return f"EditPlan {self.id}: {self.description} ({len(self.operations)} ops)"


@dataclass
class ValidationResult:
    """Result of a validation check.
    
    Contains:
    - valid: Overall validity
    - errors: List of error messages
    - warnings: List of warning messages
    - checks_run: Which validation checks were executed
    - duration: Time taken for validation
    - metadata: Additional data (coverage, complexity, etc.)
    """
    
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checks_run: List[str] = field(default_factory=list)
    duration: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_error(self, error: str) -> None:
        """Add an error, marking result as invalid."""
        self.errors.append(error)
        self.valid = False
    
    def add_warning(self, warning: str) -> None:
        """Add a warning (doesn't affect validity)."""
        self.warnings.append(warning)
    
    def merge(self, other: ValidationResult) -> None:
        """Merge another validation result into this one."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.checks_run.extend(other.checks_run)
        self.valid = self.valid and other.valid
        self.duration += other.duration
        self.metadata.update(other.metadata)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def __str__(self) -> str:
        status = "VALID" if self.valid else "INVALID"
        return f"ValidationResult: {status} ({len(self.errors)} errors, {len(self.warnings)} warnings)"


@dataclass
class EditSuggestion:
    """A suggested edit with reasoning.
    
    Used by the intelligent insertion and refactoring engines to
    propose changes to the user or to generate edit plans.
    """
    
    description: str
    code: str
    location: Location
    confidence: float = 0.8  # 0.0 to 1.0
    reasoning: str = ""
    alternatives: List[str] = field(default_factory=list)  # alternative code snippets
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["location"] = self.location.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EditSuggestion:
        if "location" in data and isinstance(data["location"], dict):
            data["location"] = Location.from_dict(data["location"])
        return cls(**data)


@dataclass
class Patch:
    """A unified diff patch for a single file.
    
    Represents the changes to be applied to one file in standard
    unified diff format. Can be applied with `patch` or `git apply`.
    """
    
    path: str
    diff_text: str
    old_hash: str  # SHA1 of original content
    new_hash: str  # SHA1 of new content
    hunks: List[Dict[str, Any]] = field(default_factory=list)
    reversible: bool = True  # can be inverted
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Patch:
        return cls(**data)
    
    def __str__(self) -> str:
        return f"Patch for {self.path} ({len(self.hunks)} hunks)"


@dataclass
class PatchSet:
    """A collection of patches for multiple files.
    
    A PatchSet represents a complete set of changes that can be
    applied together. It is used for:
    - Transaction commit/rollback
    - Git commits
    - Exporting changes
    - Sharing edits with others
    """
    
    patches: List[Patch] = field(default_factory=list)
    plan_id: Optional[str] = None
    created_at: float = field(default_factory=__import__('time').time)
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_patch(self, patch: Patch) -> None:
        """Add a patch to the set."""
        self.patches.append(patch)
    
    def get_affected_files(self) -> Set[str]:
        """Get all affected file paths."""
        return {p.path for p in self.patches}
    
    def apply_all(self, dry_run: bool = False) -> Tuple[bool, List[str]]:
        """Apply all patches in the set.
        
        Returns:
            (success, list_of_errors)
        """
        errors = []
        for patch in self.patches:
            success, msg = self._apply_patch(patch, dry_run)
            if not success:
                errors.append(f"{patch.path}: {msg}")
        
        return len(errors) == 0, errors
    
    def _apply_patch(self, patch: Patch, dry_run: bool) -> Tuple[bool, str]:
        """Apply a single patch."""
        try:
            import subprocess
            import tempfile
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.patch', delete=False) as f:
                f.write(patch.diff_text)
                patch_file = f.name
            
            cmd = ["patch", "-p1", "-i", patch_file]
            if dry_run:
                cmd.append("--dry-run")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=".",
                timeout=60,
            )
            
            Path(patch_file).unlink(missing_ok=True)
            
            if result.returncode == 0:
                return True, "OK"
            else:
                return False, result.stderr.strip() or "Unknown error"
        except Exception as e:
            return False, str(e)
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["patches"] = [p.to_dict() for p in self.patches]
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PatchSet:
        patches = [Patch.from_dict(p) for p in data.pop("patches", [])]
        ps = cls(**data)
        ps.patches = patches
        return ps
    
    def __str__(self) -> str:
        return f"PatchSet: {len(self.patches)} files, {self.plan_id}"


@dataclass
class Conflict:
    """A conflict between concurrent edits."""
    
    file_path: str
    line: int
    column: int
    conflicting_operations: List[str]  # operation IDs
    description: str
    resolution: Optional[str] = None  # "manual", "auto", "theirs", "ours"
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Conflict:
        return cls(**data)


@dataclass
class AgentCapability:
    """Capabilities of an editing agent."""
    
    agent_id: str
    languages: List[str]  # e.g., ["python", "typescript"]
    operations: List[EditType]  # supported operation types
    max_concurrent_edits: int = 5
    can_auto_repair: bool = False
    can_refactor: bool = False
    requires_approval: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["operations"] = [op.value for op in self.operations]
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentCapability:
        if "operations" in data:
            data["operations"] = [EditType(op) for op in data["operations"]]
        return cls(**data)