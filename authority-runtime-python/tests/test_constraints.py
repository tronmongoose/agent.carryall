"""
Tests for constraint enforcement (constraints.py + enforce.py integration).

Covers each constraint type independently, combinations, backward compat,
and integration with check_envelope().
"""

import pytest
from authority_runtime.constraints import check_constraints
from authority_runtime.enforce import (
    check_envelope,
    ConstraintViolation,
    ApprovalRequired,
)
from authority_runtime.envelope import create_envelope, generate_key_pair
from authority_runtime.types import (
    Skill,
    SkillParameters,
    Authority,
    Context,
    ExecutionConfig,
)


# ---- Unit tests for check_constraints() ----


class TestEmptyConstraints:
    def test_empty_dict_always_allowed(self):
        result = check_constraints({}, "read")
        assert result.allowed is True
        assert result.violated == []

    def test_none_like_constraints(self):
        result = check_constraints({}, "write", resource="slos://vaults/health/x")
        assert result.allowed is True


class TestRequirePurpose:
    def test_no_purpose_denied(self):
        result = check_constraints({"require_purpose": True}, "read", context={})
        assert result.allowed is False
        assert any("require_purpose" in v for v in result.violated)

    def test_empty_purpose_denied(self):
        result = check_constraints(
            {"require_purpose": True}, "read", context={"purpose": ""}
        )
        assert result.allowed is False

    def test_whitespace_purpose_denied(self):
        result = check_constraints(
            {"require_purpose": True}, "read", context={"purpose": "   "}
        )
        assert result.allowed is False

    def test_valid_purpose_allowed(self):
        result = check_constraints(
            {"require_purpose": True}, "read", context={"purpose": "FERPA audit"}
        )
        assert result.allowed is True

    def test_require_purpose_false_is_noop(self):
        result = check_constraints(
            {"require_purpose": False}, "read", context={}
        )
        assert result.allowed is True


class TestDeniedResources:
    def test_exact_deny(self):
        result = check_constraints(
            {"denied_resources": ["slos://vaults/student-health/*"]},
            "read",
            resource="slos://vaults/student-health/HEALTH-001",
        )
        assert result.allowed is False
        assert any("denied_resources" in v for v in result.violated)

    def test_glob_deny(self):
        result = check_constraints(
            {"denied_resources": ["slos://vaults/student-health/*"]},
            "read",
            resource="slos://vaults/student-health/anything",
        )
        assert result.allowed is False

    def test_non_matching_resource_allowed(self):
        result = check_constraints(
            {"denied_resources": ["slos://vaults/student-health/*"]},
            "read",
            resource="slos://vaults/student-records/STU-001",
        )
        assert result.allowed is True

    def test_multiple_deny_patterns(self):
        constraints = {
            "denied_resources": [
                "slos://vaults/student-health/*",
                "slos://vaults/finance/*",
            ]
        }
        result1 = check_constraints(
            constraints, "read", resource="slos://vaults/student-health/x"
        )
        assert result1.allowed is False

        result2 = check_constraints(
            constraints, "read", resource="slos://vaults/finance/y"
        )
        assert result2.allowed is False

        result3 = check_constraints(
            constraints, "read", resource="slos://vaults/student-records/z"
        )
        assert result3.allowed is True

    def test_no_resource_skips_check(self):
        result = check_constraints(
            {"denied_resources": ["slos://vaults/student-health/*"]},
            "read",
            resource=None,
        )
        assert result.allowed is True


class TestMaxRecords:
    def test_under_limit_allowed(self):
        result = check_constraints(
            {"max_records_per_request": 50},
            "read",
            context={"record_count": 10},
        )
        assert result.allowed is True

    def test_at_limit_allowed(self):
        result = check_constraints(
            {"max_records_per_request": 50},
            "read",
            context={"record_count": 50},
        )
        assert result.allowed is True

    def test_over_limit_denied(self):
        result = check_constraints(
            {"max_records_per_request": 50},
            "read",
            context={"record_count": 51},
        )
        assert result.allowed is False
        assert any("max_records_per_request" in v for v in result.violated)

    def test_no_record_count_skips_check(self):
        result = check_constraints(
            {"max_records_per_request": 50},
            "read",
            context={},
        )
        assert result.allowed is True


class TestWriteRequiresApproval:
    def test_write_action_requires_approval(self):
        result = check_constraints(
            {"write_requires_approval": True}, "write"
        )
        assert result.allowed is False
        assert result.require_approval is True

    def test_delete_action_requires_approval(self):
        result = check_constraints(
            {"write_requires_approval": True}, "delete"
        )
        assert result.allowed is False
        assert result.require_approval is True

    def test_read_action_not_affected(self):
        result = check_constraints(
            {"write_requires_approval": True}, "read"
        )
        assert result.allowed is True

    def test_write_requires_approval_false_is_noop(self):
        result = check_constraints(
            {"write_requires_approval": False}, "write"
        )
        assert result.allowed is True


