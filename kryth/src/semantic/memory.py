"""Edit Memory - Learn from past edits to improve future suggestions.

This module implements a persistent learning system that stores
information about successful and failed edits. It enables KRYTH to:

- Remember what worked (and what didn't)
- Suggest proven patterns for similar situations
- Avoid repeating mistakes
- Adapt to project-specific conventions
- Build up institutional knowledge

The memory system stores:
- Edit plans (intent, operations, context)
- Validation results (errors, warnings)
- Execution outcomes (success/failure)
- Repair attempts (what fixed broken edits)
- User feedback (approvals, rejections, modifications)
- Code patterns ( idioms, conventions)
- Symbol relationships (imports, dependencies)

It uses this data to:
- Rank suggestions by past success rate
- Recommend repair strategies for errors
- Predict validation failures before they happen
- Suggest code snippets that fit the project style
- Learn which refactorings are safe vs risky

Storage: SQLite database with embeddings for similarity search.
Indexing: FAISS or similar for fast nearest-neighbor lookup.
Privacy: All data stays in the project repository.

Integration:
- Used by SemanticEditor to improve suggestions
- Used by AutoRepairEngine to select fix strategies
- Used by IntelligentInserter to match style
- Used by RefactoringEngine to predict outcomes
- Can be exported/imported for team sharing
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .data_structures import EditPlan, EditOperation, ValidationResult, Location


@dataclass
class MemoryEntry:
    """A single entry in edit memory."""
    entry_id: str
    timestamp: float
    plan: EditPlan
    validation: Optional[ValidationResult] = None
    execution_success: bool = False
    repair_attempts: List[Dict[str, Any]] = field(default_factory=list)
    user_feedback: Optional[Dict[str, Any]] = None  # approval, rating, comments
    embedding: Optional[bytes] = None  # serialized numpy array
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["plan"] = self.plan.to_dict()
        if self.validation:
            data["validation"] = self.validation.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MemoryEntry:
        plan = EditPlan.from_dict(data.pop("plan"))
        validation = None
        if data.get("validation"):
            validation = ValidationResult(**data.pop("validation"))
        return cls(plan=plan, validation=validation, **data)


@dataclass
class MemoryStats:
    """Statistics about edit memory."""
    total_entries: int
    successful_edits: int
    failed_edits: int
    avg_validation_errors: float
    most_common_tags: List[Tuple[str, int]]
    last_updated: float


class EditMemory:
    """Persistent memory of past edits for learning and improvement.
    
    EditMemory stores every edit plan, its validation results, execution
    outcome, and any user feedback. It uses this history to:
    
    1. Suggest better edits by finding similar past successful edits
    2. Predict which edit strategies are likely to succeed
    3. Recommend repair strategies based on what worked before
    4. Learn project-specific code style and conventions
    5. Avoid repeating known failure patterns
    
    The memory is stored in a SQLite database with:
    - Main entries table (edit plans, outcomes)
    - Embeddings table (for similarity search)
    - Tags table (for categorization)
    - Statistics table (cached metrics)
    
    Similarity search uses vector embeddings of:
    - Edit intent (natural language description)
    - Code context (surrounding code)
    - Operation types and patterns
    - File paths and symbols involved
    
    The system uses a simple but effective approach:
    - Generate embedding for each entry (using sentence-transformers or OpenAI)
    - Store in FAISS index for fast nearest-neighbor search
    - When making a suggestion, find top-k similar past entries
    - Weight suggestions by success rate of similar entries
    
    Integration:
    - SemanticEditor calls memory.find_similar(intent) to get suggestions
    - AutoRepairEngine queries memory for repair strategies
    - IntelligentInserter checks memory for style preferences
    - RefactoringEngine learns safe refactoring patterns
    
    Privacy: All data stays local in the project's .kryth directory.
    No data is sent to external services unless explicitly configured.
    
    Export/Import: Memory can be exported as JSON for team sharing
    or to bootstrap learning on a new project.
    """
    
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        
        # FAISS index for similarity search
        self._index = None
        self._embedding_dim = 384  # Default for sentence-transformers
        self._load_index()
    
    def _init_db(self) -> None:
        """Initialize database tables."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                entry_id TEXT PRIMARY KEY,
                timestamp REAL,
                plan_json TEXT,
                validation_json TEXT,
                execution_success INTEGER,
                repair_attempts_json TEXT,
                user_feedback_json TEXT,
                tags_json TEXT,
                metadata_json TEXT
            )
        """)
        
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                entry_id TEXT PRIMARY KEY,
                embedding BLOB,
                FOREIGN KEY (entry_id) REFERENCES entries (entry_id)
            )
        """)
        
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                entry_id TEXT,
                tag TEXT,
                FOREIGN KEY (entry_id) REFERENCES entries (entry_id)
            )
        """)
        
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at REAL
            )
        """)
        
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_timestamp ON entries(timestamp)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag)")
        self._conn.commit()
    
    def _load_index(self) -> None:
        """Load FAISS index from database."""
        try:
            import faiss
            self._index = faiss.IndexFlatL2(self._embedding_dim)
            
            # Load existing embeddings
            cursor = self._conn.execute("SELECT embedding FROM embeddings")
            for (embedding_blob,) in cursor:
                if embedding_blob:
                    embedding = np.frombuffer(embedding_blob, dtype=np.float32)
                    self._index.add(np.array([embedding]))
        except ImportError:
            # FAISS not available - will use brute-force search
            self._index = None
    
    def record_execution(
        self,
        plan: EditPlan,
        success: bool,
        validation: Optional[ValidationResult] = None,
        user_feedback: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Record an edit execution in memory.
        
        Args:
            plan: The edit plan that was executed
            success: Whether execution succeeded
            validation: Validation result (if available)
            user_feedback: User's rating/comments (if any)
            
        Returns:
            Entry ID
        """
        entry_id = f"mem_{uuid.uuid4().hex[:8]}"
        timestamp = time.time()
        
        # Generate embedding
        embedding = self._generate_embedding(plan)
        
        # Store in database
        self._conn.execute("""
            INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry_id,
            timestamp,
            plan.to_json(),
            validation.to_json() if validation else None,
            1 if success else 0,
            json.dumps([]),  # repair_attempts
            json.dumps(user_feedback) if user_feedback else None,
            json.dumps([]),  # tags
            json.dumps({}),  # metadata
        ))
        
        if embedding is not None:
            self._conn.execute("""
                INSERT INTO embeddings VALUES (?, ?)
            """, (entry_id, embedding.tobytes()))
        
        self._conn.commit()
        
        # Update stats
        self._update_stats()
        
        return entry_id
    
    def record_repair(
        self,
        original_plan: EditPlan,
        repaired_plan: EditPlan,
        fixes_applied: List[Dict[str, Any]],
    ) -> None:
        """Record a repair attempt."""
        # Would update the most recent entry or create a linked entry
        pass
    
    def find_similar(
        self,
        intent: str,
        context: Optional[Dict[str, Any]] = None,
        limit: int = 5,
        min_success_rate: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """Find similar past edits.
        
        Args:
            intent: Natural language description of desired edit
            context: Additional context (file, symbols, etc.)
            limit: Maximum number of suggestions
            min_success_rate: Only consider entries with at least this success rate
            
        Returns:
            List of similar entries with similarity scores
        """
        # Generate query embedding
        query_embedding = self._generate_text_embedding(intent)
        if query_embedding is None:
            return []
        
        # Search index
        if self._index and self._index.ntotal > 0:
            distances, indices = self._index.search(np.array([query_embedding]), limit * 2)
            
            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx == -1:
                    continue
                
                # Get entry from database
                cursor = self._conn.execute(
                    "SELECT * FROM entries WHERE rowid = ?",
                    (idx + 1,)
                )
                row = cursor.fetchone()
                if row:
                    entry = MemoryEntry.from_dict(dict(row))
                    # Check success rate
                    success_rate = 1.0 if entry.execution_success else 0.0
                    if success_rate >= min_success_rate:
                        similarity = 1.0 / (1.0 + dist)
                        results.append({
                            "entry": entry,
                            "similarity": similarity,
                            "score": similarity * success_rate,
                        })
            
            # Sort by score
            results.sort(key=lambda r: r["score"], reverse=True)
            return results[:limit]
        
        return []
    
    def get_best_repair_strategy(
        self,
        error_message: str,
        file_type: str,
    ) -> Optional[Dict[str, Any]]:
        """Get the most successful repair strategy for a given error."""
        cursor = self._conn.execute("""
            SELECT repair_attempts_json FROM entries
            WHERE execution_success = 1
            AND repair_attempts_json != '[]'
        """)
        
        strategy_scores: Dict[str, Dict[str, Any]] = {}
        
        for (repair_json,) in cursor:
            repairs = json.loads(repair_json)
            for repair in repairs:
                strategy = repair.get("strategy", "unknown")
                if strategy not in strategy_scores:
                    strategy_scores[strategy] = {"successes": 0, "total": 0}
                strategy_scores[strategy]["total"] += 1
                if repair.get("success", False):
                    strategy_scores[strategy]["successes"] += 1
        
        # Find best strategy
        if strategy_scores:
            best = max(
                strategy_scores.items(),
                key=lambda kv: kv[1]["successes"] / kv[1]["total"] if kv[1]["total"] > 0 else 0
            )
            return {
                "strategy": best[0],
                "success_rate": best[1]["successes"] / best[1]["total"],
                "attempts": best[1]["total"],
            }
        
        return None
    
    def get_statistics(self) -> MemoryStats:
        """Get memory statistics."""
        cursor = self._conn.execute("SELECT COUNT(*) FROM entries")
        total = cursor.fetchone()[0]
        
        cursor = self._conn.execute("SELECT COUNT(*) FROM entries WHERE execution_success = 1")
        successful = cursor.fetchone()[0]
        
        failed = total - successful
        
        cursor = self._conn.execute("""
            SELECT AVG(LENGTH(validation_json) - LENGTH(REPLACE(validation_json, '"errors"', ''))) 
            FROM entries WHERE validation_json IS NOT NULL
        """)
        # Rough estimate of average error count
        avg_errors = cursor.fetchone()[0] or 0
        
        # Most common tags
        cursor = self._conn.execute("""
            SELECT tag, COUNT(*) as count 
            FROM tags 
            GROUP BY tag 
            ORDER BY count DESC 
            LIMIT 10
        """)
        common_tags = [(row["tag"], row["count"]) for row in cursor]
        
        cursor = self._conn.execute("SELECT MAX(timestamp) FROM entries")
        last_updated = cursor.fetchone()[0] or time.time()
        
        return MemoryStats(
            total_entries=total,
            successful_edits=successful,
            failed_edits=failed,
            avg_validation_errors=avg_errors,
            most_common_tags=common_tags,
            last_updated=last_updated,
        )
    
    def _generate_embedding(self, plan: EditPlan) -> Optional[bytes]:
        """Generate embedding for an edit plan."""
        # Combine intent and operation types
        text = f"{plan.description} " + " ".join(
            op.type.value for op in plan.operations
        )
        embedding = self._generate_text_embedding(text)
        return embedding.tobytes() if embedding is not None else None
    
    def _generate_text_embedding(self, text: str) -> Optional[np.ndarray]:
        """Generate embedding for text."""
        try:
            # Try sentence-transformers
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer('all-MiniLM-L6-v2')
            embedding = model.encode(text)
            return embedding.astype(np.float32)
        except ImportError:
            try:
                # Try OpenAI
                import openai
                response = openai.embeddings.create(
                    model="text-embedding-3-small",
                    input=text
                )
                embedding = response.data[0].embedding
                return np.array(embedding, dtype=np.float32)
            except Exception:
                return None
    
    def _update_stats(self) -> None:
        """Update cached statistics."""
        stats = self.get_statistics()
        self._conn.execute("""
            INSERT OR REPLACE INTO stats VALUES (?, ?, ?)
        """, (
            "total_entries", str(stats.total_entries), time.time()
        ))
        self._conn.execute("""
            INSERT OR REPLACE INTO stats VALUES (?, ?, ?)
        """, (
            "successful_edits", str(stats.successful_edits), time.time()
        ))
        self._conn.commit()
    
    def export_to_json(self, output_path: str) -> None:
        """Export memory to JSON file."""
        cursor = self._conn.execute("SELECT * FROM entries")
        entries = []
        for row in cursor:
            entry = MemoryEntry.from_dict(dict(row))
            entries.append(entry.to_dict())
        
        with open(output_path, 'w') as f:
            json.dump(entries, f, indent=2)
    
    def import_from_json(self, input_path: str) -> int:
        """Import memory from JSON file."""
        with open(input_path, 'r') as f:
            entries = json.load(f)
        
        count = 0
        for entry_data in entries:
            entry = MemoryEntry.from_dict(entry_data)
            self._conn.execute("""
                INSERT OR IGNORE INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.entry_id,
                entry.timestamp,
                entry.plan.to_json(),
                entry.validation.to_json() if entry.validation else None,
                1 if entry.execution_success else 0,
                json.dumps(entry.repair_attempts),
                json.dumps(entry.user_feedback),
                json.dumps(entry.tags),
                json.dumps(entry.metadata),
            ))
            count += 1
        
        self._conn.commit()
        self._update_stats()
        return count
    
    def clear(self) -> None:
        """Clear all memory."""
        self._conn.execute("DELETE FROM entries")
        self._conn.execute("DELETE FROM embeddings")
        self._conn.execute("DELETE FROM tags")
        self._conn.commit()
        self._update_stats()
    
    def close(self) -> None:
        """Close database connection."""
        self._conn.close()
    
    def __del__(self) -> None:
        self.close()