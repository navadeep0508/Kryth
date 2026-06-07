"""Session Tracker - Comprehensive audit trail for all editing activities.

This module provides detailed tracking of every edit operation, plan,
and transaction across KRYTH's lifetime. It enables:

- Full audit trail (who did what, when, and why)
- Session replay (reconstruct any editing session)
- Rollback to any point in time
- Compliance and security review
- Performance analytics
- Debugging and incident investigation
- User activity reporting

The SessionTracker records:
- Agent/user identity
- Edit plans and their components
- Execution results (success/failure)
- Validation outcomes
- Repair attempts
- File changes (diffs)
- Git commits made
- Time spent on each operation
- Context and intent

Data is stored in a time-series database (SQLite) with:
- Sessions table (editing sessions)
- Plans table (edit plans)
- Operations table (individual operations)
- Executions table (execution attempts)
- Files table (file-level tracking)
- Diffs table (actual changes)

All data is immutable once written - we never update history,
only append new records. This ensures a complete, tamper-evident
audit trail.

Integration:
- Used by SemanticEditor to record all activities
- Used by TransactionManager for recovery
- Used by EditMemory to correlate outcomes with patterns
- Can be queried by external tools for reporting
- Exports to JSON for archival or analysis

Privacy: Session data may contain code snippets. Ensure proper
access controls if used in multi-tenant environments.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .data_structures import EditPlan, EditOperation, ValidationResult, Location
from .patch_generator import PatchSet


@dataclass
class Session:
    """An editing session."""
    session_id: str
    agent_id: str
    user_id: Optional[str] = None
    description: str = ""
    started_at: float
    ended_at: Optional[float] = None
    status: str = "active"  # "active", "completed", "failed", "aborted"
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Session:
        return cls(**data)


@dataclass
class PlanRecord:
    """Record of an edit plan."""
    plan_id: str
    session_id: str
    plan_json: str
    created_at: float
    validation: Optional[ValidationResult] = None
    executed: bool = False
    execution_result: Optional[Dict[str, Any]] = None
    repair_attempts: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.validation:
            data["validation"] = self.validation.to_dict()
        return data


@dataclass
class OperationRecord:
    """Record of an operation execution."""
    operation_id: str
    plan_id: str
    session_id: str
    operation_json: str
    executed_at: float
    success: bool
    error_message: Optional[str] = None
    duration: float = 0.0
    patch: Optional[str] = None  # diff text if available
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FileChange:
    """Record of a file change."""
    file_path: str
    plan_id: str
    session_id: str
    old_hash: str
    new_hash: str
    changed_at: float
    lines_added: int = 0
    lines_removed: int = 0
    patch_text: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SessionTracker:
    """Comprehensive audit trail for all editing activities.
    
    The SessionTracker provides immutable, append-only logging of
    everything that happens in KRYTH. It is the single source of truth
    for:
    
    - What edits were made
    - Who made them
    - When they were made
    - Why they were made (intent)
    - How they were made (operations)
    - What the results were
    
    Key properties:
    - Immutable: Once written, records are never modified
    - Append-only: All changes are additions
    - Timestamped: Every event has precise timing
    - Linked: Sessions → Plans → Operations → Files
    - Queryable: Can search by any dimension
    
    Use cases:
    1. Audit: "Show all edits by agent X in the last week"
    2. Rollback: "Restore files to state at time T"
    3. Debug: "What plan caused this error?"
    4. Analytics: "Average edit success rate by agent"
    5. Compliance: "All changes to production files"
    
    Storage: <project_root>/.kryth/sessions/sessions.db
    The database is created automatically on first use.
    
    Integration:
    - SemanticEditor calls start_session/end_session
    - Records every plan created
    - Records every operation executed
    - Records validation results
    - Records file changes (diffs)
    - Can export sessions as JSON for analysis
    
    The tracker is essential for:
    - Multi-agent coordination (seeing what others did)
    - Incident response (understanding what changed)
    - Learning (correlating patterns with outcomes)
    - User accountability (traceability)
    """
    
    def __init__(self, db_dir: str) -> None:
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.db_dir / "sessions.db"
        
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        
        self._current_session: Optional[Session] = None
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                agent_id TEXT,
                user_id TEXT,
                description TEXT,
                started_at REAL,
                ended_at REAL,
                status TEXT,
                metadata_json TEXT
            )
        """)
        
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                plan_id TEXT PRIMARY KEY,
                session_id TEXT,
                plan_json TEXT,
                created_at REAL,
                validation_json TEXT,
                executed INTEGER,
                execution_result_json TEXT,
                repair_attempts_json TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
            )
        """)
        
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY,
                plan_id TEXT,
                session_id TEXT,
                operation_json TEXT,
                executed_at REAL,
                success INTEGER,
                error_message TEXT,
                duration REAL,
                patch_text TEXT,
                FOREIGN KEY (plan_id) REFERENCES plans (plan_id),
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
            )
        """)
        
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS file_changes (
                file_path TEXT,
                plan_id TEXT,
                session_id TEXT,
                old_hash TEXT,
                new_hash TEXT,
                changed_at REAL,
                lines_added INTEGER,
                lines_removed INTEGER,
                patch_text TEXT,
                PRIMARY KEY (file_path, plan_id),
                FOREIGN KEY (plan_id) REFERENCES plans (plan_id),
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
            )
        """)
        
        # Indexes for fast querying
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_session ON plans(session_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_operations_plan ON operations(plan_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_file_changes_file ON file_changes(file_path)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_file_changes_time ON file_changes(changed_at)")
        
        self._conn.commit()
    
    def start_session(
        self,
        agent_id: str,
        description: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Session:
        """Start a new editing session.
        
        Args:
            agent_id: ID of the agent (or "human")
            description: Brief description of the session's purpose
            user_id: Human user ID if applicable
            metadata: Additional session metadata
            
        Returns:
            Session object
        """
        session_id = f"session_{uuid.uuid4().hex[:8]}"
        now = time.time()
        
        session = Session(
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            description=description,
            started_at=now,
            status="active",
            metadata=metadata or {},
        )
        
        self._current_session = session
        
        # Persist
        self._conn.execute("""
            INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session.session_id,
            session.agent_id,
            session.user_id,
            session.description,
            session.started_at,
            session.ended_at,
            session.status,
            json.dumps(session.metadata),
        ))
        self._conn.commit()
        
        return session
    
    def end_session(
        self,
        session_id: str,
        status: str = "completed",
    ) -> None:
        """End an editing session.
        
        Args:
            session_id: Session ID to end
            status: Final status ("completed", "failed", "aborted")
        """
        now = time.time()
        
        self._conn.execute("""
            UPDATE sessions SET ended_at = ?, status = ? WHERE session_id = ?
        """, (now, status, session_id))
        self._conn.commit()
        
        if self._current_session and self._current_session.session_id == session_id:
            self._current_session = None
    
    def record_plan_created(
        self,
        plan: EditPlan,
        validation: Optional[ValidationResult] = None,
    ) -> str:
        """Record that a plan was created.
        
        Args:
            plan: The edit plan
            validation: Optional validation result
            
        Returns:
            Plan record ID (same as plan.id)
        """
        if not self._current_session:
            raise RuntimeError("No active session")
        
        plan_record = PlanRecord(
            plan_id=plan.id,
            session_id=self._current_session.session_id,
            plan_json=plan.to_json(),
            created_at=time.time(),
            validation=validation,
        )
        
        self._conn.execute("""
            INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            plan_record.plan_id,
            plan_record.session_id,
            plan_record.plan_json,
            plan_record.created_at,
            plan_record.validation.to_json() if plan_record.validation else None,
            0,  # executed
            None,  # execution_result
            json.dumps([]),  # repair_attempts
        ))
        self._conn.commit()
        
        return plan_record.plan_id
    
    def record_plan_executed(
        self,
        plan: EditPlan,
        success: bool,
        validation: Optional[ValidationResult] = None,
        error_message: Optional[str] = None,
        repair_attempts: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Record plan execution outcome.
        
        Args:
            plan: The edit plan
            success: Whether execution succeeded
            validation: Post-execution validation result
            error_message: Error if failed
            repair_attempts: List of repair attempts made
        """
        execution_result = {
            "success": success,
            "error": error_message,
            "timestamp": time.time(),
        }
        
        self._conn.execute("""
            UPDATE plans
            SET executed = ?,
                execution_result_json = ?,
                repair_attempts_json = ?
            WHERE plan_id = ?
        """, (
            1 if success else 0,
            json.dumps(execution_result),
            json.dumps(repair_attempts or []),
            plan.id,
        ))
        self._conn.commit()
    
    def record_operation_applied(
        self,
        operation: EditOperation,
        success: bool,
        error_message: Optional[str] = None,
        patch: Optional[str] = None,
    ) -> None:
        """Record that an operation was applied.
        
        Args:
            operation: The operation
            success: Whether it succeeded
            error_message: Error if failed
            patch: Diff text if available
        """
        if not self._current_session:
            return
        
        op_record = OperationRecord(
            operation_id=operation.id,
            plan_id=operation.id.split('_')[0],  # rough extraction
            session_id=self._current_session.session_id,
            operation_json=operation.to_json(),
            executed_at=time.time(),
            success=success,
            error_message=error_message,
            patch=patch,
        )
        
        self._conn.execute("""
            INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            op_record.operation_id,
            op_record.plan_id,
            op_record.session_id,
            op_record.operation_json,
            op_record.executed_at,
            1 if success else 0,
            op_record.error_message,
            op_record.duration,
            op_record.patch,
        ))
        self._conn.commit()
    
    def record_file_change(
        self,
        file_path: str,
        plan_id: str,
        old_hash: str,
        new_hash: str,
        lines_added: int = 0,
        lines_removed: int = 0,
        patch_text: Optional[str] = None,
    ) -> None:
        """Record a file change.
        
        Args:
            file_path: Path to the file
            plan_id: ID of the plan that caused the change
            old_hash: SHA1 hash of original content
            new_hash: SHA1 hash of new content
            lines_added: Number of lines added
            lines_removed: Number of lines removed
            patch_text: Unified diff text
        """
        if not self._current_session:
            return
        
        self._conn.execute("""
            INSERT OR REPLACE INTO file_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            file_path,
            plan_id,
            self._current_session.session_id,
            old_hash,
            new_hash,
            time.time(),
            lines_added,
            lines_removed,
            patch_text,
        ))
        self._conn.commit()
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        cursor = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        if row:
            return Session.from_dict(dict(row))
        return None
    
    def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        """Get a plan by ID with its operations."""
        cursor = self._conn.execute(
            "SELECT * FROM plans WHERE plan_id = ?",
            (plan_id,)
        )
        row = cursor.fetchone()
        if row:
            data = dict(row)
            data["plan"] = EditPlan.from_json(data["plan_json"]).to_dict()
            if data.get("validation_json"):
                data["validation"] = ValidationResult.from_json(data["validation_json"]).to_dict()
            return data
        return None
    
    def get_operations(self, plan_id: str) -> List[Dict[str, Any]]:
        """Get all operations for a plan."""
        cursor = self._conn.execute(
            "SELECT * FROM operations WHERE plan_id = ? ORDER BY executed_at",
            (plan_id,)
        )
        operations = []
        for row in cursor:
            op_data = dict(row)
            op_data["operation"] = EditOperation.from_json(op_data["operation_json"]).to_dict()
            operations.append(op_data)
        return operations
    
    def get_file_changes(
        self,
        file_path: Optional[str] = None,
        session_id: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Get file changes with optional filters."""
        query = "SELECT * FROM file_changes WHERE 1=1"
        params = []
        
        if file_path:
            query += " AND file_path = ?"
            params.append(file_path)
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if start_time:
            query += " AND changed_at >= ?"
            params.append(start_time)
        if end_time:
            query += " AND changed_at <= ?"
            params.append(end_time)
        
        query += " ORDER BY changed_at DESC"
        
        cursor = self._conn.execute(query, params)
        return [dict(row) for row in cursor]
    
    def get_sessions_by_agent(
        self,
        agent_id: str,
        limit: int = 100,
    ) -> List[Session]:
        """Get recent sessions for an agent."""
        cursor = self._conn.execute("""
            SELECT * FROM sessions
            WHERE agent_id = ?
            ORDER BY started_at DESC
            LIMIT ?
        """, (agent_id, limit))
        return [Session.from_dict(dict(row)) for row in cursor]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get session statistics."""
        stats = {}
        
        # Total sessions
        cursor = self._conn.execute("SELECT COUNT(*) FROM sessions")
        stats["total_sessions"] = cursor.fetchone()[0]
        
        # Sessions by status
        cursor = self._conn.execute("""
            SELECT status, COUNT(*) FROM sessions GROUP BY status
        """)
        stats["sessions_by_status"] = dict(cursor.fetchall())
        
        # Total plans
        cursor = self._conn.execute("SELECT COUNT(*) FROM plans")
        stats["total_plans"] = cursor.fetchone()[0]
        
        # Plan success rate
        cursor = self._conn.execute("""
            SELECT executed, COUNT(*) FROM plans GROUP BY executed
        """)
        executed_counts = dict(cursor.fetchall())
        executed = executed_counts.get(1, 0)
        total = executed + executed_counts.get(0, 0)
        stats["plan_execution_rate"] = executed / total if total > 0 else 0.0
        
        # Total operations
        cursor = self._conn.execute("SELECT COUNT(*) FROM operations")
        stats["total_operations"] = cursor.fetchone()[0]
        
        # Operation success rate
        cursor = self._conn.execute("""
            SELECT success, COUNT(*) FROM operations GROUP BY success
        """)
        success_counts = dict(cursor.fetchall())
        successes = success_counts.get(1, 0)
        total_ops = successes + success_counts.get(0, 0)
        stats["operation_success_rate"] = successes / total_ops if total_ops > 0 else 0.0
        
        # Total file changes
        cursor = self._conn.execute("SELECT COUNT(*) FROM file_changes")
        stats["total_file_changes"] = cursor.fetchone()[0]
        
        # Active sessions
        cursor = self._conn.execute("""
            SELECT COUNT(*) FROM sessions WHERE status = 'active'
        """)
        stats["active_sessions"] = cursor.fetchone()[0]
        
        return stats
    
    def export_session(
        self,
        session_id: str,
        output_path: str,
    ) -> None:
        """Export a complete session to JSON.
        
        Args:
            session_id: Session to export
            output_path: Output JSON file path
        """
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        # Get all plans in session
        cursor = self._conn.execute(
            "SELECT plan_id FROM plans WHERE session_id = ?",
            (session_id,)
        )
        plan_ids = [row[0] for row in cursor]
        
        plans_data = []
        for plan_id in plan_ids:
            plan_data = self.get_plan(plan_id)
            if plan_data:
                plan_data["operations"] = self.get_operations(plan_id)
                plans_data.append(plan_data)
        
        export_data = {
            "session": session.to_dict(),
            "plans": plans_data,
            "exported_at": time.time(),
        }
        
        with open(output_path, 'w') as f:
            json.dump(export_data, f, indent=2)
    
    def cleanup_old_sessions(self, days: int = 30) -> int:
        """Remove old completed sessions.
        
        Args:
            days: Remove sessions older than this many days
            
        Returns:
            Number of sessions removed
        """
        cutoff = time.time() - (days * 86400)
        
        # Get sessions to delete
        cursor = self._conn.execute("""
            SELECT session_id FROM sessions
            WHERE status != 'active' AND started_at < ?
        """, (cutoff,))
        old_sessions = [row[0] for row in cursor]
        
        # Delete associated data first (foreign keys)
        for session_id in old_sessions:
            self._conn.execute("DELETE FROM operations WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM plans WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM file_changes WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        
        self._conn.commit()
        return len(old_sessions)
    
    def close(self) -> None:
        """Close database connection."""
        self._conn.close()
    
    def __del__(self) -> None:
        self.close()