"""
Tests for the SLOS backend adapter.

Covers:
- parse_slos_uri (valid/invalid URI parsing)
- SlosBackend._sign_request (Ed25519 signature construction)
- SlosBackend.check_access policy evaluation (deny > approval > allow > scope > default deny)
- SlosBackend mock mode (list_vaults, get_metadata, list_resources)
- SlosBackend._call_mcp error handling (timeout, bad JSON, MCP errors)
- Request signing verification (signature is valid, excludes _auth from payload)
- Wildcard scope matching in check_access
"""

import base64
import json
import tempfile
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import nacl.signing
import nacl.encoding
import pytest

from authority_runtime.backends.slos import (
    Decision,
    DocumentMetadata,
    PolicyResult,
    SlosBackend,
    parse_slos_uri,
)
from authority_runtime.keys import AgentKeyStore
from authority_runtime.types import Authority


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def key_store():
    with tempfile.TemporaryDirectory() as d:
        store = AgentKeyStore(d)
        store.generate_keypair("test-agent")
        store.generate_keypair("finance-agent")
        store.generate_keypair("startup-agent")
        yield store


@pytest.fixture
def backend(key_store):
    return SlosBackend(key_store=key_store)


@pytest.fixture
def mock_envelope():
    """Minimal envelope-like object for check_access."""
    envelope = MagicMock()
    envelope.agent_id = "finance-agent"
    envelope.authority = MagicMock()
    envelope.authority.scopes = ["vault:finance:read"]
    return envelope


# =============================================================================
# parse_slos_uri
# =============================================================================


class TestParseSlosUri:
    def test_valid_uri(self):
        vault, doc_id = parse_slos_uri("slos://vaults/finance/doc-001")
        assert vault == "finance"
        assert doc_id == "doc-001"

    def test_nested_path(self):
        vault, doc_id = parse_slos_uri("slos://vaults/finance/budgets/q1-2026-budget.md")
        assert vault == "finance"
        assert doc_id == "budgets/q1-2026-budget.md"

    def test_uuid_doc_id(self):
        vault, doc_id = parse_slos_uri("slos://vaults/health/019bf091-1234-5678-9abc-def012345678")
        assert vault == "health"
        assert doc_id == "019bf091-1234-5678-9abc-def012345678"

    def test_invalid_scheme_raises(self):
        with pytest.raises(ValueError, match="Invalid SLOS URI"):
            parse_slos_uri("https://example.com/vaults/finance/doc")

    def test_missing_doc_id_raises(self):
        with pytest.raises(ValueError, match="Invalid SLOS URI"):
            parse_slos_uri("slos://vaults/finance")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Invalid SLOS URI"):
            parse_slos_uri("")

    def test_vault_only_no_slash_raises(self):
        with pytest.raises(ValueError, match="Invalid SLOS URI"):
            parse_slos_uri("slos://vaults/onlyvault")


# =============================================================================
# PolicyResult and Decision
# =============================================================================


class TestPolicyResult:
    def test_str_representation(self):
        result = PolicyResult(
            decision=Decision.ALLOW,
            reason="Agent allowed",
            metadata={},
        )
        assert str(result) == "ALLOW: Agent allowed"

    def test_deny_str(self):
        result = PolicyResult(
            decision=Decision.DENY,
            reason="Insufficient permissions",
            metadata={},
        )
        assert "DENY" in str(result)

    def test_decision_values(self):
        assert Decision.ALLOW.value == "allow"
        assert Decision.DENY.value == "deny"
        assert Decision.REQUIRE_APPROVAL.value == "require_approval"


# =============================================================================
# Request Signing
# =============================================================================


class TestRequestSigning:
    def test_sign_request_adds_auth(self, backend):
        signed = backend._sign_request("test-agent", {"action": "read"})
        assert "_auth" in signed
        assert signed["_auth"]["agent_id"] == "test-agent"
        assert "timestamp" in signed["_auth"]
        assert "signature" in signed["_auth"]

    def test_signature_is_valid_base64(self, backend):
        signed = backend._sign_request("test-agent", {"key": "value"})
        sig_b64 = signed["_auth"]["signature"]
        sig_bytes = base64.b64decode(sig_b64)
        assert len(sig_bytes) == 64  # Ed25519 signature is 64 bytes

    def test_signature_verifies(self, backend, key_store):
        args = {"action": "read", "resource": "slos://vaults/finance/doc-001"}
        signed = backend._sign_request("test-agent", args)

        # Reconstruct the message
        auth = signed["_auth"]
        args_without_auth = {k: v for k, v in signed.items() if k != "_auth"}
        args_json = json.dumps(args_without_auth, sort_keys=True, separators=(",", ":"))
        message = f"{auth['agent_id']}{auth['timestamp']}{args_json}".encode()

        # Verify with the public key
        signing_key = key_store.load_signing_key("test-agent")
        verify_key = signing_key.verify_key
        signature = base64.b64decode(auth["signature"])

        # Should not raise
        verify_key.verify(message, signature)

    def test_auth_excluded_from_signing_payload(self, backend):
        """The _auth field itself must not be included in the signed payload."""
        args = {"action": "read", "_auth": {"old": "data"}}
        signed = backend._sign_request("test-agent", args)

        # The original _auth should be replaced, not nested
        assert signed["_auth"]["agent_id"] == "test-agent"
        assert "old" not in signed["_auth"]

    def test_sign_request_with_missing_key_raises(self, backend):
        with pytest.raises(FileNotFoundError):
            backend._sign_request("nonexistent-agent", {"action": "read"})