class TestConstraintCombinations:
    def test_purpose_and_denied_resources(self):
        constraints = {
            "require_purpose": True,
            "denied_resources": ["slos://vaults/health/*"],
        }
        # Missing purpose — fail
        result = check_constraints(
            constraints, "read", resource="slos://vaults/records/x", context={}
        )
        assert result.allowed is False

        # Valid purpose, denied resource — fail
        result = check_constraints(
            constraints,
            "read",
            resource="slos://vaults/health/x",
            context={"purpose": "audit"},
        )
        assert result.allowed is False

        # Valid purpose, allowed resource — pass
        result = check_constraints(
            constraints,
            "read",
            resource="slos://vaults/records/x",
            context={"purpose": "audit"},
        )
        assert result.allowed is True

    def test_multiple_violations_all_reported(self):
        constraints = {
            "require_purpose": True,
            "denied_resources": ["slos://vaults/health/*"],
            "max_records_per_request": 10,
        }
        result = check_constraints(
            constraints,
            "read",
            resource="slos://vaults/health/x",
            context={"record_count": 100},
        )
        assert result.allowed is False
        assert len(result.violated) == 3


# ---- Integration tests: check_envelope() with constraints ----


class TestCheckEnvelopeConstraints:
    @pytest.fixture
    def keys(self):
        private_key, public_key = generate_key_pair()
        return private_key, public_key

    def _make_envelope(self, private_key, scopes, constraints=None, resources=None):
        return create_envelope(
            agent_id="test-agent",
            provider="custom",
            step_number=1,
            root_policy_id="policy-test",
            skill=Skill(
                id="skill-test",
                name="test-access",
                tool="test-tool",
                parameters=SkillParameters(allowed=["read"], constraints={}),
            ),
            authority=Authority(
                scopes=scopes,
                resources=resources or ["slos://vaults/*"],
                constraints=constraints or {},
            ),
            context=Context(included=["purpose"], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=3600,
        )

    def test_no_constraints_passes(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(private_key, ["vault:finance:read"])
        check_envelope(envelope, public_key, "vault:finance:read")

    def test_require_purpose_enforced(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(
            private_key,
            ["vault:finance:read"],
            constraints={"require_purpose": True},
        )
        # Without purpose context → ConstraintViolation
        with pytest.raises(ConstraintViolation, match="require_purpose"):
            check_envelope(
                envelope, public_key, "vault:finance:read",
                action="read", context={},
            )
        # With purpose context → passes
        check_envelope(
            envelope, public_key, "vault:finance:read",
            action="read", context={"purpose": "quarterly audit"},
        )

    def test_denied_resources_enforced(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(
            private_key,
            ["vault:*:read"],
            constraints={"denied_resources": ["slos://vaults/student-health/*"]},
        )
        # Denied resource → ConstraintViolation
        with pytest.raises(ConstraintViolation, match="denied_resources"):
            check_envelope(
                envelope, public_key, "vault:student-health:read",
                action="read", resource="slos://vaults/student-health/HEALTH-001",
            )
        # Allowed resource → passes
        check_envelope(
            envelope, public_key, "vault:student-records:read",
            action="read", resource="slos://vaults/student-records/STU-001",
        )

    def test_write_requires_approval_enforced(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(
            private_key,
            ["vault:finance:read", "vault:finance:write"],
            constraints={"write_requires_approval": True},
        )
        # Read → passes (write_requires_approval only blocks writes)
        check_envelope(
            envelope, public_key, "vault:finance:read",
            action="read",
        )
        # Write → ApprovalRequired
        with pytest.raises(ApprovalRequired):
            check_envelope(
                envelope, public_key, "vault:finance:write",
                action="write",
            )

    def test_action_inferred_from_scope(self, keys):
        """When action is not explicitly passed, it's inferred from the scope's last segment."""
        private_key, public_key = keys
        envelope = self._make_envelope(
            private_key,
            ["vault:finance:write"],
            constraints={"write_requires_approval": True},
        )
        # No explicit action → inferred as "write" from scope
        with pytest.raises(ApprovalRequired):
            check_envelope(envelope, public_key, "vault:finance:write")

    def test_backward_compat_empty_constraints(self, keys):
        """Envelopes with empty constraints dict (the default) work exactly as before."""
        private_key, public_key = keys
        envelope = self._make_envelope(private_key, ["vault:finance:read"])
        assert envelope.authority.constraints == {}
        check_envelope(envelope, public_key, "vault:finance:read")
