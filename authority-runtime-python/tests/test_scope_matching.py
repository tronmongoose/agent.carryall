"""
Tests for scope wildcard matching in enforce.py.

Covers: exact match, segment wildcards, full wildcards, mismatched segments,
and integration with check_envelope().
"""

import pytest
from authority_runtime.enforce import _scope_matches, check_envelope, PermissionDenied
from authority_runtime.envelope import create_envelope, generate_key_pair
from authority_runtime.types import Skill, SkillParameters, Authority, Context, ExecutionConfig


class TestScopeMatches:
    """Unit tests for _scope_matches()."""

    def test_exact_match(self):
        assert _scope_matches("vault:finance:read", "vault:finance:read") is True

    def test_exact_mismatch(self):
        assert _scope_matches("vault:finance:read", "vault:finance:write") is False

    def test_segment_wildcard_middle(self):
        assert _scope_matches("vault:*:read", "vault:finance:read") is True

    def test_segment_wildcard_middle_different_domain(self):
        assert _scope_matches("vault:*:read", "vault:health:read") is True

    def test_segment_wildcard_action(self):
        assert _scope_matches("vault:finance:*", "vault:finance:read") is True
        assert _scope_matches("vault:finance:*", "vault:finance:write") is True

    def test_segment_wildcard_first(self):
        assert _scope_matches("*:finance:read", "vault:finance:read") is True
        assert _scope_matches("*:finance:read", "audit:finance:read") is True

    def test_full_wildcard(self):
        assert _scope_matches("*:*:*", "vault:finance:read") is True
        assert _scope_matches("*:*:*", "audit:health:write") is True

    def test_mismatched_segment_count(self):
        assert _scope_matches("vault:*", "vault:finance:read") is False
        assert _scope_matches("vault:finance:read:extra", "vault:finance:read") is False

    def test_two_segment_scope(self):
        assert _scope_matches("audit:read", "audit:read") is True
        assert _scope_matches("audit:*", "audit:read") is True
        assert _scope_matches("*:read", "audit:read") is True

    def test_wildcard_does_not_match_wrong_action(self):
        assert _scope_matches("vault:*:read", "vault:finance:write") is False

    def test_wildcard_does_not_match_wrong_prefix(self):
        assert _scope_matches("vault:*:read", "audit:finance:read") is False

    def test_empty_scope(self):
        assert _scope_matches("", "") is True
        assert _scope_matches("vault:finance:read", "") is False


class TestCheckEnvelopeWildcardScopes:
    """Integration tests: check_envelope() with wildcard scopes."""

    @pytest.fixture
    def keys(self):
        private_key, public_key = generate_key_pair()
        return private_key, public_key

    def _make_envelope(self, private_key, scopes, resources=None):
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
            ),
            context=Context(included=["purpose"], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=3600,
        )

    def test_exact_scope_passes(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(private_key, ["vault:finance:read"])
        check_envelope(envelope, public_key, "vault:finance:read")

    def test_wildcard_scope_passes(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(private_key, ["vault:*:read"])
        check_envelope(envelope, public_key, "vault:finance:read")
        check_envelope(envelope, public_key, "vault:health:read")
        check_envelope(envelope, public_key, "vault:student-records:read")

    def test_action_wildcard_passes(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(private_key, ["vault:finance:*"])
        check_envelope(envelope, public_key, "vault:finance:read")
        check_envelope(envelope, public_key, "vault:finance:write")

    def test_full_wildcard_passes(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(private_key, ["*:*:*"])
        check_envelope(envelope, public_key, "vault:finance:read")
        check_envelope(envelope, public_key, "audit:health:write")

    def test_wildcard_scope_denied_wrong_action(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(private_key, ["vault:*:read"])
        with pytest.raises(PermissionDenied):
            check_envelope(envelope, public_key, "vault:finance:write")

    def test_wildcard_scope_denied_wrong_prefix(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(private_key, ["vault:*:read"])
        with pytest.raises(PermissionDenied):
            check_envelope(envelope, public_key, "audit:finance:read")

    def test_multiple_scopes_with_wildcards(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(
            private_key,
            ["vault:finance:read", "vault:*:write", "audit:read"],
        )
        check_envelope(envelope, public_key, "vault:finance:read")
        check_envelope(envelope, public_key, "vault:health:write")
        check_envelope(envelope, public_key, "audit:read")
        with pytest.raises(PermissionDenied):
            check_envelope(envelope, public_key, "vault:health:read")

    def test_no_scope_match_raises(self, keys):
        private_key, public_key = keys
        envelope = self._make_envelope(private_key, ["vault:finance:read"])
        with pytest.raises(PermissionDenied, match="requires scope"):
            check_envelope(envelope, public_key, "vault:health:read")
