"""Tests for vault-scoped enforcement primitives.

Ported from Fast Forward agents (bjornswarm dogfood deployment).
Original: fastforward.agents/tests/test_enforcement.py
"""

from __future__ import annotations

from authority_runtime import generate_key_pair
from authority_runtime.vault_scope import (
    VaultScope,
    check_vault_access,
    create_vault_envelope,
    enforce_envelope,
)


class TestVaultScope:
    def test_scopes_generated(self):
        scope = VaultScope(slug="acme", read=["context.json"], write=["proposals/"])
        assert "vault:acme:read:context.json" in scope.scopes
        assert "vault:acme:write:proposals/" in scope.scopes

    def test_domains(self):
        scope = VaultScope(slug="acme", read=["context.json"])
        assert scope.domains == ["acme"]

    def test_operations(self):
        scope = VaultScope(slug="acme", read=["x"], write=["y"])
        assert "read" in scope.operations
        assert "write" in scope.operations

    def test_read_only(self):
        scope = VaultScope(slug="acme", read=["context.json"])
        assert scope.operations == ["read"]


class TestEnvelopeCreation:
    def test_create_valid_envelope(self):
        private_key, _ = generate_key_pair()
        scope = VaultScope(slug="acme", read=["context.json"], write=["proposals/"])
        envelope = create_vault_envelope(
            agent_id="proposal-agent",
            scope=scope,
            private_key=private_key,
            ttl_seconds=3600,
        )
        assert envelope is not None
        assert envelope.agent_id == "proposal-agent"

    def test_envelope_has_scopes(self):
        private_key, _ = generate_key_pair()
        scope = VaultScope(slug="acme", read=["context.json"])
        envelope = create_vault_envelope(
            agent_id="test-agent",
            scope=scope,
            private_key=private_key,
        )
        granted = set(envelope.authority.scopes)
        assert "vault:acme:read:context.json" in granted


class TestAccessCheck:
    def test_valid_envelope_passes(self):
        private_key, _ = generate_key_pair()
        scope = VaultScope(slug="acme", read=["context.json"])
        envelope = create_vault_envelope(
            agent_id="test-agent",
            scope=scope,
            private_key=private_key,
            ttl_seconds=3600,
        )
        result = check_vault_access(envelope, scope)
        assert result["allowed"] is True

    def test_expired_envelope_fails(self):
        private_key, _ = generate_key_pair()
        scope = VaultScope(slug="acme", read=["context.json"])
        envelope = create_vault_envelope(
            agent_id="test-agent",
            scope=scope,
            private_key=private_key,
        )
        # Force expiration using model_copy with ISO 8601 timestamp in the past
        expired_envelope = envelope.model_copy(
            update={"expires_at": "2020-01-01T00:00:00Z"}
        )
        result = check_vault_access(expired_envelope, scope)
        assert result["allowed"] is False
        assert "expired" in result["reason"].lower()

    def test_missing_scope_fails(self):
        private_key, _ = generate_key_pair()
        # Envelope grants read on context.json only
        create_scope = VaultScope(slug="acme", read=["context.json"])
        envelope = create_vault_envelope(
            agent_id="test-agent",
            scope=create_scope,
            private_key=private_key,
        )
        # But we request write to proposals/
        check_scope = VaultScope(slug="acme", read=["context.json"], write=["proposals/"])
        result = check_vault_access(envelope, check_scope)
        assert result["allowed"] is False
        assert "missing scopes" in result["reason"].lower()

    def test_no_envelope_fails(self):
        scope = VaultScope(slug="acme", read=["context.json"])
        result = check_vault_access(None, scope)
        assert result["allowed"] is False

    def test_cross_client_denied(self):
        private_key, _ = generate_key_pair()
        # Envelope scoped to "acme"
        acme_scope = VaultScope(slug="acme", read=["context.json"])
        envelope = create_vault_envelope(
            agent_id="test-agent",
            scope=acme_scope,
            private_key=private_key,
        )
        # Try to use it on "globex"
        globex_scope = VaultScope(slug="globex", read=["context.json"])
        result = check_vault_access(envelope, globex_scope)
        assert result["allowed"] is False


class TestEnforceDecorator:
    def test_decorator_allows_valid_scope(self):
        scope = VaultScope(slug="acme", read=["context.json"])

        @enforce_envelope(scope=scope, agent_id="test-agent")
        def my_agent(slug, _envelope=None):
            return f"processed {slug}"

        result = my_agent("acme")
        assert result == "processed acme"

    def test_decorator_injects_envelope(self):
        scope = VaultScope(slug="acme", read=["context.json"])

        @enforce_envelope(scope=scope, agent_id="test-agent")
        def my_agent(_envelope=None):
            assert _envelope is not None
            assert _envelope.agent_id == "test-agent"
            return "ok"

        assert my_agent() == "ok"

    def test_dynamic_scope_resolution(self):
        def resolve_scope(slug, **kwargs):
            return VaultScope(slug=slug, read=["context.json"])

        @enforce_envelope(scope=resolve_scope, agent_id="test-agent")
        def my_agent(slug, _envelope=None):
            return slug

        assert my_agent("acme") == "acme"
