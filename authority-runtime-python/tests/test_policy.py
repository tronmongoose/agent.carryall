"""
Tests for YAML policy engine (policy.py).

Covers loading, validation, agent policies, data classifications,
envelope creation from policy, and error handling.
"""

import os
import tempfile
import pytest
from authority_runtime.policy import (
    PolicyEngine,
    PolicyValidationError,
)
from authority_runtime.envelope import generate_key_pair, verify_signature
from authority_runtime.enforce import check_envelope, ConstraintViolation


MINIMAL_POLICY = """
version: "1.0"
organization: "Test Corp"

agents:
  test-agent:
    description: "A test agent"
    scopes:
      - "vault:test:read"
    resources:
      - "slos://vaults/test/*"
"""

FULL_POLICY = """
version: "1.0"
organization: "Greenfield Academy"
compliance_frameworks:
  - FERPA

data_classifications:
  student-records:
    sensitivity: confidential
    pii_fields: ["ssn", "dob", "address"]
    retention_days: 2555
    description: "Student enrollment records"
  student-health:
    sensitivity: restricted
    pii_fields: ["ssn", "dob", "diagnosis"]
    retention_days: 2555

agents:
  academic-advisor:
    description: "Reads enrollment records"
    scopes:
      - "vault:student-records:read"
    resources:
      - "slos://vaults/student-records/*"
    constraints:
      require_purpose: true
      max_records_per_request: 50
    denied_resources:
      - "slos://vaults/student-health/*"

  financial-aid-agent:
    description: "Manages financial aid"
    scopes:
      - "vault:financial-aid:read"
      - "vault:financial-aid:write"
    resources:
      - "slos://vaults/financial-aid/*"
    constraints:
      require_purpose: true
      write_requires_approval: true
    denied_resources:
      - "slos://vaults/student-health/*"
"""

INVALID_NO_ORG = """
version: "1.0"
agents:
  test-agent:
    scopes: ["vault:test:read"]
"""

INVALID_NO_SCOPES = """
version: "1.0"
organization: "Test Corp"
agents:
  bad-agent:
    description: "No scopes"
"""


class TestPolicyLoading:
    def test_load_minimal_policy(self):
        engine = PolicyEngine.load_from_string(MINIMAL_POLICY)
        assert engine.policy.organization == "Test Corp"
        assert engine.policy.version == "1.0"
        assert len(engine.policy.agents) == 1

    def test_load_full_policy(self):
        engine = PolicyEngine.load_from_string(FULL_POLICY)
        assert engine.policy.organization == "Greenfield Academy"
        assert engine.policy.compliance_frameworks == ["FERPA"]
        assert len(engine.policy.agents) == 2
        assert len(engine.policy.data_classifications) == 2

    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(MINIMAL_POLICY)
            f.flush()
            engine = PolicyEngine.load(f.name)
        os.unlink(f.name)
        assert engine.policy.organization == "Test Corp"

    def test_load_nonexistent_file(self):
        with pytest.raises(PolicyValidationError, match="not found"):
            PolicyEngine.load("/nonexistent/policy.yaml")


class TestPolicyValidation:
    def test_invalid_no_organization(self):
        with pytest.raises(PolicyValidationError, match="organization"):
            PolicyEngine.load_from_string(INVALID_NO_ORG)

    def test_invalid_no_scopes(self):
        with pytest.raises(PolicyValidationError, match="no scopes"):
            PolicyEngine.load_from_string(INVALID_NO_SCOPES)

    def test_invalid_yaml_content(self):
        with pytest.raises(PolicyValidationError, match="YAML mapping"):
            PolicyEngine.load_from_string("just a string")


class TestAgentPolicies:
    @pytest.fixture
    def engine(self):
        return PolicyEngine.load_from_string(FULL_POLICY)

    def test_get_agent_policy(self, engine):
        agent = engine.get_agent_policy("academic-advisor")
        assert agent.agent_id == "academic-advisor"
        assert agent.scopes == ["vault:student-records:read"]
        assert agent.denied_resources == ["slos://vaults/student-health/*"]

    def test_agent_constraints_include_denied(self, engine):
        agent = engine.get_agent_policy("academic-advisor")
        assert "denied_resources" in agent.constraints
        assert "require_purpose" in agent.constraints
        assert agent.constraints["require_purpose"] is True

    def test_list_agents(self, engine):
        agents = engine.list_agents()
        assert len(agents) == 2
        ids = [a.agent_id for a in agents]
        assert "academic-advisor" in ids
        assert "financial-aid-agent" in ids

    def test_missing_agent_raises(self, engine):
        with pytest.raises(KeyError, match="not found"):
            engine.get_agent_policy("nonexistent-agent")


