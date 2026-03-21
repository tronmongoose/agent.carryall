"""
Tests for FERPA compliance reporting module.
"""

import os
import tempfile
import pytest

from authority_runtime.envelope import create_simple_envelope, create_envelope, generate_key_pair
from authority_runtime.enforce import create_audit_entry
from authority_runtime.storage import EnvelopeStore
from authority_runtime.compliance import ComplianceReport
from authority_runtime.types import Skill, SkillParameters, Authority, Context, ExecutionConfig


@pytest.fixture
def store():
    """Create a temporary EnvelopeStore."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    s = EnvelopeStore(db_path)
    yield s
    os.unlink(db_path)


@pytest.fixture
def key_pair():
    return generate_key_pair()


@pytest.fixture
def populated_store(store, key_pair):
    """Store with realistic edtech audit data."""
    private_key, public_key = key_pair

    # Create envelopes for different agents
    agents = {
        "academic-advisor": ["vault:student-records:read"],
        "financial-aid-agent": ["vault:financial-aid:read"],
        "registrar-agent": ["vault:student-records:read", "vault:student-records:write"],
    }

    for agent_id, scopes in agents.items():
        envelope = create_envelope(
            agent_id=agent_id,
            provider="custom",
            step_number=1,
            root_policy_id=f"policy-{agent_id}",
            skill=Skill(
                id="test", name="test", tool="test",
                parameters=SkillParameters(allowed=["p"], constraints={}),
            ),
            authority=Authority(scopes=scopes, resources=["*"]),
            context=Context(included=["data"], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
        )
        store.save_envelope(envelope)

        # Advisor reads enrollment records (allowed)
        if agent_id == "academic-advisor":
            for i in range(3):
                entry = create_audit_entry(
                    action="read",
                    envelope=envelope,
                    public_key=public_key,
                    result="success",
                    resource=f"slos://vaults/student-records/enrollment-{i:03d}",
                )
                store.save_audit_entry(entry)

        # Financial aid agent reads aid records (allowed)
        if agent_id == "financial-aid-agent":
            entry = create_audit_entry(
                action="read",
                envelope=envelope,
                public_key=public_key,
                result="success",
                resource="slos://vaults/financial-aid/aid-001",
            )
            store.save_audit_entry(entry)

            # Financial aid tried to access health records (blocked)
            entry = create_audit_entry(
                action="read",
                envelope=envelope,
                public_key=public_key,
                result="blocked",
                resource="slos://vaults/student-health/health-001",
            )
            store.save_audit_entry(entry)

        # Registrar writes records
        if agent_id == "registrar-agent":
            entry = create_audit_entry(
                action="write",
                envelope=envelope,
                public_key=public_key,
                result="success",
                resource="slos://vaults/student-records/enrollment-new",
            )
            store.save_audit_entry(entry)

    return store


class TestNegativeAttestation:
    """The FERPA killer feature."""

    def test_confirms_no_access(self, populated_store):
        report = ComplianceReport(populated_store)
        result = report.negative_attestation(
            agent_id="academic-advisor",
            resource_pattern="slos://vaults/student-health/%",
        )
        assert result["confirmed"] is True
        assert result["count"] == 0
        assert "CONFIRMED" in result["result"]
        assert result["attestation_hash"]  # non-empty hash

    def test_detects_access(self, populated_store):
        report = ComplianceReport(populated_store)
        result = report.negative_attestation(
            agent_id="financial-aid-agent",
            resource_pattern="slos://vaults/student-health/%",
        )
        assert result["confirmed"] is False
        assert result["count"] == 1
        assert "FAILED" in result["result"]

    def test_attestation_hash_is_deterministic(self, populated_store):
        report = ComplianceReport(populated_store)
        r1 = report.negative_attestation("agent-a", "pattern-%")
        r2 = report.negative_attestation("agent-a", "pattern-%")
        assert r1["attestation_hash"] == r2["attestation_hash"]

    def test_different_params_different_hash(self, populated_store):
        report = ComplianceReport(populated_store)
        r1 = report.negative_attestation("agent-a", "pattern-1-%")
        r2 = report.negative_attestation("agent-a", "pattern-2-%")
        assert r1["attestation_hash"] != r2["attestation_hash"]


class TestAgentAccessReport:
    def test_shows_all_accesses(self, populated_store):
        report = ComplianceReport(populated_store)
        result = report.agent_access_report(agent_id="academic-advisor")
        assert result["summary"]["total_events"] == 3
        assert result["summary"]["successful"] == 3
        assert result["summary"]["distinct_resources"] == 3

    def test_filter_by_resource(self, populated_store):
        report = ComplianceReport(populated_store)
        result = report.agent_access_report(
            agent_id="financial-aid-agent",
            resource_pattern="slos://vaults/student-health/%",
        )
        assert result["summary"]["total_events"] == 1
        assert result["summary"]["blocked"] == 1


class TestResourceAccessReport:
    def test_shows_all_agents_for_resource(self, populated_store):
        report = ComplianceReport(populated_store)
        result = report.resource_access_report(
            resource_pattern="slos://vaults/student-records/%",
        )
        assert result["summary"]["total_events"] == 4  # 3 advisor + 1 registrar
        assert result["summary"]["distinct_agents"] == 2

    def test_agents_ordered_by_count(self, populated_store):
        report = ComplianceReport(populated_store)
        result = report.resource_access_report(
            resource_pattern="slos://vaults/student-records/%",
        )
        agents = result["agents"]
        assert agents[0]["agent_id"] == "academic-advisor"  # 3 accesses
        assert agents[0]["access_count"] == 3


class TestScopeUsageReport:
    def test_shows_agent_activity(self, populated_store):
        report = ComplianceReport(populated_store)
        result = report.scope_usage_report()
        assert result["summary"]["total_events"] == 6
        assert result["summary"]["distinct_agents"] == 3


class TestExport:
    def test_generate_summary(self, populated_store):
        report = ComplianceReport(populated_store)
        attestation = report.negative_attestation(
            "academic-advisor", "slos://vaults/student-health/%"
        )
        summary = report.generate_summary(attestation)
        assert "CONFIRMED" in summary
        assert "academic-advisor" in summary

    def test_export_json(self, populated_store, tmp_path):
        report = ComplianceReport(populated_store)
        result = report.agent_access_report("academic-advisor")
        filepath = str(tmp_path / "report.json")
        report.export_json(result, filepath)
        with open(filepath) as f:
            loaded = __import__("json").load(f)
        assert loaded["report_type"] == "agent_access"

    def test_export_csv(self, populated_store, tmp_path):
        report = ComplianceReport(populated_store)
        entries = populated_store.get_audit_trail(agent_id="academic-advisor")
        filepath = str(tmp_path / "audit.csv")
        report.export_csv(entries, filepath)
        with open(filepath) as f:
            lines = f.readlines()
        assert len(lines) == 4  # header + 3 entries

    def test_export_csv_string(self, populated_store):
        report = ComplianceReport(populated_store)
        entries = populated_store.get_audit_trail(agent_id="academic-advisor")
        csv_str = report.export_csv_string(entries)
        assert "timestamp" in csv_str  # header
        assert "academic-advisor" in csv_str
