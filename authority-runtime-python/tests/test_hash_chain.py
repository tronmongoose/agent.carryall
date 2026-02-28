"""
Tests for audit trail hash chain integrity (D2).
"""

import os
import sqlite3
import tempfile
import pytest

from authority_runtime.storage import EnvelopeStore
from authority_runtime.envelope import create_envelope, generate_key_pair
from authority_runtime.enforce import create_audit_entry
from authority_runtime.types import (
    Skill, SkillParameters, Authority, Context, ExecutionConfig,
)


@pytest.fixture
def db_path():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = f.name
    f.close()
    os.unlink(path)
    yield path
    for p in [path, path + ".bak"]:
        if os.path.exists(p):
            os.unlink(p)


@pytest.fixture
def store_and_envelope(db_path):
    """Create a store with a saved envelope ready for audit entries."""
    store = EnvelopeStore(db_path)
    priv, pub = generate_key_pair()
    envelope = create_envelope(
        agent_id="test-agent",
        provider="custom",
        step_number=1,
        root_policy_id="test-policy",
        skill=Skill(id="s1", name="test", tool="t", parameters=SkillParameters(allowed=["read"], constraints={})),
        authority=Authority(scopes=["vault:test:read"], resources=["slos://vaults/test/*"]),
        context=Context(included=[], excluded=[]),
        execution=ExecutionConfig(provider_config={}),
        private_key=priv,
        ttl_seconds=3600,
    )
    store.save_envelope(envelope)
    return store, envelope, pub, db_path


class TestHashChainCreation:
    def test_save_audit_entry_creates_hash(self, store_and_envelope):
        store, envelope, pub, db_path = store_and_envelope
        entry = create_audit_entry(
            action="read", envelope=envelope, public_key=pub,
            result="success", resource="slos://vaults/test/doc1",
        )
        row_id = store.save_audit_entry(entry)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT entry_hash, prev_hash FROM audit_trail WHERE id = ?", (row_id,))
        row = cursor.fetchone()
        conn.close()

        assert row[0] is not None  # entry_hash set
        assert len(row[0]) == 64   # SHA-256 hex
        assert row[1] is None      # First entry has no prev_hash

    def test_first_entry_has_null_prev_hash(self, store_and_envelope):
        store, envelope, pub, db_path = store_and_envelope
        entry = create_audit_entry(
            action="read", envelope=envelope, public_key=pub,
            result="success", resource="slos://vaults/test/doc1",
        )
        store.save_audit_entry(entry)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT prev_hash FROM audit_trail ORDER BY id LIMIT 1")
        assert cursor.fetchone()[0] is None
        conn.close()

    def test_hash_chain_links(self, store_and_envelope):
        store, envelope, pub, db_path = store_and_envelope

        # Insert 3 entries
        for i in range(3):
            entry = create_audit_entry(
                action="read", envelope=envelope, public_key=pub,
                result="success", resource=f"slos://vaults/test/doc{i}",
            )
            store.save_audit_entry(entry)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, entry_hash, prev_hash FROM audit_trail ORDER BY id")
        rows = cursor.fetchall()
        conn.close()

        assert len(rows) == 3
        # First entry: prev_hash is None
        assert rows[0][2] is None
        # Second entry: prev_hash equals first entry's hash
        assert rows[1][2] == rows[0][1]
        # Third entry: prev_hash equals second entry's hash
        assert rows[2][2] == rows[1][1]


