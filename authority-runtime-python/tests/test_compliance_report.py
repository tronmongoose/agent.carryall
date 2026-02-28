"""
Tests for full compliance report generation and HTML rendering (Deliverable 4).
"""

import tempfile
import os
import pytest
from authority_runtime.compliance import ComplianceReport
from authority_runtime.storage import EnvelopeStore
from authority_runtime.envelope import create_envelope, generate_key_pair
from authority_runtime.enforce import create_audit_entry
from authority_runtime.types import (
    Skill, SkillParameters, Authority, Context, ExecutionConfig,
)


@pytest.fixture
def populated_store():
    """Create a store with realistic audit data for report generation."""
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = db_file.name
    db_file.close()

    store = EnvelopeStore(db_path)

    # Create agents and envelopes
    agents = {
        "academic-advisor": {
            "scopes": ["vault:student-records:read"],
            "resources": ["slos://vaults/student-records/*"],
        },
        "financial-aid-agent": {
            "scopes": ["vault:financial-aid:read"],
            "resources": ["slos://vaults/financial-aid/*"],
        },
    }

    agent_keys = {}
    for agent_id, config in agents.items():
        private_key, public_key = generate_key_pair()
        agent_keys[agent_id] = (private_key, public_key)

        envelope = create_envelope(
            agent_id=agent_id,
            provider="custom",
            step_number=1,
            root_policy_id=f"policy-{agent_id}",
            skill=Skill(
                id=f"skill-{agent_id}",
                name=f"{agent_id}-access",
                tool="test",
                parameters=SkillParameters(allowed=["read"], constraints={}),
            ),
            authority=Authority(scopes=config["scopes"], resources=config["resources"]),
            context=Context(included=["purpose"], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=3600,
        )
        store.save_envelope(envelope)

        # Audit entries
        _, pub = agent_keys[agent_id]

        # Academic advisor reads student records
        if agent_id == "academic-advisor":
            for i in range(3):
                entry = create_audit_entry(
                    action="read",
                    envelope=envelope,
                    public_key=pub,
                    result="success",
                    resource=f"slos://vaults/student-records/STU-{i:03d}",
                )
                store.save_audit_entry(entry)

        # Financial aid agent: reads aid, blocked from health
        if agent_id == "financial-aid-agent":
            for i in range(2):
                entry = create_audit_entry(
                    action="read",
                    envelope=envelope,
                    public_key=pub,
                    result="success",
                    resource=f"slos://vaults/financial-aid/AID-{i:03d}",
                )
                store.save_audit_entry(entry)

            entry = create_audit_entry(
                action="read",
                envelope=envelope,
                public_key=pub,
                result="blocked",
                resource="slos://vaults/student-health/HEALTH-001",
            )
            store.save_audit_entry(entry)

    yield store
    os.unlink(db_path)


class TestGenerateFullReport:
    def test_report_structure(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        report = report_gen.generate_full_report(title="Test Report")

        assert report["report_type"] == "full_compliance"
        assert report["title"] == "Test Report"
        assert "executive_summary" in report
        assert "agent_reports" in report
        assert "attestations" in report

    def test_executive_summary(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        report = report_gen.generate_full_report()
        summary = report["executive_summary"]

        assert summary["total_agents"] == 2
        assert summary["total_events"] == 6  # 3 + 2 + 1
        assert summary["successful"] == 5
        assert summary["blocked"] == 1

    def test_agent_reports_present(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        report = report_gen.generate_full_report()

        assert "academic-advisor" in report["agent_reports"]
        assert "financial-aid-agent" in report["agent_reports"]

        advisor = report["agent_reports"]["academic-advisor"]
        assert advisor["summary"]["total_events"] == 3

    def test_attestations_generated(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        report = report_gen.generate_full_report()

        # 2 agents x 2 sensitive patterns = 4 attestations
        assert len(report["attestations"]) == 4

        # Academic advisor never touched health → confirmed
        advisor_health = next(
            a for a in report["attestations"]
            if a["query_parameters"]["agent_id"] == "academic-advisor"
            and "student-health" in a["query_parameters"]["resource_pattern"]
        )
        assert advisor_health["confirmed"] is True

    def test_policy_summary_included(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        policy = {"organization": "Test", "agents": [], "data_classifications": []}
        report = report_gen.generate_full_report(policy_summary=policy)
        assert report["policy_summary"] == policy


class TestRenderHTML:
    def test_html_is_valid(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        report = report_gen.generate_full_report(title="FERPA Q1 Report")
        html = report_gen.render_html(report)

        assert html.startswith("<!DOCTYPE html>")
        assert "FERPA Q1 Report" in html
        assert "</html>" in html

    def test_html_contains_executive_summary(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        report = report_gen.generate_full_report()
        html = report_gen.render_html(report)

        assert "Executive Summary" in html
        assert "Total Events" in html

    def test_html_contains_agent_breakdown(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        report = report_gen.generate_full_report()
        html = report_gen.render_html(report)

        assert "academic-advisor" in html
        assert "financial-aid-agent" in html

    def test_html_contains_attestations(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        report = report_gen.generate_full_report()
        html = report_gen.render_html(report)

        assert "Negative Attestation" in html
        assert "CONFIRMED" in html

    def test_html_contains_policy_data(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        policy = {
            "organization": "Greenfield Academy",
            "compliance_frameworks": ["FERPA"],
            "data_classifications": [
                {
                    "domain": "student-health",
                    "sensitivity": "restricted",
                    "pii_fields": ["ssn", "diagnosis"],
                    "retention_days": 2555,
                }
            ],
        }
        report = report_gen.generate_full_report(policy_summary=policy)
        html = report_gen.render_html(report)

        assert "Data Classifications" in html
        assert "student-health" in html
        assert "restricted" in html
        assert "ssn" in html

    def test_html_self_contained(self, populated_store):
        """No external CSS/JS references."""
        report_gen = ComplianceReport(populated_store)
        report = report_gen.generate_full_report()
        html = report_gen.render_html(report)

        assert "http" not in html.split("</style>")[1]  # No external resources after style block
        assert "<script" not in html

    def test_html_escapes_special_chars(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        report = report_gen.generate_full_report(title='Test <script>alert("xss")</script>')
        html = report_gen.render_html(report)

        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_write_html_file(self, populated_store):
        report_gen = ComplianceReport(populated_store)
        report = report_gen.generate_full_report()
        html = report_gen.render_html(report)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write(html)
            path = f.name

        assert os.path.getsize(path) > 100
        os.unlink(path)
