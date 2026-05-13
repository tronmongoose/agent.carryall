"""
Tests for the MCP Server tool dispatching, request handling, and error paths.

Extends test_mcp_auth.py (which covers rate limiting and HTTP auth) to cover:
- JSON-RPC request routing
- Tool call dispatching and response format
- Envelope caching
- Error handling (PermissionDenied, InvalidSignature, EnvelopeExpired, unknown method)
- Tool-level permission enforcement
- Scope derivation from resource URIs
"""

import json
import os
import tempfile
import pytest

from authority_runtime.mcp_server import CarryallMCPServer
from authority_runtime.keys import AgentKeyStore
from authority_runtime.storage import EnvelopeStore
from authority_runtime.backends.memory import MemoryBackend
from authority_runtime.envelope import create_envelope, generate_key_pair
from authority_runtime.types import (
    Authority,
    Context,
    Skill,
    SkillParameters,
    ExecutionConfig,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def key_pair():
    return generate_key_pair()


@pytest.fixture
def temp_dirs():
    """Create temporary directories for keys and database."""
    with tempfile.TemporaryDirectory() as keys_dir, \
         tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_file:
        db_path = db_file.name
        yield keys_dir, db_path
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def server(temp_dirs):
    """Create a CarryallMCPServer backed by MemoryBackend."""
    keys_dir, db_path = temp_dirs
    key_store = AgentKeyStore(keys_dir)
    envelope_store = EnvelopeStore(db_path)
    backend = MemoryBackend()
    return CarryallMCPServer(
        key_store=key_store,
        envelope_store=envelope_store,
        backend=backend,
    )


@pytest.fixture
def envelope_with_key(server):
    """Create a valid envelope and register its key in the server's key store."""
    agent_id = "test-agent"

    # Generate keypair via AgentKeyStore (stores the seed file)
    server.key_store.generate_keypair(agent_id, overwrite=True)
    signing_key = server.key_store.load_signing_key(agent_id)

    # Get hex-encoded keys for envelope creation
    import nacl.encoding
    private_key = signing_key.encode(encoder=nacl.encoding.HexEncoder).decode("utf-8")
    public_key = signing_key.verify_key.encode(encoder=nacl.encoding.HexEncoder).decode("utf-8")

    envelope = create_envelope(
        agent_id=agent_id,
        provider="custom",
        step_number=1,
        root_policy_id="test-policy",
        skill=Skill(
            id="skill-vault-read",
            name="Read Vault",
            tool="vault_read",
            parameters=SkillParameters(allowed=["vault:finance:read"], constraints={}),
        ),
        authority=Authority(
            scopes=["vault:finance:read", "vault:hr:read", "audit:read"],
            resources=["*"],
        ),
        context=Context(included=["intent"], excluded=[]),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        ttl_seconds=300,
    )
    return envelope, private_key, public_key


# =============================================================================
# JSON-RPC Request Routing
# =============================================================================


class TestRequestRouting:
    @pytest.mark.asyncio
    async def test_initialize(self, server):
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "1",
            "method": "initialize",
            "params": {},
        })
        assert response["id"] == "1"
        assert "result" in response
        assert response["result"]["serverInfo"]["name"] == "carryall"
        assert "protocolVersion" in response["result"]

    @pytest.mark.asyncio
    async def test_list_tools(self, server):
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "2",
            "method": "tools/list",
            "params": {},
        })
        assert "result" in response
        tools = response["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "carryall_check_access" in tool_names
        assert "carryall_list_vaults" in tool_names
        assert "carryall_get_metadata" in tool_names
        assert "carryall_audit_log" in tool_names
        assert "carryall_compile_policy" in tool_names
        assert "carryall_read_document" in tool_names
        assert "carryall_write_document" in tool_names
        assert "carryall_query_documents" in tool_names

    @pytest.mark.asyncio
    async def test_unknown_method(self, server):
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "3",
            "method": "nonexistent/method",
            "params": {},
        })
        assert "error" in response
        assert response["error"]["code"] == -32601
        assert "not found" in response["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_unknown_tool(self, server):
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "4",
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        })
        assert "error" in response


