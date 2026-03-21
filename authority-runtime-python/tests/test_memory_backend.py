"""
Tests for MemoryBackend — standalone policy evaluation without SLOS.
"""

import pytest
from authority_runtime.backends.memory import MemoryBackend
from authority_runtime.backends.slos import Decision
from authority_runtime.envelope import create_simple_envelope, generate_key_pair


@pytest.fixture
def key_pair():
    return generate_key_pair()


@pytest.fixture
def sample_data():
    return {
        "student-records": {
            "enrollment-001": {
                "content": "# Jane Doe\nEnrolled in CS101, MATH201",
                "title": "Jane Doe - Enrollment",
                "sensitivity": "confidential",
                "allowed_agents": ["academic-advisor-agent", "registrar-agent"],
                "denied_agents": ["financial-aid-agent"],
                "requires_approval": ["executive-agent"],
            },
            "transcript-002": {
                "content": "# Jane Doe Transcript\nGPA: 3.7",
                "title": "Jane Doe - Transcript",
                "sensitivity": "confidential",
                "allowed_agents": ["academic-advisor-agent"],
            },
        },
        "financial-aid": {
            "aid-001": {
                "content": "# Jane Doe Financial Aid\nPell Grant: $6,895",
                "title": "Jane Doe - Financial Aid",
                "sensitivity": "confidential",
                "allowed_agents": ["financial-aid-agent"],
            },
        },
        "student-health": {
            "health-001": {
                "content": "# Jane Doe Health\nDisability accommodation: extended test time",
                "title": "Jane Doe - Health Record",
                "sensitivity": "restricted",
                "allowed_agents": ["health-agent"],
                "denied_agents": ["financial-aid-agent", "academic-advisor-agent"],
            },
        },
    }


@pytest.fixture
def backend(sample_data):
    return MemoryBackend(initial_data=sample_data)


class TestListVaults:
    def test_lists_all_vaults(self, backend):
        vaults = backend.list_vaults()
        assert set(vaults) == {"student-records", "financial-aid", "student-health"}

    def test_empty_backend(self):
        b = MemoryBackend()
        assert b.list_vaults() == []


class TestListResources:
    def test_lists_documents_in_vault(self, backend):
        resources = backend.list_resources("student-records", "test-agent")
        assert len(resources) == 2
        titles = {r["title"] for r in resources}
        assert "Jane Doe - Enrollment" in titles

    def test_empty_vault(self, backend):
        resources = backend.list_resources("nonexistent", "test-agent")
        assert resources == []


class TestGetMetadata:
    def test_returns_document_metadata(self, backend):
        meta = backend.get_metadata("slos://vaults/student-records/enrollment-001", "test-agent")
        assert meta.uri == "slos://vaults/student-records/enrollment-001"
        assert meta.id == "enrollment-001"
        assert meta.domain == ["student-records"]
        assert meta.sensitivity == "confidential"
        assert "academic-advisor-agent" in meta.allowed_agents
        assert "financial-aid-agent" in meta.denied_agents

    def test_missing_document_returns_empty_metadata(self, backend):
        meta = backend.get_metadata("slos://vaults/student-records/nonexistent", "test-agent")
        assert meta.sensitivity == "unknown"
        assert meta.allowed_agents == []