class TestDataClassifications:
    @pytest.fixture
    def engine(self):
        return PolicyEngine.load_from_string(FULL_POLICY)

    def test_get_classification(self, engine):
        dc = engine.get_data_classification("student-records")
        assert dc.sensitivity == "confidential"
        assert "ssn" in dc.pii_fields
        assert dc.retention_days == 2555

    def test_restricted_classification(self, engine):
        dc = engine.get_data_classification("student-health")
        assert dc.sensitivity == "restricted"
        assert "diagnosis" in dc.pii_fields

    def test_list_classifications(self, engine):
        dcs = engine.list_data_classifications()
        assert len(dcs) == 2

    def test_missing_classification_raises(self, engine):
        with pytest.raises(KeyError, match="not found"):
            engine.get_data_classification("nonexistent")


class TestEnvelopeCreation:
    @pytest.fixture
    def engine(self):
        return PolicyEngine.load_from_string(FULL_POLICY)

    @pytest.fixture
    def keys(self):
        private_key, public_key = generate_key_pair()
        return private_key, public_key

    def test_create_envelope_from_policy(self, engine, keys):
        private_key, public_key = keys
        envelope = engine.create_envelope_for_agent("academic-advisor", private_key)

        assert envelope.agent_id == "academic-advisor"
        assert envelope.authority.scopes == ["vault:student-records:read"]
        assert "denied_resources" in envelope.authority.constraints
        assert verify_signature(envelope, public_key)

    def test_envelope_constraints_enforced(self, engine, keys):
        """Envelopes created from policy carry constraints that are enforced."""
        private_key, public_key = keys
        envelope = engine.create_envelope_for_agent("academic-advisor", private_key)

        # require_purpose is set — no purpose should fail
        with pytest.raises(ConstraintViolation, match="require_purpose"):
            check_envelope(
                envelope, public_key, "vault:student-records:read",
                action="read", context={},
            )

        # With purpose → passes
        check_envelope(
            envelope, public_key, "vault:student-records:read",
            action="read", context={"purpose": "advising"},
        )

    def test_envelope_denied_resources_enforced(self, engine, keys):
        private_key, public_key = keys
        envelope = engine.create_envelope_for_agent("academic-advisor", private_key)

        # Health records denied
        with pytest.raises(ConstraintViolation, match="denied_resources"):
            check_envelope(
                envelope, public_key, "vault:student-records:read",
                action="read",
                resource="slos://vaults/student-health/HEALTH-001",
                context={"purpose": "advising"},
            )

    def test_envelope_custom_ttl(self, engine, keys):
        private_key, _ = keys
        envelope = engine.create_envelope_for_agent(
            "academic-advisor", private_key, ttl_seconds=600
        )
        assert envelope.ttl_seconds == 600


class TestPolicySummary:
    def test_summary_structure(self):
        engine = PolicyEngine.load_from_string(FULL_POLICY)
        s = engine.summary()

        assert s["version"] == "1.0"
        assert s["organization"] == "Greenfield Academy"
        assert s["compliance_frameworks"] == ["FERPA"]
        assert s["agent_count"] == 2
        assert s["classification_count"] == 2
        assert len(s["agents"]) == 2
        assert len(s["data_classifications"]) == 2

    def test_summary_agent_details(self):
        engine = PolicyEngine.load_from_string(FULL_POLICY)
        s = engine.summary()

        advisor = next(a for a in s["agents"] if a["id"] == "academic-advisor")
        assert advisor["scopes"] == ["vault:student-records:read"]
        assert advisor["denied_resources"] == ["slos://vaults/student-health/*"]
        assert "require_purpose" in advisor["constraints"]