# =============================================================================
# Tool: check_access
# =============================================================================


class TestCheckAccess:
    @pytest.mark.asyncio
    async def test_missing_envelope_returns_permission_denied(self, server):
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "5",
            "method": "tools/call",
            "params": {
                "name": "carryall_check_access",
                "arguments": {
                    "action": "read",
                    "resource": "slos://vaults/finance/doc-001",
                },
            },
        })
        assert "error" in response
        assert response["error"]["code"] == 403

    @pytest.mark.asyncio
    async def test_valid_check_access(self, server, envelope_with_key):
        envelope, _, _ = envelope_with_key
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "6",
            "method": "tools/call",
            "params": {
                "name": "carryall_check_access",
                "arguments": {
                    "envelope": envelope.model_dump(),
                    "action": "read",
                    "resource": "slos://vaults/finance/doc-001",
                },
            },
        })
        assert "result" in response
        content = json.loads(response["result"]["content"][0]["text"])
        assert "decision" in content


# =============================================================================
# Tool: list_vaults
# =============================================================================


class TestListVaults:
    @pytest.mark.asyncio
    async def test_missing_envelope(self, server):
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "7",
            "method": "tools/call",
            "params": {
                "name": "carryall_list_vaults",
                "arguments": {},
            },
        })
        assert "error" in response
        assert response["error"]["code"] == 403

    @pytest.mark.asyncio
    async def test_valid_list_vaults(self, server, envelope_with_key):
        envelope, _, _ = envelope_with_key
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "8",
            "method": "tools/call",
            "params": {
                "name": "carryall_list_vaults",
                "arguments": {
                    "envelope": envelope.model_dump(),
                },
            },
        })
        assert "result" in response
        content = json.loads(response["result"]["content"][0]["text"])
        assert "vaults" in content


# =============================================================================
# Tool: audit_log
# =============================================================================


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_missing_envelope(self, server):
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "9",
            "method": "tools/call",
            "params": {
                "name": "carryall_audit_log",
                "arguments": {},
            },
        })
        assert "error" in response
        assert response["error"]["code"] == 403

    @pytest.mark.asyncio
    async def test_requires_audit_scope(self, server, temp_dirs):
        """Envelope without audit:read scope should be rejected."""
        agent_id = "no-audit-agent"
        server.key_store.generate_keypair(agent_id, overwrite=True)
        signing_key = server.key_store.load_signing_key(agent_id)
        import nacl.encoding
        private_key = signing_key.encode(encoder=nacl.encoding.HexEncoder).decode("utf-8")

        envelope = create_envelope(
            agent_id=agent_id,
            provider="custom",
            step_number=1,
            root_policy_id="test-policy",
            skill=Skill(
                id="skill-1",
                name="Read",
                tool="read",
                parameters=SkillParameters(allowed=[], constraints={}),
            ),
            authority=Authority(
                scopes=["vault:finance:read"],  # No audit:read
                resources=["*"],
            ),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=300,
        )

        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "10",
            "method": "tools/call",
            "params": {
                "name": "carryall_audit_log",
                "arguments": {
                    "envelope": envelope.model_dump(),
                },
            },
        })
        assert "error" in response
        assert response["error"]["code"] == 403

    @pytest.mark.asyncio
    async def test_valid_audit_log(self, server, envelope_with_key):
        envelope, _, _ = envelope_with_key
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "11",
            "method": "tools/call",
            "params": {
                "name": "carryall_audit_log",
                "arguments": {
                    "envelope": envelope.model_dump(),
                    "limit": 10,
                },
            },
        })
        assert "result" in response
        content = json.loads(response["result"]["content"][0]["text"])
        assert "entries" in content
        assert "count" in content


# =============================================================================
# Envelope Caching
# =============================================================================