class TestCheckAccess:
    def test_explicit_allow(self, backend, key_pair):
        private_key, _ = key_pair
        envelope = create_simple_envelope(
            agent_id="academic-advisor-agent",
            scopes=["vault:student-records:read"],
            private_key=private_key,
        )
        result = backend.check_access(envelope, "read", "slos://vaults/student-records/enrollment-001")
        assert result.decision == Decision.ALLOW
        assert "explicitly allowed" in result.reason

    def test_explicit_deny(self, backend, key_pair):
        private_key, _ = key_pair
        envelope = create_simple_envelope(
            agent_id="financial-aid-agent",
            scopes=["vault:student-records:read"],
            private_key=private_key,
        )
        result = backend.check_access(envelope, "read", "slos://vaults/student-records/enrollment-001")
        assert result.decision == Decision.DENY
        assert "explicitly denied" in result.reason

    def test_requires_approval(self, backend, key_pair):
        private_key, _ = key_pair
        envelope = create_simple_envelope(
            agent_id="executive-agent",
            scopes=["vault:student-records:read"],
            private_key=private_key,
        )
        result = backend.check_access(envelope, "read", "slos://vaults/student-records/enrollment-001")
        assert result.decision == Decision.REQUIRE_APPROVAL

    def test_scope_based_allow(self, backend, key_pair):
        """Agent not in allowed_agents list but has correct scope — allowed."""
        private_key, _ = key_pair
        envelope = create_simple_envelope(
            agent_id="some-new-agent",
            scopes=["vault:financial-aid:read"],
            private_key=private_key,
        )
        result = backend.check_access(envelope, "read", "slos://vaults/financial-aid/aid-001")
        # aid-001 has allowed_agents=["financial-aid-agent"] so some-new-agent isn't explicit
        # But it has the scope, so it should be denied because allowed_agents is non-empty
        # and some-new-agent is not in it. Wait — let me check the logic.
        # Looking at SlosBackend.check_access: step 3 checks "if metadata.allowed_agents AND agent in allowed_agents"
        # If allowed_agents is non-empty and agent NOT in it, falls through to step 4 (scope check).
        # So scope-based allow works even if allowed_agents exists but doesn't include this agent.
        assert result.decision == Decision.ALLOW
        assert "scope" in result.reason

    def test_default_deny_no_scope(self, backend, key_pair):
        """Agent has wrong scope — denied."""
        private_key, _ = key_pair
        envelope = create_simple_envelope(
            agent_id="random-agent",
            scopes=["vault:other:read"],
            private_key=private_key,
        )
        result = backend.check_access(envelope, "read", "slos://vaults/student-records/transcript-002")
        assert result.decision == Decision.DENY
        assert "No permission" in result.reason

    def test_wildcard_scope(self, backend, key_pair):
        private_key, _ = key_pair
        envelope = create_simple_envelope(
            agent_id="admin-agent",
            scopes=["vault:student-records:*"],
            private_key=private_key,
        )
        result = backend.check_access(envelope, "read", "slos://vaults/student-records/transcript-002")
        assert result.decision == Decision.ALLOW
        assert "wildcard" in result.reason

    def test_deny_takes_precedence_over_scope(self, backend, key_pair):
        """Even with correct scope, explicit deny wins."""
        private_key, _ = key_pair
        envelope = create_simple_envelope(
            agent_id="financial-aid-agent",
            scopes=["vault:student-health:read"],
            private_key=private_key,
        )
        result = backend.check_access(envelope, "read", "slos://vaults/student-health/health-001")
        assert result.decision == Decision.DENY
        assert "explicitly denied" in result.reason


class TestReadDocument:
    def test_read_existing_document(self, backend):
        result = backend.read_document("enrollment-001", "advising session", "advisor-agent")
        assert result["content"] == "# Jane Doe\nEnrolled in CS101, MATH201"
        assert result["vault"] == "student-records"

    def test_read_missing_document(self, backend):
        result = backend.read_document("nonexistent", "test", "test-agent")
        assert "error" in result


class TestWriteDocument:
    def test_write_new_document(self, backend):
        result = backend.write_document(
            domain="student-records",
            content="# New Student\nEnrolled in BIO101",
            metadata={"title": "New Student Enrollment", "sensitivity": "confidential"},
            agent_id="registrar-agent",
        )
        assert result["status"] == "created"
        assert result["domain"] == "student-records"

        # Verify it's readable
        resources = backend.list_resources("student-records", "registrar-agent")
        titles = {r["title"] for r in resources}
        assert "New Student Enrollment" in titles

    def test_write_to_new_vault(self, backend):
        result = backend.write_document(
            domain="new-vault",
            content="# Test",
            metadata={"title": "Test Doc"},
            agent_id="test-agent",
        )
        assert "new-vault" in backend.list_vaults()


class TestQueryDocuments:
    def test_query_by_content(self, backend):
        result = backend.query_documents("student-records", "CS101", "test-agent")
        assert result["total"] >= 1
        assert any("Jane Doe" in r["title"] for r in result["results"])

    def test_query_with_content(self, backend):
        result = backend.query_documents(
            "student-records", "CS101", "test-agent", include_content=True
        )
        assert "content" in result["results"][0]

    def test_query_empty_domain(self, backend):
        result = backend.query_documents("nonexistent", "test", "test-agent")
        assert result["total"] == 0