class TestHashChainVerification:
    def test_verify_empty_db(self, db_path):
        store = EnvelopeStore(db_path)
        result = store.verify_audit_chain()
        assert result["valid"] is True
        assert result["entries_checked"] == 0
        assert result["gaps"] == []

    def test_verify_chain_valid(self, store_and_envelope):
        store, envelope, pub, _ = store_and_envelope
        for i in range(5):
            entry = create_audit_entry(
                action="read", envelope=envelope, public_key=pub,
                result="success", resource=f"slos://vaults/test/doc{i}",
            )
            store.save_audit_entry(entry)

        result = store.verify_audit_chain()
        assert result["valid"] is True
        assert result["entries_checked"] == 5
        assert result["first_invalid_id"] is None
        assert result["gaps"] == []

    def test_verify_detects_tampered_hash(self, store_and_envelope):
        store, envelope, pub, db_path = store_and_envelope
        for i in range(3):
            entry = create_audit_entry(
                action="read", envelope=envelope, public_key=pub,
                result="success", resource=f"slos://vaults/test/doc{i}",
            )
            store.save_audit_entry(entry)

        # Tamper with the second entry's hash
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE audit_trail SET entry_hash = 'tampered' WHERE id = 2")
        conn.commit()
        conn.close()

        result = store.verify_audit_chain()
        assert result["valid"] is False
        assert result["first_invalid_id"] == 2
        assert "entry_hash mismatch" in result["error"]

    def test_verify_detects_deleted_row(self, store_and_envelope):
        store, envelope, pub, db_path = store_and_envelope
        for i in range(5):
            entry = create_audit_entry(
                action="read", envelope=envelope, public_key=pub,
                result="success", resource=f"slos://vaults/test/doc{i}",
            )
            store.save_audit_entry(entry)

        # Delete the middle entry
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM audit_trail WHERE id = 3")
        conn.commit()
        conn.close()

        result = store.verify_audit_chain()
        # Gaps are detected
        assert len(result["gaps"]) > 0
        # The chain is also broken because entry 4's prev_hash references entry 3
        assert result["valid"] is False

    def test_verify_detects_modified_data(self, store_and_envelope):
        store, envelope, pub, db_path = store_and_envelope
        entry = create_audit_entry(
            action="read", envelope=envelope, public_key=pub,
            result="success", resource="slos://vaults/test/doc1",
        )
        store.save_audit_entry(entry)

        # Modify the action text without updating the hash
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE audit_trail SET action = 'write' WHERE id = 1")
        conn.commit()
        conn.close()

        result = store.verify_audit_chain()
        assert result["valid"] is False
        assert result["first_invalid_id"] == 1
        assert "entry_hash mismatch" in result["error"]


class TestBackfill:
    def test_backfill_existing_rows(self, db_path):
        """Rows created before hash chain migration get backfilled correctly."""
        # Create a DB with rows that have no hash columns
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE envelopes (
                envelope_id TEXT PRIMARY KEY,
                agent_id TEXT, provider TEXT, step_number INTEGER,
                parent_envelope_id TEXT, root_policy_id TEXT,
                created_at TEXT, expires_at TEXT, ttl_seconds INTEGER,
                scopes TEXT, resources TEXT, constraints TEXT,
                decision_context TEXT, signature TEXT, envelope_json TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE audit_trail (
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
                resource TEXT
            )
        """)
        # Insert rows without hash columns
        for i in range(3):
            cursor.execute(
                "INSERT INTO audit_trail (timestamp, action, envelope_id, agent_id, root_policy_id, result, signature_valid, metadata, resource) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"2026-01-0{i+1}T00:00:00Z", "read", f"env-{i}", "agent-1", "pol-1", "success", 1, "{}", f"slos://vaults/test/doc{i}")
            )
        conn.commit()
        conn.close()

        # Open with EnvelopeStore — triggers migration + backfill
        store = EnvelopeStore(db_path)

        # Verify chain is valid after backfill
        result = store.verify_audit_chain()
        assert result["valid"] is True
        assert result["entries_checked"] == 3

        # Verify hashes are populated
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT entry_hash, prev_hash FROM audit_trail ORDER BY id")
        rows = cursor.fetchall()
        conn.close()

        assert rows[0][0] is not None  # First hash set
        assert rows[0][1] is None      # First prev_hash is None
        assert rows[1][1] == rows[0][0]  # Chain links
        assert rows[2][1] == rows[1][0]