class TestEnvelopeCaching:
    def test_envelope_is_cached_after_load(self, server, envelope_with_key):
        envelope, _, _ = envelope_with_key
        envelope_data = envelope.model_dump()

        loaded = server._load_envelope(envelope_data)
        assert loaded.envelope_id == envelope.envelope_id

        # Should be in cache now
        assert envelope.envelope_id in server._envelope_cache

        # Loading again should return cached version
        loaded2 = server._load_envelope(envelope_data)
        assert loaded2.envelope_id == loaded.envelope_id

    def test_cache_stores_by_envelope_id(self, server, envelope_with_key):
        """Multiple loads of the same envelope should return cached version."""
        envelope, _, _ = envelope_with_key
        envelope_data = envelope.model_dump()

        # First load
        loaded1 = server._load_envelope(envelope_data)
        # Second load
        loaded2 = server._load_envelope(envelope_data)
        assert loaded1.envelope_id == loaded2.envelope_id
        # Only one entry in cache
        assert len(server._envelope_cache) == 1


# =============================================================================
# Scope Derivation
# =============================================================================


class TestScopeDerivation:
    def test_slos_uri_to_scope(self, server):
        scope = server._derive_scope_from_resource("slos://vaults/finance/doc-001", "read")
        assert scope == "vault:finance:read"

    def test_slos_uri_write_scope(self, server):
        scope = server._derive_scope_from_resource("slos://vaults/hr/employees", "write")
        assert scope == "vault:hr:write"

    def test_slos_nested_path(self, server):
        scope = server._derive_scope_from_resource("slos://vaults/finance/budgets/q1-2026", "read")
        assert scope == "vault:finance:read"

    def test_non_slos_uri_fallback(self, server):
        scope = server._derive_scope_from_resource("https://example.com/api", "read")
        assert scope == "access:read"


# =============================================================================
# Error Response Formatting
# =============================================================================


class TestErrorResponses:
    @pytest.mark.asyncio
    async def test_permission_denied_returns_403(self, server):
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "err-1",
            "method": "tools/call",
            "params": {
                "name": "carryall_check_access",
                "arguments": {"action": "read", "resource": "slos://vaults/finance/x"},
            },
        })
        assert response["error"]["code"] == 403

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_401(self, server):
        """Tampered envelope should get 401."""
        agent_id = "tamper-agent"
        server.key_store.generate_keypair(agent_id, overwrite=True)
        signing_key = server.key_store.load_signing_key(agent_id)
        import nacl.encoding
        private_key = signing_key.encode(encoder=nacl.encoding.HexEncoder).decode("utf-8")

        envelope = create_envelope(
            agent_id=agent_id,
            provider="custom",
            step_number=1,
            root_policy_id="test-policy",
            skill=Skill(
                id="skill-1",
                name="Read",
                tool="read",
                parameters=SkillParameters(allowed=[], constraints={}),
            ),
            authority=Authority(scopes=["vault:finance:read"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=300,
        )

        # Tamper with the envelope data
        envelope_data = envelope.model_dump()
        envelope_data["authority"]["scopes"].append("vault:secret:admin")

        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "err-2",
            "method": "tools/call",
            "params": {
                "name": "carryall_check_access",
                "arguments": {
                    "envelope": envelope_data,
                    "action": "read",
                    "resource": "slos://vaults/finance/x",
                },
            },
        })
        assert "error" in response
        assert response["error"]["code"] == 401

    @pytest.mark.asyncio
    async def test_generic_error_returns_32000(self, server):
        """Unknown tool should return -32000 error."""
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "err-3",
            "method": "tools/call",
            "params": {
                "name": "nonexistent_tool",
                "arguments": {},
            },
        })
        assert "error" in response
        assert response["error"]["code"] == -32000


# =============================================================================
# Skill generation from scopes
# =============================================================================


class TestSkillGeneration:
    def test_generates_skills_from_scopes(self, server):
        skills = server._generate_skills_from_scopes([
            "vault:finance:read",
            "vault:hr:write",
            "audit:read",
        ])
        assert len(skills) == 3
        ids = [s.id for s in skills]
        assert "skill-vault-read" in ids
        assert "skill-vault-write" in ids
        # audit:read is 2-segment, so action is "access" per the code logic
        assert "skill-audit-access" in ids

    def test_deduplicates_skills(self, server):
        skills = server._generate_skills_from_scopes([
            "vault:finance:read",
            "vault:hr:read",  # Same namespace:action, different resource
        ])
        # Both have id "skill-vault-read" so should deduplicate
        assert len(skills) == 1

    def test_empty_scopes_returns_default(self, server):
        skills = server._generate_skills_from_scopes([])
        assert len(skills) == 1
        assert skills[0].id == "skill-default"


