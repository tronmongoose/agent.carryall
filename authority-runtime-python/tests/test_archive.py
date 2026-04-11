"""
Tests for audit archive, export, and version alignment (D4).
"""

import os
import sqlite3
import tempfile
import pytest
from datetime import datetime, timezone, timedelta

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
def populated_store(db_path):
    """Store with entries spanning old and new dates."""
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

    # Insert old entries (2 years ago) by manipulating timestamps directly
    old_ts = (datetime.now(timezone.utc) - timedelta(days=730)).isoformat()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    for i in range(3):
        cursor.execute(
            "INSERT INTO audit_trail (timestamp, action, envelope_id, agent_id, root_policy_id, result, signature_valid, metadata, resource) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (old_ts, "read", envelope.envelope_id, "test-agent", "test-policy", "success", 1, "{}", f"slos://vaults/test/old{i}")
        )
    conn.commit()
    conn.close()

    # Insert recent entries normally
    for i in range(2):
        entry = create_audit_entry(
            action="read", envelope=envelope, public_key=pub,
            result="success", resource=f"slos://vaults/test/new{i}",
        )
        store.save_audit_entry(entry)

    return store, db_path


class TestArchive:
    def test_archive_moves_old_entries(self, populated_store):
        store, db_path = populated_store

        # Archive entries older than 365 days
        result = store.archive_audit_entries(older_than_days=365)
        assert result["archived_count"] == 3

        # Verify archive table has the entries
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM audit_trail_archive")
        assert cursor.fetchone()[0] == 3
        conn.close()

    def test_archive_preserves_recent(self, populated_store):
        store, db_path = populated_store

        store.archive_audit_entries(older_than_days=365)

        # Main table should still have recent entries
        entries = store.get_audit_trail()
        assert len(entries) == 2
        for e in entries:
            assert "new" in e["resource"]

    def test_archive_returns_zero_when_nothing_old(self, db_path):
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
        store.save_audit_entry(entry)

        result = store.archive_audit_entries(older_than_days=365)
        assert result["archived_count"] == 0


class TestVersionConsistency:
    def test_version_in_init(self):
        from authority_runtime import __version__
        assert __version__ == "0.3.0"

    def test_version_in_pyproject(self):
        import tomllib
        pyproject_path = os.path.join(
            os.path.dirname(__file__), "..", "pyproject.toml"
        )
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        assert data["project"]["version"] == "0.3.0"

    @pytest.mark.asyncio
    async def test_version_in_mcp_initialize(self):
        from authority_runtime.mcp_server import CarryallMCPServer

        server = CarryallMCPServer()
        result = await server._handle_initialize({})
        assert result["serverInfo"]["version"] == "0.3.0"
