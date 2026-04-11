"""
Tests for schema migrations, WAL mode, and SQLite hardening (D1).
"""

import os
import sqlite3
import tempfile
import pytest

from authority_runtime.storage import EnvelopeStore, MIGRATIONS


@pytest.fixture
def db_path():
    """Create a temporary DB file for testing."""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = f.name
    f.close()
    os.unlink(path)  # Start fresh — EnvelopeStore creates the file
    yield path
    if os.path.exists(path):
        os.unlink(path)
    bak = path + ".bak"
    if os.path.exists(bak):
        os.unlink(bak)


class TestSchemaVersions:
    def test_fresh_db_creates_schema_versions_table(self, db_path):
        EnvelopeStore(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_versions'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_fresh_db_runs_all_migrations(self, db_path):
        store = EnvelopeStore(db_path)
        version = store.get_schema_version()
        assert version == MIGRATIONS[-1][0]

    def test_migration_history_recorded(self, db_path):
        store = EnvelopeStore(db_path)
        history = store.get_migration_history()
        assert len(history) == len(MIGRATIONS)
        for i, m in enumerate(history):
            assert m["version"] == MIGRATIONS[i][0]
            assert m["description"] == MIGRATIONS[i][1]
            assert m["applied_at"] is not None

    def test_migrations_are_idempotent(self, db_path):
        """Running migrations twice does not error."""
        store1 = EnvelopeStore(db_path)
        v1 = store1.get_schema_version()
        # Instantiate again — should not re-run migrations
        store2 = EnvelopeStore(db_path)
        v2 = store2.get_schema_version()
        assert v1 == v2

    def test_existing_db_without_schema_versions_migrates(self, db_path):
        """Simulate a pre-migration database and verify it upgrades."""
        # Create a bare DB with just the tables but no schema_versions
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE envelopes (
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
                decision_context TEXT
            )
        """)
        conn.commit()
        conn.close()

        # Now open with EnvelopeStore — should run all migrations
        store = EnvelopeStore(db_path)
        version = store.get_schema_version()
        assert version == MIGRATIONS[-1][0]

        # Verify columns were added
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(audit_trail)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "resource" in columns
        assert "entry_hash" in columns
        assert "prev_hash" in columns
        conn.close()


class TestWALMode:
    def test_wal_mode_enabled(self, db_path):
        EnvelopeStore(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_synchronous_full(self, db_path):
        EnvelopeStore(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA synchronous")
        # synchronous=FULL is value 2
        value = cursor.fetchone()[0]
        assert value == 2
        conn.close()


class TestBackup:
    def test_backup_created_before_migration(self, db_path):
        """Pre-migration DB gets backed up when migrations run."""
        # Create a bare DB (no schema_versions)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE envelopes (
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
                decision_context TEXT
            )
        """)
        # Insert a dummy row so the DB has content
        cursor.execute("INSERT INTO audit_trail (timestamp, action, envelope_id, agent_id, root_policy_id, result, signature_valid) VALUES ('2026-01-01', 'read', 'env1', 'agent1', 'pol1', 'success', 1)")
        conn.commit()
        conn.close()

        # Open with EnvelopeStore — triggers migration + backup
        EnvelopeStore(db_path)

        bak_path = db_path + ".bak"
        assert os.path.exists(bak_path)
        assert os.path.getsize(bak_path) > 0

    def test_no_backup_when_no_pending_migrations(self, db_path):
        """Fresh DB (all migrations applied) does not create a backup."""
        EnvelopeStore(db_path)
        bak_path = db_path + ".bak"
        assert not os.path.exists(bak_path)


class TestExplicitTransactions:
    def test_save_audit_entry_returns_row_id(self, db_path):
        from authority_runtime.envelope import create_envelope, generate_key_pair
        from authority_runtime.enforce import create_audit_entry
        from authority_runtime.types import Skill, SkillParameters, Authority, Context, ExecutionConfig

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

        entry = create_audit_entry(
            action="read", envelope=envelope, public_key=pub,
            result="success", resource="slos://vaults/test/doc1",
        )
        row_id = store.save_audit_entry(entry)
        assert isinstance(row_id, int)
        assert row_id > 0
