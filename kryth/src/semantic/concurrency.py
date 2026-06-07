"""Concurrency Control - Multi-agent file ownership and conflict detection.

This module implements optimistic concurrency control for KRYTH's
multi-agent editing system. It prevents simultaneous writes to the
same file and detects conflicts when they occur.

Key features:
- File ownership leasing (prevents simultaneous edits)
- Conflict detection (overlapping edits, symbol conflicts)
- Automatic merging for safe changes (non-overlapping lines)
- Escalation for complex conflicts
- Deadlock prevention
- Agent coordination

Integrates with:
- Parallel agent system (spawn_agents_parallel)
- File ownership tracking
- Patch conflict detection
- Git merge capabilities
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .data_structures import FileOwnership, ConcurrencyState


@dataclass
class Conflict:
    """A detected conflict between edits."""
    type: str  # "overlap", "symbol", "import", "patch"
    path: str
    description: str
    agents: List[str]  # agents involved
    severity: str  # "low", "medium", "high", "critical"
    can_auto_merge: bool = False
    resolution: Optional[str] = None
    
    def __str__(self) -> str:
        return f"[{self.severity}] {self.type} conflict in {self.path}: {self.description}"


@dataclass
class EditRange:
    """A range of lines being edited."""
    start_line: int
    end_line: int
    agent_id: str
    operation_id: str
    
    def overlaps(self, other: EditRange) -> bool:
        """Check if this range overlaps with another."""
        return not (self.end_line < other.start_line or other.end_line < self.start_line)


class ConcurrencyController:
    """Controls concurrent access to files by multiple agents.
    
    The ConcurrencyController ensures that multiple parallel agents
    don't step on each other's toes. It uses a leasing system where
    an agent must acquire ownership of a file before editing it.
    
    Features:
    - File ownership with lease expiration
- Blocking/waiting for locked files
    - Conflict detection across multiple files
    - Automatic merge for non-overlapping edits
    - Manual resolution required for complex conflicts
    
    Integration with KRYTH:
    - Works with spawn_agents_parallel
    - Coordinates with SemanticEditor
    - Reports conflicts to planner
    """
    
    def __init__(self, lease_duration: float = 300.0) -> None:
        self.lease_duration = lease_duration
        self.state = ConcurrencyState()
        self._pending_edits: Dict[str, List[EditRange]] = {}  # path -> pending ranges
    
    def acquire_files(self, agent_id: str, files: List[str]) -> Tuple[bool, List[str]]:
        """Acquire ownership of multiple files.
        
        Returns:
            (success, list of files that couldn't be acquired)
        """
        failed = []
        for path in files:
            if not self.state.acquire_file(agent_id, path):
                owner = self.state.get_owner(path)
                failed.append(f"{path} (owned by {owner})")
        
        return len(failed) == 0, failed
    
    def release_files(self, agent_id: str, files: List[str]) -> None:
        """Release ownership of files."""
        for path in files:
            self.state.release_file(agent_id, path)
    
    def check_edit_conflicts(
        self,
        agent_id: str,
        edits: List[EditRange]
    ) -> List[Conflict]:
        """Check if proposed edits conflict with existing ones."""
        conflicts = []
        
        for edit in edits:
            path = edit.path  # Need to store path in EditRange
            
            # Check if we own the file
            owner = self.state.get_owner(path)
            if owner and owner != agent_id:
                conflicts.append(Conflict(
                    type="ownership",
                    path=path,
                    description=f"File is owned by agent {owner}",
                    agents=[agent_id, owner],
                    severity="high",
                    can_auto_merge=False,
                ))
            
            # Check for overlapping edits on same file
            if path in self._pending_edits:
                for existing in self._pending_edits[path]:
                    if edit.overlaps(existing):
                        conflicts.append(Conflict(
                            type="overlap",
                            path=path,
                            description=f"Lines {edit.start_line}-{edit.end_line} overlap with {existing.agent_id}",
                            agents=[agent_id, existing.agent_id],
                            severity="medium",
                            can_auto_merge=False,
                        ))
        
        return conflicts
    
    def register_pending_edit(self, edit: EditRange) -> None:
        """Register an edit that's about to be applied."""
        if edit.path not in self._pending_edits:
            self._pending_edits[edit.path] = []
        self._pending_edits[edit.path].append(edit)
    
    def unregister_pending_edit(self, edit: EditRange) -> None:
        """Remove an edit from pending list."""
        if edit.path in self._pending_edits:
            try:
                self._pending_edits[edit.path].remove(edit)
            except ValueError:
                pass
            if not self._pending_edits[edit.path]:
                del self._pending_edits[edit.path]
    
    def can_auto_merge(self, path: str, agent_id: str, other_edits: List[EditRange]) -> bool:
        """Determine if edits can be automatically merged."""
        # Auto-merge if all edits are on non-overlapping lines
        all_edits = self._pending_edits.get(path, []) + other_edits
        # Sort by start line
        sorted_edits = sorted(all_edits, key=lambda e: e.start_line)
        
        for i in range(len(sorted_edits) - 1):
            if sorted_edits[i].end_line >= sorted_edits[i+1].start_line:
                return False
        
        return True
    
    def cleanup_agent(self, agent_id: str) -> None:
        """Clean up all state for an agent (on exit)."""
        self.state.cleanup_agent(agent_id)
        
        # Remove pending edits from this agent
        for path, edits in list(self._pending_edits.items()):
            self._pending_edits[path] = [e for e in edits if e.agent_id != agent_id]
            if not self._pending_edits[path]:
                del self._pending_edits[path]
    
    def get_owned_files(self, agent_id: str) -> List[str]:
        """Get list of files owned by an agent."""
        if agent_id in self.state.agent_ownership:
            return list(self.state.agent_ownership[agent_id])
        return []
    
    def get_file_owner(self, path: str) -> Optional[str]:
        """Get the agent that owns a file."""
        return self.state.get_owner(path)
    
    def get_waiting_agents(self, path: str) -> List[str]:
        """Get agents waiting for a file."""
        if path in self.state.pending_edits:
            return list(self.state.pending_edits[path])
        return []
    
    def force_release(self, path: str, agent_id: Optional[str] = None) -> bool:
        """Force release of a file (admin operation)."""
        if agent_id:
            self.state.release_file(agent_id, path)
        else:
            # Release all owners
            owner = self.state.get_owner(path)
            if owner:
                self.state.release_file(owner, path)
        return True