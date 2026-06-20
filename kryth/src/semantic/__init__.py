"""KRYTH Semantic Write & Edit Engine.

A production-grade semantic code editing system that transforms KRYTH from
a text editor into an intelligent code modification engine.

Core principles:
- Patch-first: All edits generate minimal, reviewable patches
- Transaction safety: Atomic commits with automatic rollback
- Semantic awareness: AST-based edits preserve structure and formatting
- Multi-agent coordination: File ownership prevents collisions
- Automatic validation: Syntax, lint, tests before commit
- Intelligent repair: Auto-fix validation failures

Architecture layers:
1. Intent Analysis → Understand user request
2. Impact Analysis → Determine affected files/symbols
3. LSP/Graphify → Find references and dependencies
4. AST/tree-sitter → Precise code structure
5. ast-grep/Comby → Pattern-based transformations
6. Patch Generator → Minimal diff creation
7. diff-match-patch → Optimize edit size
8. Transaction Layer → Atomic apply with rollback
9. Validation → Syntax, lint, tests, security
10. Commit → Git integration

All operations are reversible and auditable.
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "KRYTH Team"

# Core semantic editing classes
from .editor import SemanticEditor, EditOperation, EditPlan
from .patch import PatchGenerator, PatchSet
from .transaction import TransactionManager, Transaction
from .validator import ValidationPipeline, ValidationResult
from .concurrency import ConcurrencyController, FileOwnership
from .refactoring import RefactoringEngine
from .insertion import IntelligentInserter, InsertionContext
from .auto_repair import AutoRepairEngine, RepairAttempt
from .memory import EditMemory

__all__ = [
    "SemanticEditor",
    "EditOperation",
    "EditPlan",
    "PatchGenerator",
    "PatchSet",
    "TransactionManager",
    "Transaction",
    "ValidationPipeline",
    "ValidationResult",
    "ConcurrencyController",
    "FileOwnership",
    "RefactoringEngine",
    "IntelligentInserter",
    "InsertionContext",
    "AutoRepairEngine",
    "RepairAttempt",
    "EditMemory",
]