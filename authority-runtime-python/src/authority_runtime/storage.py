"""
Envelope Persistence - SQLite storage for envelopes and audit trail

This provides:
- Envelope storage and retrieval
- Audit trail persistence with SHA-256 hash chain
- Query by agent_id, policy_id, time range
- Decision chain reconstruction
- Versioned schema migrations
"""

import hashlib
import logging
import shutil
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Generator
from pathlib import Path

from .types import AuthorityEnvelope, DecisionContext
from .enforce import AuditEntry

logger = logging.getLogger(__name__)

# Maximum depth for envelope chain traversal to prevent infinite loops
MAX_CHAIN_DEPTH = 100


# =============================================================================
# Schema Migrations
# =============================================================================

def _migrate_001_add_resource_column(cursor: sqlite3.Cursor):
    """Add resource column to audit_trail (from pre-0.2.0 databases)."""
    cursor.execute("PRAGMA table_info(audit_trail)")
    columns = {row[1] for row in cursor.fetchall()}
    if "resource" not in columns:
        cursor.execute("ALTER TABLE audit_trail ADD COLUMN resource TEXT")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_trail(resource)")


def _migrate_002_add_hash_chain(cursor: sqlite3.Cursor):
    """Add entry_hash and prev_hash columns for tamper-evident audit trail."""
    cursor.execute("PRAGMA table_info(audit_trail)")
    columns = {row[1] for row in cursor.fetchall()}
    if "entry_hash" not in columns:
        cursor.execute("ALTER TABLE audit_trail ADD COLUMN entry_hash TEXT")
        cursor.execute("ALTER TABLE audit_trail ADD COLUMN prev_hash TEXT")
        # Backfill existing rows
        _backfill_hash_chain(cursor)


def _backfill_hash_chain(cursor: sqlite3.Cursor):
    """Compute and store hashes for existing audit entries that predate the hash chain."""
    cursor.execute("""
        SELECT id, timestamp, action, envelope_id, agent_id, root_policy_id,
               result, error, signature_valid, metadata, resource
        FROM audit_trail ORDER BY id ASC
    """)
    rows = cursor.fetchall()
    prev_hash = None
    for row in rows:
        canonical = json.dumps({
            "id": row[0],
            "timestamp": row[1],
            "action": row[2],
            "envelope_id": row[3],
            "agent_id": row[4],
            "root_policy_id": row[5],
            "result": row[6],
            "error": row[7],
            "signature_valid": row[8],
            "metadata": row[9],
            "resource": row[10],
            "prev_hash": prev_hash,
        }, sort_keys=True)
        entry_hash = hashlib.sha256(canonical.encode()).hexdigest()
        cursor.execute(
            "UPDATE audit_trail SET entry_hash = ?, prev_hash = ? WHERE id = ?",
            (entry_hash, prev_hash, row[0])
        )
        prev_hash = entry_hash


# Ordered list of migrations. Each is (version, description, function).
MIGRATIONS = [
    (1, "add_resource_column", _migrate_001_add_resource_column),
    (2, "add_hash_chain_columns", _migrate_002_add_hash_chain),
]


