"""Semantic Editor - Core engine for semantic code editing.

This is the main entry point for KRYTH's semantic editing capabilities.
The SemanticEditor orchestrates all components:

- IntegrationManager (external systems)
- IntelligentInserter (code insertion)
- RefactoringEngine (semantic transformations)
- PatchGenerator (diff creation)
- TransactionManager (atomic operations)
- ConflictDetector (concurrency control)
- ValidationPipeline (quality gates)
- AutoRepairEngine (error recovery)
- EditMemory (learning)
- SessionTracker (audit trail)

The editor provides a high-level API for:
- Analyzing code intent
- Generating edit plans
- Executing edits safely
- Validating results
- Rolling back on failure

It is the brain of KRYTH's semantic editing system.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .data_structures import EditPlan, EditOperation, ValidationResult, EditPriority
from .integration import IntegrationManager
from .insertion import IntelligentInserter, InsertionContext
from .refactoring import RefactoringEngine
from .patch_generator import PatchGenerator
from .transaction import TransactionManager
from .conflict_detector import ConflictDetector
from .validator import ValidationPipeline
from .auto_repair import AutoRepairEngine
from .memory import EditMemory
from .session_tracker import SessionTracker


@dataclass
class EditorConfig:
    """Configuration for the SemanticEditor."""
    project_root: str
    enable_auto_repair: bool = True
    enable_memory: bool = True
    enable_session_tracking: bool = True
    max_repair_attempts: int = 3
    validation_timeout: float = 30.0
    transaction_timeout: float = 300.0
    concurrency_enabled: bool = True
    dry_run: bool = False
    require_approval: bool = False  # Require human approval before applying


class SemanticEditor:
    """The core semantic editing engine.
    
    The SemanticEditor is the central orchestrator that coordinates all
    semantic editing capabilities. It provides a unified API for:
    
    High-level operations:
    - edit(intent, context) -> EditPlan
    - refactor(intent, target) -> EditPlan
    - insert(code, location) -> EditPlan
    - rename(old_name, new_name, scope) -> EditPlan
    - extract(block, target) -> EditPlan
    
    Execution:
    - execute(plan) -> ExecutionResult
    - validate(plan) -> ValidationResult
    - repair(plan, validation) -> EditPlan
    
    Analysis:
    - analyze_intent(text) -> IntentAnalysis
    - suggest_edits(context) -> List[EditSuggestion]
    - find_symbols(query) -> List[SymbolReference]
    
    The editor ensures:
    - All edits are semantically aware
    - Changes are atomic and reversible
    - Conflicts are detected and resolved
    - Validation passes before applying
    - Errors are automatically repaired when possible
    - Everything is audited and learnable
    
    Integration:
    - Uses IntegrationManager to access external systems
    - Works with language-specific processors
    - Calls formatters and linters as needed
    - Persists memory and session data
    """
    
    def __init__(self, config: EditorConfig) -> None:
        self.config = config
        self.project_root = Path(config.project_root).resolve()
        
        # Initialize subsystems
        self.integration = IntegrationManager(str(self.project_root))
        self.inserter = IntelligentInserter()
        self.refactorer = RefactoringEngine()
        self.patch_gen = PatchGenerator()
        self.transaction_mgr = TransactionManager()
        self.conflict_detector = ConflictDetector()
        self.validator = ValidationPipeline()
        self.repair_engine = AutoRepairEngine()
        
        # Optional subsystems
        self.memory: Optional[EditMemory] = None
        if config.enable_memory:
            memory_path = self.project_root / ".kryth" / "memory.db"
            self.memory = EditMemory(str(memory_path))
        
        self.session_tracker: Optional[SessionTracker] = None
        if config.enable_session_tracking:
            sessions_dir = self.project_root / ".kryth" / "sessions"
            self.session_tracker = SessionTracker(str(sessions_dir))
        
        # State
        self._current_session: Optional[str] = None
        self._active_transaction: Optional[str] = None
    
    def start_session(self, agent_id: str, description: str) -> str:
        """Start an editing session."""
        if self.session_tracker:
            session = self.session_tracker.start_session(agent_id, description)
            self._current_session = session.session_id
            return session.session_id
        return "no-session"
    
    def end_session(self, status: str = "completed") -> None:
        """End the current session."""
        if self.session_tracker and self._current_session:
            self.session_tracker.end_session(status)
            self._current_session = None
    
    def edit(
        self,
        intent: str,
        context: Optional[Dict[str, Any]] = None,
        target_files: Optional[List[str]] = None,
    ) -> EditPlan:
        """Create an edit plan based on natural language intent.
        
        This is the main entry point for semantic editing. It analyzes
        the user's intent, determines the necessary changes, and creates
        a comprehensive edit plan.
        
        Args:
            intent: Natural language description of the desired change
            context: Additional context (current code, symbols, etc.)
            target_files: Optional list of files to limit scope
            
        Returns:
            EditPlan with all required operations
        """
        context = context or {}
        
        # Record in session
        if self.session_tracker and self._current_session:
            self.session_tracker.record_plan_created(EditPlan(
                id="temp",
                description=intent,
                created_by="user",
            ))
        
        # Step 1: Analyze intent to determine edit type
        analysis = self._analyze_intent(intent)
        
        # Step 2: Find relevant code locations
        locations = self._find_target_locations(analysis, target_files)
        
        # Step 3: Generate edit operations
        plan = self._generate_edit_plan(intent, analysis, locations, context)
        
        # Step 4: Estimate impact
        plan.estimated_impact = self._estimate_impact(plan)
        
        # Step 5: Check for similar past edits
        if self.memory:
            similar = self.memory.find_similar(intent)
            if similar:
                # Could use to optimize plan
                pass
        
        return plan
    
    def execute(
        self,
        plan: EditPlan,
        validate: bool = True,
        auto_repair: bool = True,
    ) -> Tuple[bool, str, Optional[ValidationResult]]:
        """Execute an edit plan.
        
        Args:
            plan: The edit plan to execute
            validate: Whether to validate before and after
            auto_repair: Whether to attempt auto-repair on failure
            
        Returns:
            (success, message, validation_result)
        """
        if self.config.dry_run:
            return True, "Dry run - no changes made", None
        
        if self.config.require_approval:
            # Would prompt user for approval
            pass
        
        # Start transaction if needed
        if plan.transaction_required:
            self._active_transaction = self.transaction_mgr.begin_transaction(
                plan.id, timeout=self.config.transaction_timeout
            )
        
        try:
            # Pre-validation
            if validate:
                pre_validation = self.validator.validate_plan(plan, fast=True)
                if not pre_validation.valid:
                    return False, "Pre-execution validation failed", pre_validation
            
            # Check for conflicts
            if self.config.concurrency_enabled:
                conflicts = self.conflict_detector.detect_conflicts(plan)
                if conflicts:
                    return False, f"Conflicts detected: {conflicts}", None
            
            # Execute operations in dependency order
            operations = self._sort_operations_by_dependencies(plan.operations)
            
            for op in operations:
                success, error = self._execute_operation(op)
                if not success:
                    # Record failure
                    if self.session_tracker:
                        self.session_tracker.record_operation_applied(op, False, error)
                    
                    # Attempt repair if enabled
                    if auto_repair and self.config.enable_auto_repair:
                        repaired_plan, repair_msg = self._attempt_repair(plan, error)
                        if repaired_plan:
                            # Retry with repaired plan
                            return self.execute(repaired_plan, validate, auto_repair=False)
                    
                    # Rollback transaction
                    if self._active_transaction:
                        self.transaction_mgr.rollback_transaction(self._active_transaction)
                    
                    return False, f"Operation failed: {error}", None
                
                # Record success
                if self.session_tracker:
                    self.session_tracker.record_operation_applied(op, True)
            
            # Post-validation
            if validate:
                post_validation = self.validator.validate_plan(plan, fast=False)
                if not post_validation.valid:
                    # Try to repair
                    if auto_repair:
                        success, msg, repaired_plan = self.repair_engine.attempt_repair(
                            plan, post_validation, max_attempts=self.config.max_repair_attempts
                        )
                        if success and repaired_plan:
                            # Apply repairs
                            return self.execute(repaired_plan, validate=False, auto_repair=False)
                    
                    # Rollback
                    if self._active_transaction:
                        self.transaction_mgr.rollback_transaction(self._active_transaction)
                    
                    return False, f"Post-execution validation failed: {post_validation.errors}", post_validation
            
            # Commit transaction
            if self._active_transaction:
                self.transaction_mgr.commit_transaction(self._active_transaction)
                self._active_transaction = None
            
            # Record in memory
            if self.memory:
                self.memory.record_execution(plan, True, post_validation if validate else None)
            
            return True, "Edit executed successfully", post_validation if validate else None
        
        except Exception as e:
            # Rollback on any exception
            if self._active_transaction:
                self.transaction_mgr.rollback_transaction(self._active_transaction)
            raise
    
    def validate(
        self,
        plan: EditPlan,
        fast: bool = True,
    ) -> ValidationResult:
        """Validate an edit plan without executing it."""
        return self.validator.validate_plan(plan, fast=fast)
    
    def _analyze_intent(self, intent: str) -> Dict[str, Any]:
        """Analyze user intent to determine edit characteristics."""
        # This would use NLP/LLM to classify intent
        # For now, simple keyword matching
        intent_lower = intent.lower()
        
        analysis = {
            "type": "unknown",
            "confidence": 0.5,
            "target_symbols": [],
            "target_files": [],
            "required_changes": [],
        }
        
        if "rename" in intent_lower:
            analysis["type"] = "rename"
        elif "extract" in intent_lower:
            analysis["type"] = "extract"
        elif "inline" in intent_lower:
            analysis["type"] = "inline"
        elif "move" in intent_lower:
            analysis["type"] = "move"
        elif "add" in intent_lower or "insert" in intent_lower:
            analysis["type"] = "insert"
        elif "delete" in intent_lower or "remove" in intent_lower:
            analysis["type"] = "delete"
        elif "format" in intent_lower:
            analysis["type"] = "format"
        elif "fix" in intent_lower:
            analysis["type"] = "fix"
        
        return analysis
    
    def _find_target_locations(
        self,
        analysis: Dict[str, Any],
        target_files: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        """Find the code locations that need to be changed."""
        locations = []
        
        # Use integration to find symbols
        if analysis.get("target_symbols"):
            for symbol in analysis["target_symbols"]:
                refs = self.integration.lookup_symbol(symbol)
                for ref in refs:
                    locations.append({
                        "symbol": symbol,
                        "file": ref.file_path,
                        "line": ref.line,
                        "column": ref.column,
                        "kind": ref.kind,
                    })
        
        # Use target files if provided
        if target_files:
            for file_path in target_files:
                locations.append({
                    "file": file_path,
                    "line": 1,
                    "column": 0,
                    "kind": "file",
                })
        
        return locations
    
    def _generate_edit_plan(
        self,
        intent: str,
        analysis: Dict[str, Any],
        locations: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> EditPlan:
        """Generate an EditPlan from analysis."""
        plan = EditPlan(
            id=f"plan_{int(time.time())}",
            description=intent,
            created_by="semantic_editor",
        )
        
        edit_type = analysis["type"]
        
        if edit_type == "rename":
            # Generate rename operations
            old_name = context.get("old_name", "")
            new_name = context.get("new_name", "")
            for loc in locations:
                op = EditOperation(
                    type=EditType.RENAME_SYMBOL,
                    target_path=loc["file"],
                    symbol=loc.get("symbol"),
                    location=loc,
                    description=f"Rename {old_name} to {new_name}",
                    metadata={"old_name": old_name, "new_name": new_name},
                )
                plan.add_operation(op)
        
        elif edit_type == "insert":
            # Generate insertion operations
            code = context.get("code", "")
            for loc in locations:
                insert_context = InsertionContext(
                    file_path=loc["file"],
                    line=loc.get("line", 1),
                    column=loc.get("column", 0),
                )
                insert_plan = self.inserter.insert_code(code, insert_context)
                for op in insert_plan.operations:
                    plan.add_operation(op)
        
        elif edit_type == "format":
            # Generate format operations
            for loc in locations:
                op = EditOperation(
                    type=EditType.FORMAT,
                    target_path=loc["file"],
                    description=f"Format {loc['file']}",
                )
                plan.add_operation(op)
        
        # Add more edit types...
        
        return plan
    
    def _estimate_impact(self, plan: EditPlan) -> Dict[str, Any]:
        """Estimate the impact of an edit plan."""
        affected_files = plan.get_affected_files()
        
        impact = {
            "files_modified": len(affected_files),
            "operations_count": len(plan.operations),
            "risk_level": "low",  # Would calculate based on type
            "estimated_duration": len(plan.operations) * 0.5,  # seconds
        }
        
        return impact
    
    def _sort_operations_by_dependencies(self, operations: List[EditOperation]) -> List[EditOperation]:
        """Sort operations respecting dependencies."""
        # Simple topological sort
        sorted_ops = []
        visited = set()
        temp_visited = set()
        
        def visit(op: EditOperation) -> None:
            if op.id in visited:
                return
            if op.id in temp_visited:
                # Cycle detected - just add anyway
                sorted_ops.append(op)
                return
            
            temp_visited.add(op.id)
            for dep_id in op.dependencies:
                # Find dependent operation
                for other in operations:
                    if other.id == dep_id:
                        visit(other)
            temp_visited.remove(op.id)
            visited.add(op.id)
            sorted_ops.append(op)
        
        for op in operations:
            if op.id not in visited:
                visit(op)
        
        return sorted_ops
    
    def _execute_operation(self, op: EditOperation) -> Tuple[bool, Optional[str]]:
        """Execute a single operation."""
        try:
            # This would delegate to appropriate handler
            # For now, placeholder
            return True, None
        except Exception as e:
            return False, str(e)
    
    def _attempt_repair(
        self,
        plan: EditPlan,
        error: str,
    ) -> Tuple[Optional[EditPlan], str]:
        """Attempt to repair a failed plan."""
        # Create a fake validation result
        validation = ValidationResult(valid=False, errors=[error])
        
        success, msg, repaired_plan = self.repair_engine.attempt_repair(
            plan, validation, max_attempts=self.config.max_repair_attempts
        )
        
        return repaired_plan, msg
    
    def get_memory_statistics(self) -> Dict[str, Any]:
        """Get statistics from edit memory."""
        if self.memory:
            return self.memory.get_statistics()
        return {}
    
    def get_session_statistics(self) -> Dict[str, Any]:
        """Get statistics from session tracker."""
        if self.session_tracker:
            return self.session_tracker.get_statistics()
        return {}
    
    def close(self) -> None:
        """Cleanup resources."""
        if self.memory:
            self.memory.close()
        if self.session_tracker:
            self.session_tracker.close()