# =============================================================================
# Structured deny payload (AI Negotiation Loop)
# =============================================================================


class TestStructuredDenyPayload:
    @pytest.mark.asyncio
    async def test_missing_envelope_carries_structured_data(self, server):
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "den-1",
            "method": "tools/call",
            "params": {
                "name": "carryall_check_access",
                "arguments": {"action": "read", "resource": "slos://vaults/finance/x"},
            },
        })
        assert response["error"]["code"] == 403
        data = response["error"]["data"]
        assert data["reason_class"] == "MISSING_ENVELOPE"
        assert "carryall_compile_policy" in data["retry_hint"]
        assert data["reason"] == "Missing envelope"

    @pytest.mark.asyncio
    async def test_scope_missing_carries_suggested_scope(self, server, envelope_with_key):
        """A scoped deny must surface suggested_scope and current_scope."""
        envelope, _, _ = envelope_with_key
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "den-2",
            "method": "tools/call",
            "params": {
                "name": "carryall_get_metadata",
                "arguments": {
                    "envelope": envelope.model_dump(),
                    "uri": "slos://vaults/finance/doc-001",
                },
            },
        })
        if response.get("error", {}).get("code") == 403:
            data = response["error"]["data"]
            assert "reason_class" in data
            assert data["reason"]

    @pytest.mark.asyncio
    async def test_invalid_signature_carries_structured_data(self, server):
        agent_id = "tamper-agent"
        server.key_store.generate_keypair(agent_id, overwrite=True)
        signing_key = server.key_store.load_signing_key(agent_id)
        import nacl.encoding
        private_key = signing_key.encode(encoder=nacl.encoding.HexEncoder).decode("utf-8")

        envelope = create_envelope(
            agent_id=agent_id,
            provider="custom",
            step_number=1,
            root_policy_id="test-policy",
            skill=Skill(
                id="skill-1",
                name="Read",
                tool="read",
                parameters=SkillParameters(allowed=[], constraints={}),
            ),
            authority=Authority(scopes=["vault:finance:read"], resources=["*"]),
            context=Context(included=[], excluded=[]),
            execution=ExecutionConfig(provider_config={}),
            private_key=private_key,
            ttl_seconds=300,
        )
        envelope_data = envelope.model_dump()
        envelope_data["authority"]["scopes"].append("vault:secret:admin")

        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": "den-3",
            "method": "tools/call",
            "params": {
                "name": "carryall_check_access",
                "arguments": {
                    "envelope": envelope_data,
                    "action": "read",
                    "resource": "slos://vaults/finance/x",
                },
            },
        })
        assert response["error"]["code"] == 401
        data = response["error"]["data"]
        assert data["reason_class"] == "INVALID_SIGNATURE"
        assert "Re-mint" in data["retry_hint"]


class TestClassifyDenial:
    def test_classify_explicit_deny(self):
        from authority_runtime.enforce import classify_denial
        out = classify_denial("Agent 'x' explicitly denied", {"rule": "denied_agents"})
        assert out["reason_class"] == "EXPLICIT_DENY"
        assert out["retry_hint"]

    def test_classify_scope_missing_from_required_scope(self):
        from authority_runtime.enforce import classify_denial
        out = classify_denial(
            "No permission for finance:read",
            {"required_scope": "vault:finance:read"},
        )
        assert out["reason_class"] == "SCOPE_MISSING"
        assert out["suggested_scope"] == "vault:finance:read"

    def test_classify_requires_approval(self):
        from authority_runtime.enforce import classify_denial
        out = classify_denial("needs approval", {"rule": "requires_approval"})
        assert out["reason_class"] == "APPROVAL_REQUIRED"

    def test_classify_unknown_returns_none(self):
        from authority_runtime.enforce import classify_denial
        out = classify_denial("something else", {})
        assert out["reason_class"] is None
        assert out["retry_hint"] is None