class EnvelopeStore:
    """
    SQLite-backed storage for envelopes and audit trail.

    Features:
    - WAL mode for crash safety
    - Versioned schema migrations with automatic backup
    - SHA-256 hash chain on audit entries for tamper detection

    Example:
        ```python
        store = EnvelopeStore("./authority.db")

        # Save envelope
        store.save_envelope(envelope)

        # Query envelopes
        envelopes = store.get_envelopes_by_agent("agent-001")

        # Save audit entry
        store.save_audit_entry(entry)

        # Verify audit integrity
        result = store.verify_audit_chain()
        assert result["valid"]
        ```
    """

    def __init__(self, db_path: str = "./authority_runtime.db"):
        """
        Initialize envelope store.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager for database connections.

        Ensures connections are always properly closed, even on exceptions.
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Create tables if they don't exist and run pending migrations."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Envelopes table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS envelopes (
                    envelope_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    step_number INTEGER NOT NULL,
                    parent_envelope_id TEXT,
                    root_policy_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    scopes TEXT NOT NULL,
                    resources TEXT NOT NULL,
                    constraints TEXT NOT NULL,
                    decision_context TEXT,
                    signature TEXT NOT NULL,
                    envelope_json TEXT NOT NULL
                )
            """)

            # Create indexes for envelopes table
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_id ON envelopes(agent_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_root_policy_id ON envelopes(root_policy_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON envelopes(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_parent_envelope_id ON envelopes(parent_envelope_id)")

            # Audit trail table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_trail (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    envelope_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    root_policy_id TEXT NOT NULL,
                    result TEXT NOT NULL,
                    error TEXT,
                    signature_valid INTEGER NOT NULL,
                    metadata TEXT,
                    decision_context TEXT,
                    resource TEXT,
                    entry_hash TEXT,
                    prev_hash TEXT,
                    FOREIGN KEY (envelope_id) REFERENCES envelopes(envelope_id)
                )
            """)

            # Create indexes for audit_trail table
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_trail(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_trail(action)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_envelope_id ON audit_trail(envelope_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_agent_id ON audit_trail(agent_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_root_policy_id ON audit_trail(root_policy_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_result ON audit_trail(result)")
            # resource index: created by migration 1 for pre-existing DBs; for fresh
            # DBs the column exists so we can create it here. Use try/except for safety.
            try:
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_trail(resource)")
            except sqlite3.OperationalError:
                pass  # Column not yet added — migration 1 will handle it

            # Schema version tracking
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                )
            """)

            conn.commit()

            # Run pending migrations
            self._run_migrations(conn)

    def _backup_db(self):
        """Copy DB file to .bak before running migrations."""
        db_file = Path(self.db_path)
        if db_file.exists() and db_file.stat().st_size > 0:
            backup_path = str(db_file) + ".bak"
            shutil.copy2(str(db_file), backup_path)
            logger.info("Database backed up to %s", backup_path)

    def _run_migrations(self, conn: sqlite3.Connection):
        """Run all pending schema migrations in order."""
        cursor = conn.cursor()

        # Get current schema version
        cursor.execute("SELECT MAX(version) FROM schema_versions")
        row = cursor.fetchone()
        current_version = row[0] if row[0] is not None else 0

        # Find pending migrations
        pending = [(v, d, fn) for v, d, fn in MIGRATIONS if v > current_version]
        if not pending:
            return

        # Backup before migrating if there's existing data to protect
        cursor.execute("SELECT COUNT(*) FROM audit_trail")
        has_data = cursor.fetchone()[0] > 0
        if has_data:
            self._backup_db()

        for version, description, migrate_fn in pending:
            logger.info("Running migration %d: %s", version, description)
            migrate_fn(cursor)
            cursor.execute(
                "INSERT INTO schema_versions (version, applied_at, description) VALUES (?, ?, ?)",
                (version, datetime.now(timezone.utc).isoformat(), description)
            )
            conn.commit()
            logger.info("Migration %d complete", version)

    def get_schema_version(self) -> int:
        """Get current schema version."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(version) FROM schema_versions")
            row = cursor.fetchone()
            return row[0] if row[0] is not None else 0

    def get_migration_history(self) -> List[Dict[str, Any]]:
        """Get list of applied migrations."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT version, applied_at, description FROM schema_versions ORDER BY version")
            return [
                {"version": row[0], "applied_at": row[1], "description": row[2]}
                for row in cursor.fetchall()
            ]

    def save_envelope(self, envelope: AuthorityEnvelope) -> None:
        """
        Save an envelope to the database.

        Args:
            envelope: The envelope to save
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Serialize decision context if present
            decision_context_json = None
            if envelope.decision_context:
                decision_context_json = json.dumps({
                    "intent": envelope.decision_context.intent,
                    "inputs": envelope.decision_context.inputs,
                    "constraints_applied": envelope.decision_context.constraints_applied,
                    "alternatives_considered": envelope.decision_context.alternatives_considered,
                    "selected_because": envelope.decision_context.selected_because,
                    "policy_references": envelope.decision_context.policy_references,
                    "confidence": envelope.decision_context.confidence,
                    "escalation_reason": envelope.decision_context.escalation_reason,
                    "risk_factors": envelope.decision_context.risk_factors,
                })

            cursor.execute("""
                INSERT OR REPLACE INTO envelopes (
                    envelope_id, agent_id, provider, step_number, parent_envelope_id,
                    root_policy_id, created_at, expires_at, ttl_seconds,
                    scopes, resources, constraints, decision_context,
                    signature, envelope_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                envelope.envelope_id,
                envelope.agent_id,
                envelope.provider,
                envelope.step_number,
                envelope.parent_envelope_id,
                envelope.root_policy_id,
                envelope.created_at,
                envelope.expires_at,
                envelope.ttl_seconds,
                json.dumps(envelope.authority.scopes),
                json.dumps(envelope.authority.resources),
                json.dumps(envelope.authority.constraints),
                decision_context_json,
                envelope.signature,
                envelope.model_dump_json(),
            ))

            conn.commit()

    def get_envelope(self, envelope_id: str) -> Optional[AuthorityEnvelope]:
        """
        Retrieve an envelope by ID.

        Args:
            envelope_id: The envelope ID to retrieve

        Returns:
            The envelope if found, None otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT envelope_json FROM envelopes WHERE envelope_id = ?
            """, (envelope_id,))

            row = cursor.fetchone()

            if row:
                return AuthorityEnvelope.model_validate_json(row[0])
            return None

    def get_envelopes_by_agent(
        self,
        agent_id: str,
        limit: int = 100
    ) -> List[AuthorityEnvelope]:
        """
        Get all envelopes for a specific agent.

        Args:
            agent_id: The agent ID to query
            limit: Maximum number of envelopes to return

        Returns:
            List of envelopes for this agent
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT envelope_json FROM envelopes
                WHERE agent_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (agent_id, limit))

            envelopes = [
                AuthorityEnvelope.model_validate_json(row[0])
                for row in cursor.fetchall()
            ]

            return envelopes

    def get_envelope_chain(self, envelope_id: str) -> List[AuthorityEnvelope]:
        """
        Get the full delegation chain for an envelope (child -> parent -> grandparent -> ...).

        Args:
            envelope_id: The envelope ID to start from

        Returns:
            List of envelopes in the chain, from child to root

        Raises:
            ValueError: If circular reference detected or chain exceeds max depth
        """
        chain = []
        current_id = envelope_id
        seen_ids = set()

        while current_id:
            # Check for circular reference
            if current_id in seen_ids:
                raise ValueError(
                    f"Circular reference detected in envelope chain at {current_id}. "
                    f"Chain so far: {[e.envelope_id for e in chain]}"
                )

            # Check for max depth (prevents infinite loops from corrupted data)
            if len(chain) >= MAX_CHAIN_DEPTH:
                raise ValueError(
                    f"Envelope chain exceeded maximum depth of {MAX_CHAIN_DEPTH}. "
                    f"Possible circular reference or corrupted data."
                )

            seen_ids.add(current_id)
            envelope = self.get_envelope(current_id)

            if not envelope:
                break

            chain.append(envelope)
            current_id = envelope.parent_envelope_id

        return chain

    @staticmethod
    def _compute_entry_hash(
        row_id: int,
        timestamp: str,
        action: str,
        envelope_id: str,
        agent_id: str,
        root_policy_id: str,
        result: str,
        error: Optional[str],
        signature_valid: int,
        metadata: Optional[str],
        resource: Optional[str],
        prev_hash: Optional[str],
    ) -> str:
        """Compute SHA-256 hash of an audit entry for the hash chain."""
        canonical = json.dumps({
            "id": row_id,
            "timestamp": timestamp,
            "action": action,
            "envelope_id": envelope_id,
            "agent_id": agent_id,
            "root_policy_id": root_policy_id,
            "result": result,
            "error": error,
            "signature_valid": signature_valid,
            "metadata": metadata,
            "resource": resource,
            "prev_hash": prev_hash,
        }, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def save_audit_entry(self, entry: AuditEntry) -> int:
        """
        Save an audit entry to the database with hash chain linking.

        Uses an explicit transaction to ensure atomicity of the insert
        and hash computation.

        Args:
            entry: The audit entry to save

        Returns:
            The row ID of the inserted entry
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            try:
                cursor.execute("BEGIN IMMEDIATE")

                # Serialize decision context if present
                decision_context_json = None
                if entry.envelope.decision_context:
                    decision_context_json = json.dumps({
                        "intent": entry.envelope.decision_context.intent,
                        "inputs": entry.envelope.decision_context.inputs,
                        "constraints_applied": entry.envelope.decision_context.constraints_applied,
                        "alternatives_considered": entry.envelope.decision_context.alternatives_considered,
                        "selected_because": entry.envelope.decision_context.selected_because,
                        "policy_references": entry.envelope.decision_context.policy_references,
                        "confidence": entry.envelope.decision_context.confidence,
                        "escalation_reason": entry.envelope.decision_context.escalation_reason,
                        "risk_factors": entry.envelope.decision_context.risk_factors,
                    })

                metadata_json = json.dumps(entry.metadata)

                # Insert the row first to get the auto-increment ID
                cursor.execute("""
                    INSERT INTO audit_trail (
                        timestamp, action, envelope_id, agent_id, root_policy_id,
                        result, error, signature_valid, metadata, decision_context,
                        resource
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    entry.timestamp,
                    entry.action,
                    entry.envelope.envelope_id,
                    entry.envelope.agent_id,
                    entry.envelope.root_policy_id,
                    entry.result,
                    entry.error,
                    1 if entry.signature_valid else 0,
                    metadata_json,
                    decision_context_json,
                    entry.resource,
                ))

                row_id = cursor.lastrowid

                # Get the previous entry's hash
                cursor.execute(
                    "SELECT entry_hash FROM audit_trail WHERE id < ? ORDER BY id DESC LIMIT 1",
                    (row_id,)
                )
                prev_row = cursor.fetchone()
                prev_hash = prev_row[0] if prev_row else None

                # Compute this entry's hash
                entry_hash = self._compute_entry_hash(
                    row_id=row_id,
                    timestamp=entry.timestamp,
                    action=entry.action,
                    envelope_id=entry.envelope.envelope_id,
                    agent_id=entry.envelope.agent_id,
                    root_policy_id=entry.envelope.root_policy_id,
                    result=entry.result,
                    error=entry.error,
                    signature_valid=1 if entry.signature_valid else 0,
                    metadata=metadata_json,
                    resource=entry.resource,
                    prev_hash=prev_hash,
                )

                # Update the row with hash chain data
                cursor.execute(
                    "UPDATE audit_trail SET entry_hash = ?, prev_hash = ? WHERE id = ?",
                    (entry_hash, prev_hash, row_id)
                )

                conn.commit()
                return row_id

            except Exception:
                conn.rollback()
                raise

    def get_audit_trail(
        self,
        agent_id: Optional[str] = None,
        root_policy_id: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        result: Optional[str] = None,
        resource_pattern: Optional[str] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Query the audit trail with filters.

        Args:
            agent_id: Filter by agent ID
            root_policy_id: Filter by root policy ID
            start_time: Filter by start timestamp (ISO 8601)
            end_time: Filter by end timestamp (ISO 8601)
            result: Filter by result ('success', 'blocked', 'error')
            resource_pattern: Filter by resource URI pattern (SQL LIKE, use % as wildcard)
            limit: Maximum number of entries to return

        Returns:
            List of audit entries as dictionaries
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM audit_trail WHERE 1=1"
            params = []

            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)

            if root_policy_id:
                query += " AND root_policy_id = ?"
                params.append(root_policy_id)

            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time)

            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time)

            if result:
                query += " AND result = ?"
                params.append(result)

            if resource_pattern:
                query += " AND resource LIKE ?"
                params.append(resource_pattern)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, tuple(params))

            entries = []
            for row in cursor.fetchall():
                entry = dict(row)
                # Parse JSON fields
                entry["metadata"] = json.loads(entry["metadata"]) if entry["metadata"] else {}
                entry["decision_context"] = json.loads(entry["decision_context"]) if entry["decision_context"] else None
                entry["signature_valid"] = bool(entry["signature_valid"])
                entries.append(entry)

            return entries

    # =========================================================================
    # Compliance Query Methods
    # =========================================================================

    def count_access_events(
        self,
        agent_id: Optional[str] = None,
        resource_pattern: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> int:
        """
        Count access events matching filters. Used for compliance attestations.

        A count of 0 with agent_id + resource_pattern proves that agent
        never accessed resources matching that pattern (negative attestation).
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT COUNT(*) FROM audit_trail WHERE 1=1"
            params = []

            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            if resource_pattern:
                query += " AND resource LIKE ?"
                params.append(resource_pattern)
            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time)
            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time)

            cursor.execute(query, tuple(params))
            return cursor.fetchone()[0]

    def get_distinct_agents_for_resource(
        self,
        resource_pattern: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all agents that accessed resources matching a pattern."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT agent_id, COUNT(*) as access_count,
                       MIN(timestamp) as first_access, MAX(timestamp) as last_access
                FROM audit_trail
                WHERE resource LIKE ?
            """
            params: list = [resource_pattern]

            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time)
            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time)

            query += " GROUP BY agent_id ORDER BY access_count DESC"
            cursor.execute(query, tuple(params))

            return [
                {
                    "agent_id": row[0],
                    "access_count": row[1],
                    "first_access": row[2],
                    "last_access": row[3],
                }
                for row in cursor.fetchall()
            ]

    def get_distinct_resources_for_agent(
        self,
        agent_id: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all resources an agent has accessed."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT resource, COUNT(*) as access_count,
                       MIN(timestamp) as first_access, MAX(timestamp) as last_access
                FROM audit_trail
                WHERE agent_id = ? AND resource IS NOT NULL
            """
            params: list = [agent_id]

            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time)
            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time)

            query += " GROUP BY resource ORDER BY access_count DESC"
            cursor.execute(query, tuple(params))

            return [
                {
                    "resource": row[0],
                    "access_count": row[1],
                    "first_access": row[2],
                    "last_access": row[3],
                }
                for row in cursor.fetchall()
            ]

    # =========================================================================
    # Hash Chain Verification
    # =========================================================================

    def verify_audit_chain(
        self,
        start_id: Optional[int] = None,
        end_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Verify the hash chain integrity of the audit trail.

        Reads entries in ID order, recomputes each hash, and checks it
        matches the stored hash. Also detects gaps in sequential IDs
        (indicating deleted rows).

        Returns:
            {
                "valid": bool,
                "entries_checked": int,
                "first_invalid_id": int or None,
                "error": str or None,
                "gaps": list of (expected_id, actual_id) tuples,
            }
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = """
                SELECT id, timestamp, action, envelope_id, agent_id, root_policy_id,
                       result, error, signature_valid, metadata, resource,
                       entry_hash, prev_hash
                FROM audit_trail
            """
            params: list = []
            conditions = []
            if start_id is not None:
                conditions.append("id >= ?")
                params.append(start_id)
            if end_id is not None:
                conditions.append("id <= ?")
                params.append(end_id)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY id ASC"

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

            if not rows:
                return {
                    "valid": True,
                    "entries_checked": 0,
                    "first_invalid_id": None,
                    "error": None,
                    "gaps": [],
                }

            gaps = []
            prev_expected_hash = None
            prev_id = None
            entries_checked = 0

            for row in rows:
                (row_id, timestamp, action, envelope_id, agent_id, root_policy_id,
                 result, error, sig_valid, metadata, resource,
                 stored_hash, stored_prev_hash) = row

                entries_checked += 1

                # Check for gaps (deleted rows)
                if prev_id is not None and row_id != prev_id + 1:
                    gaps.append((prev_id + 1, row_id))

                # Verify prev_hash links correctly
                if stored_prev_hash != prev_expected_hash:
                    return {
                        "valid": False,
                        "entries_checked": entries_checked,
                        "first_invalid_id": row_id,
                        "error": f"prev_hash mismatch at id={row_id}: "
                                 f"stored={stored_prev_hash}, expected={prev_expected_hash}",
                        "gaps": gaps,
                    }

                # Recompute hash and compare
                computed_hash = self._compute_entry_hash(
                    row_id=row_id,
                    timestamp=timestamp,
                    action=action,
                    envelope_id=envelope_id,
                    agent_id=agent_id,
                    root_policy_id=root_policy_id,
                    result=result,
                    error=error,
                    signature_valid=sig_valid,
                    metadata=metadata,
                    resource=resource,
                    prev_hash=stored_prev_hash,
                )

                if computed_hash != stored_hash:
                    return {
                        "valid": False,
                        "entries_checked": entries_checked,
                        "first_invalid_id": row_id,
                        "error": f"entry_hash mismatch at id={row_id}: "
                                 f"stored={stored_hash}, computed={computed_hash}",
                        "gaps": gaps,
                    }

                prev_expected_hash = stored_hash
                prev_id = row_id

            return {
                "valid": True if not gaps else True,  # gaps are warnings, not invalidity
                "entries_checked": entries_checked,
                "first_invalid_id": None,
                "error": None,
                "gaps": gaps,
            }

    # =========================================================================
    # Archive
    # =========================================================================

    def archive_audit_entries(self, older_than_days: int) -> Dict[str, Any]:
        """
        Move audit entries older than N days to a separate archive table.

        The archive preserves all columns including hash chain data. Gaps
        created in the main table's ID sequence will be detected by
        verify_audit_chain as warnings.

        Returns:
            {"archived_count": int, "cutoff_date": str}
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")

                # Create archive table with same schema if not exists
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS audit_trail_archive (
                        id INTEGER PRIMARY KEY,
                        timestamp TEXT NOT NULL,
                        action TEXT NOT NULL,
                        envelope_id TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        root_policy_id TEXT NOT NULL,
                        result TEXT NOT NULL,
                        error TEXT,
                        signature_valid INTEGER NOT NULL,
                        metadata TEXT,
                        decision_context TEXT,
                        resource TEXT,
                        entry_hash TEXT,
                        prev_hash TEXT
                    )
                """)

                # Move old rows
                cursor.execute(
                    "INSERT INTO audit_trail_archive SELECT * FROM audit_trail WHERE timestamp < ?",
                    (cutoff,)
                )
                count = cursor.rowcount
                cursor.execute("DELETE FROM audit_trail WHERE timestamp < ?", (cutoff,))
                conn.commit()
                logger.info("Archived %d audit entries older than %s", count, cutoff)
                return {"archived_count": count, "cutoff_date": cutoff}
            except Exception:
                conn.rollback()
                raise

    def get_stats(self) -> Dict[str, Any]:
        """
        Get summary statistics about stored data.

        Returns:
            Dictionary with stats about envelopes and audit entries
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Envelope stats
            cursor.execute("SELECT COUNT(*) FROM envelopes")
            total_envelopes = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT agent_id) FROM envelopes")
            unique_agents = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT root_policy_id) FROM envelopes")
            unique_policies = cursor.fetchone()[0]

            # Audit trail stats
            cursor.execute("SELECT COUNT(*) FROM audit_trail")
            total_actions = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM audit_trail WHERE result = 'success'")
            successful_actions = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM audit_trail WHERE result = 'blocked'")
            blocked_actions = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM audit_trail WHERE signature_valid = 0")
            signature_failures = cursor.fetchone()[0]

            return {
                "envelopes": {
                    "total": total_envelopes,
                    "unique_agents": unique_agents,
                    "unique_policies": unique_policies,
                },
                "audit_trail": {
                    "total_actions": total_actions,
                    "successful": successful_actions,
                    "blocked": blocked_actions,
                    "signature_failures": signature_failures,
                }
            }
