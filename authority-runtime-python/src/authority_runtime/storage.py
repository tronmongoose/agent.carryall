"""
Envelope Persistence - SQLite storage for envelopes and audit trail

This provides:
- Envelope storage and retrieval
- Audit trail persistence
- Query by agent_id, policy_id, time range
- Decision chain reconstruction
"""

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Generator
from pathlib import Path

from .types import AuthorityEnvelope, DecisionContext
from .enforce import AuditEntry


# Maximum depth for envelope chain traversal to prevent infinite loops
MAX_CHAIN_DEPTH = 100


class EnvelopeStore:
    """
    SQLite-backed storage for envelopes and audit trail.

    Example:
        ```python
        store = EnvelopeStore("./authority.db")

        # Save envelope
        store.save_envelope(envelope)

        # Query envelopes
        envelopes = store.get_envelopes_by_agent("agent-001")

        # Save audit entry
        store.save_audit_entry(entry)

        # Query audit trail
        trail = store.get_audit_trail(
            agent_id="agent-001",
            start_time="2026-01-01T00:00:00Z"
        )
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
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Create tables if they don't exist."""
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
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_trail(resource)")

            conn.commit()

            # Migration: add resource column to existing databases
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection):
        """Run schema migrations for existing databases."""
        cursor = conn.cursor()
        # Check if resource column exists
        cursor.execute("PRAGMA table_info(audit_trail)")
        columns = {row[1] for row in cursor.fetchall()}
        if "resource" not in columns:
            cursor.execute("ALTER TABLE audit_trail ADD COLUMN resource TEXT")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_trail(resource)")
            conn.commit()

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

    def save_audit_entry(self, entry: AuditEntry) -> None:
        """
        Save an audit entry to the database.

        Args:
            entry: The audit entry to save
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

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
                json.dumps(entry.metadata),
                decision_context_json,
                entry.resource,
            ))

            conn.commit()

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