# =============================================================================
# Mock Mode
# =============================================================================


class TestMockMode:
    def test_list_vaults_mock(self, backend):
        vaults = backend.list_vaults("test-agent", mock=True)
        assert isinstance(vaults, list)
        assert "finance" in vaults
        assert "health" in vaults

    def test_list_resources_mock(self, backend):
        docs = backend.list_resources("finance", "test-agent", mock=True)
        assert isinstance(docs, list)
        assert len(docs) > 0
        assert "id" in docs[0]

    def test_get_metadata_mock_finance(self, backend):
        meta = backend.get_metadata(
            "slos://vaults/finance/doc-001", "test-agent", mock=True
        )
        assert isinstance(meta, DocumentMetadata)
        assert meta.sensitivity == "confidential"
        assert "finance-agent" in meta.allowed_agents
        assert "startup-agent" in meta.denied_agents

    def test_get_metadata_mock_health(self, backend):
        meta = backend.get_metadata(
            "slos://vaults/health/doc-001", "test-agent", mock=True
        )
        assert meta.sensitivity == "confidential"
        assert "executive-agent" in meta.requires_approval

    def test_get_metadata_mock_shared(self, backend):
        meta = backend.get_metadata(
            "slos://vaults/shared/doc-001", "test-agent", mock=True
        )
        assert meta.sensitivity == "internal"
        assert meta.allowed_agents == []  # Open to all with scope


# =============================================================================
# check_access Policy Evaluation
# =============================================================================


class TestCheckAccessPolicy:
    """Tests the 5-tier policy evaluation: deny > approval > allow > scope > default deny."""

    def test_explicit_deny_takes_precedence(self, backend):
        """Agent in denied_agents list should be denied regardless of scopes."""
        envelope = MagicMock()
        envelope.agent_id = "startup-agent"
        envelope.authority.scopes = ["vault:finance:read"]  # Has scope, but denied

        result = backend.check_access(
            envelope, "read", "slos://vaults/finance/doc-001", mock=True
        )
        assert result.decision == Decision.DENY
        assert "denied" in result.reason.lower()
        assert result.metadata["rule"] == "denied_agents"

    def test_requires_approval(self, backend):
        """Agent in requires_approval list should get REQUIRE_APPROVAL."""
        envelope = MagicMock()
        envelope.agent_id = "executive-agent"
        envelope.authority.scopes = ["vault:health:read"]

        result = backend.check_access(
            envelope, "read", "slos://vaults/health/doc-001", mock=True
        )
        assert result.decision == Decision.REQUIRE_APPROVAL
        assert result.metadata["rule"] == "requires_approval"

    def test_explicit_allow(self, backend):
        """Agent in allowed_agents list should be allowed."""
        envelope = MagicMock()
        envelope.agent_id = "finance-agent"
        envelope.authority.scopes = []  # No scopes needed, explicitly allowed

        result = backend.check_access(
            envelope, "read", "slos://vaults/finance/doc-001", mock=True
        )
        assert result.decision == Decision.ALLOW
        assert result.metadata["rule"] == "allowed_agents"

    def test_scope_based_allow(self, backend):
        """Agent with matching scope but not in allowed_agents should be allowed via scope."""
        envelope = MagicMock()
        envelope.agent_id = "some-other-agent"
        envelope.authority.scopes = ["vault:shared:read"]

        result = backend.check_access(
            envelope, "read", "slos://vaults/shared/doc-001", mock=True
        )
        assert result.decision == Decision.ALLOW
        assert result.metadata["rule"] == "envelope_scope"

    def test_wildcard_scope_allows(self, backend):
        """Wildcard scope vault:shared:* should allow read access."""
        envelope = MagicMock()
        envelope.agent_id = "wildcard-agent"
        envelope.authority.scopes = ["vault:shared:*"]

        result = backend.check_access(
            envelope, "read", "slos://vaults/shared/doc-001", mock=True
        )
        assert result.decision == Decision.ALLOW
        assert "wildcard" in result.reason.lower()

    def test_default_deny(self, backend):
        """Agent with no matching scope and not in allowed_agents should be denied."""
        envelope = MagicMock()
        envelope.agent_id = "random-agent"
        envelope.authority.scopes = ["vault:hr:read"]  # Wrong vault

        result = backend.check_access(
            envelope, "read", "slos://vaults/shared/doc-001", mock=True
        )
        assert result.decision == Decision.DENY
        assert "No permission" in result.reason

    def test_deny_overrides_scope(self, backend):
        """Even with matching scope, denied_agents should deny."""
        envelope = MagicMock()
        envelope.agent_id = "startup-agent"
        # Has the scope for finance, but is denied in the document
        envelope.authority.scopes = ["vault:finance:read", "vault:finance:*"]

        result = backend.check_access(
            envelope, "read", "slos://vaults/finance/doc-001", mock=True
        )
        assert result.decision == Decision.DENY

    def test_approval_overrides_scope(self, backend):
        """requires_approval should take precedence over scope-based allow."""
        envelope = MagicMock()
        envelope.agent_id = "executive-agent"
        envelope.authority.scopes = ["vault:health:read"]

        result = backend.check_access(
            envelope, "read", "slos://vaults/health/doc-001", mock=True
        )
        assert result.decision == Decision.REQUIRE_APPROVAL


# =============================================================================
# check_access Error Handling
# =============================================================================


class TestCheckAccessErrors:
    def test_missing_agent_key_returns_deny(self, backend):
        """If agent key is not found, check_access should deny (not crash)."""
        envelope = MagicMock()
        envelope.agent_id = "nonexistent-agent"
        envelope.authority.scopes = ["vault:finance:read"]

        # Mock mode doesn't need signing, but real mode would fail at get_metadata
        # Use a patched get_metadata that raises FileNotFoundError
        with patch.object(backend, "get_metadata", side_effect=FileNotFoundError("No key")):
            result = backend.check_access(envelope, "read", "slos://vaults/finance/doc-001")

        assert result.decision == Decision.DENY
        assert "key not found" in result.reason.lower()

    def test_slos_runtime_error_returns_deny(self, backend):
        """If SLOS rejects the request, check_access should deny."""
        envelope = MagicMock()
        envelope.agent_id = "test-agent"
        envelope.authority.scopes = ["vault:finance:read"]

        with patch.object(backend, "get_metadata", side_effect=RuntimeError("SLOS auth failed")):
            result = backend.check_access(envelope, "read", "slos://vaults/finance/doc-001")

        assert result.decision == Decision.DENY
        assert "SLOS rejected" in result.reason


# =============================================================================
# _call_mcp Error Handling
# =============================================================================


class TestCallMcpErrors:
    def test_timeout_raises(self, backend):
        import subprocess
        with patch("authority_runtime.backends.slos.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="slos-mcp", timeout=30)):
            with pytest.raises(RuntimeError, match="timed out"):
                backend._call_mcp("test_method", {}, "test-agent")

    def test_nonzero_exit_raises(self, backend):
        import subprocess
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Something went wrong"
        with patch("authority_runtime.backends.slos.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="MCP call failed"):
                backend._call_mcp("test_method", {}, "test-agent")

    def test_invalid_json_response_raises(self, backend):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json"
        with patch("authority_runtime.backends.slos.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="Invalid MCP response"):
                backend._call_mcp("test_method", {}, "test-agent")

    def test_mcp_error_in_response_raises(self, backend):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32000, "message": "Internal error"},
        })
        with patch("authority_runtime.backends.slos.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="MCP error"):
                backend._call_mcp("test_method", {}, "test-agent")

    def test_successful_mcp_call_unwraps_content(self, backend):
        """Successful MCP response with content envelope should be unwrapped."""
        inner = {"vaults": ["finance", "health"]}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": json.dumps(inner)}],
            },
        })
        with patch("authority_runtime.backends.slos.subprocess.run", return_value=mock_result):
            result = backend._call_mcp("list_vaults", {}, "test-agent")
        assert result == inner


# =============================================================================
# SlosBackend Init
# =============================================================================


class TestSlosBackendInit:
    def test_default_init_no_config(self, key_store):
        backend = SlosBackend(key_store=key_store)
        assert backend.config == {}
        assert backend.mcp_command == ["slos-mcp"]

    def test_init_with_config_file(self, key_store):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "mcp_command": "custom-mcp",
                "mcp_args": ["--verbose"],
                "mcp_cwd": ".",
            }, f)
            config_path = f.name

        backend = SlosBackend(config_path=config_path, key_store=key_store)
        assert backend.mcp_command == ["custom-mcp", "--verbose"]
        assert backend.mcp_cwd is not None

    def test_init_with_custom_mcp_command(self, key_store):
        backend = SlosBackend(
            key_store=key_store,
            mcp_command=["npx", "slos-mcp", "--stdio"],
        )
        assert backend.mcp_command == ["npx", "slos-mcp", "--stdio"]

    def test_init_with_missing_config_file(self, key_store):
        """Non-existent config file should not crash, just use empty config."""
        backend = SlosBackend(config_path="/nonexistent/path.json", key_store=key_store)
        assert backend.config == {}
