"""
Sovereign Life OS Backend Adapter

Handles:
- Signed request authentication (_auth header)
- Policy evaluation (allowed_agents, denied_agents, requires_approval)
- SLOS URI parsing (slos://vaults/{vault}/{doc_id})
"""

import json
import base64
import logging
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Any

import nacl.signing

from ..keys import AgentKeyStore
from ..crypto import encrypt_document, decrypt_document, is_encrypted

logger = logging.getLogger(__name__)


class Decision(Enum):
    """Policy evaluation result."""
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class PolicyResult:
    """Result of policy evaluation."""
    decision: Decision
    reason: str
    metadata: dict

    def __str__(self) -> str:
        return f"{self.decision.value.upper()}: {self.reason}"


@dataclass
class DocumentMetadata:
    """SLOS document metadata from frontmatter."""
    uri: str
    id: str
    domain: list[str]
    sensitivity: str
    allowed_agents: list[str]
    denied_agents: list[str]
    requires_approval: list[str]


def parse_slos_uri(uri: str) -> tuple[str, str]:
    """
    Parse SLOS URI into vault and document ID.

    Args:
        uri: URI in format slos://vaults/{vault}/{doc_id}

    Returns:
        Tuple of (vault, doc_id)

    Raises:
        ValueError: If URI format is invalid
    """
    if not uri.startswith("slos://vaults/"):
        raise ValueError(f"Invalid SLOS URI: {uri} (must start with slos://vaults/)")

    parts = uri.replace("slos://vaults/", "").split("/", 1)

    if len(parts) != 2:
        raise ValueError(f"Invalid SLOS URI: {uri} (expected slos://vaults/{{vault}}/{{doc_id}})")

    return parts[0], parts[1]


class SlosBackend:
    """
    Sovereign Life OS backend adapter.

    Handles:
    - Signing requests with agent Ed25519 keys
    - Calling SLOS MCP tools
    - Evaluating document-level policies

    Example:
        ```python
        backend = SlosBackend(
            config_path="./carryall-integration.json",
            key_store=AgentKeyStore("~/.carryall/keys")
        )

        # List vaults
        vaults = backend.list_vaults("executive-agent")

        # Check access
        result = backend.check_access(
            envelope=my_envelope,
            action="read",
            uri="slos://vaults/finance/019bf091-..."
        )

        if result.decision == Decision.ALLOW:
            # Proceed with access
            pass
        elif result.decision == Decision.REQUIRE_APPROVAL:
            # Request human approval
            pass
        else:
            # Access denied
            print(f"Denied: {result.reason}")
        ```
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        key_store: Optional[AgentKeyStore] = None,
        mcp_command: Optional[list[str]] = None,
        encryption_keys_dir: Optional[str] = None,
    ):
        """
        Initialize SLOS backend.

        Args:
            config_path: Path to carryall-integration.json from SLOS
            key_store: AgentKeyStore for signing requests
            mcp_command: Command to invoke SLOS MCP server (e.g., ["npx", "slos-mcp"])
        """
        self.config: dict = {}
        self._config_path = config_path
        if config_path:
            config_file = Path(config_path).expanduser()
            if config_file.exists():
                with open(config_file) as f:
                    self.config = json.load(f)

        self.key_store = key_store or AgentKeyStore()

        if mcp_command:
            self.mcp_command = mcp_command
        else:
            cmd = self.config.get("mcp_command", "slos-mcp")
            args = self.config.get("mcp_args", [])
            # mcp_command can be a string or list
            self.mcp_command = ([cmd] if isinstance(cmd, str) else cmd) + args

        # Resolve working directory for subprocess calls
        # mcp_cwd in config is relative to the config file's directory
        mcp_cwd = self.config.get("mcp_cwd")
        if mcp_cwd and config_path:
            config_dir = Path(config_path).expanduser().parent
            self.mcp_cwd = str(config_dir) if mcp_cwd == "." else str(config_dir / mcp_cwd)
        else:
            self.mcp_cwd = None

        # Encryption keys directory for vault-level encryption at rest
        if encryption_keys_dir:
            self.encryption_keys_dir = Path(encryption_keys_dir).expanduser()
        elif key_store and key_store.keys_dir:
            self.encryption_keys_dir = Path(key_store.keys_dir).expanduser()
        else:
            self.encryption_keys_dir = None

    def _sign_request(self, agent_id: str, arguments: dict) -> dict:
        """
        Add _auth header to request arguments.

        Signature format: sign(agent_id || timestamp || json(arguments))
        """
        signing_key = self.key_store.load_signing_key(agent_id)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Create canonical JSON of arguments (without _auth)
        args_without_auth = {k: v for k, v in arguments.items() if k != "_auth"}
        args_json = json.dumps(args_without_auth, sort_keys=True, separators=(",", ":"))

        # Sign: agent_id || timestamp || json
        message = f"{agent_id}{timestamp}{args_json}".encode()
        signature = signing_key.sign(message).signature

        return {
            **arguments,
            "_auth": {
                "agent_id": agent_id,
                "timestamp": timestamp,
                "signature": base64.b64encode(signature).decode(),
            },
        }

    def _call_mcp(self, method: str, params: dict, agent_id: str) -> dict:
        """
        Call SLOS MCP server with signed request.

        Uses stdio transport - sends JSON-RPC request to stdin, reads response from stdout.
        SLOS expects MCP protocol: method="tools/call" with tool name in params.name.
        """
        signed_params = self._sign_request(agent_id, params)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": method,
                "arguments": signed_params,
            },
        }

        try:
            result = subprocess.run(
                self.mcp_command,
                input=json.dumps(request),
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self.mcp_cwd,
            )

            if result.returncode != 0:
                raise RuntimeError(f"MCP call failed: {result.stderr}")

            response = json.loads(result.stdout)

            if "error" in response:
                raise RuntimeError(f"MCP error: {response['error']}")

            raw_result = response.get("result", {})

            # Unwrap MCP content envelope format
            # SLOS returns: {"content": [{"type": "text", "text": "{...}"}]}
            if "content" in raw_result and isinstance(raw_result["content"], list):
                for item in raw_result["content"]:
                    if item.get("type") == "text":
                        return json.loads(item["text"])

            return raw_result

        except subprocess.TimeoutExpired:
            raise RuntimeError("MCP call timed out after 30 seconds")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid MCP response: {e}")

    def _call_mcp_mock(self, method: str, params: dict, agent_id: str) -> dict:
        """
        Mock MCP call for testing without SLOS running.

        Returns simulated responses based on method.
        """
        if method == "list_vaults":
            return {"vaults": ["finance", "startup", "health", "personal", "shared"]}

        if method == "list_vault":
            vault = params.get("vault", "unknown")
            return {
                "vault": vault,
                "documents": [
                    {"id": "doc-001", "title": "Test Document 1"},
                    {"id": "doc-002", "title": "Test Document 2"},
                ],
            }

        if method == "get_metadata":
            uri = params.get("uri", "")
            vault, doc_id = parse_slos_uri(uri) if uri.startswith("slos://") else ("unknown", "unknown")

            # Simulate document-level policies based on vault
            allowed_agents = []
            denied_agents = []
            requires_approval = []

            if vault == "finance":
                allowed_agents = ["finance-agent", "executive-agent"]
                denied_agents = ["startup-agent"]
            elif vault == "health":
                allowed_agents = ["health-agent"]
                requires_approval = ["executive-agent"]
            elif vault == "shared":
                allowed_agents = []  # Open to all with scope

            return {
                "uri": uri,
                "id": doc_id,
                "domain": [vault],
                "sensitivity": "confidential" if vault in ["finance", "health"] else "internal",
                "allowed_agents": allowed_agents,
                "denied_agents": denied_agents,
                "requires_approval": requires_approval,
            }

        return {}

    def list_vaults(self, agent_id: str = "executive-agent", mock: bool = False) -> list[str]:
        """
        List available vaults.

        Args:
            agent_id: Agent to authenticate as
            mock: If True, return simulated data

        Returns:
            List of vault names
        """
        call = self._call_mcp_mock if mock else self._call_mcp
        result = call("list_vaults", {}, agent_id)
        return result.get("vaults", [])

    def list_resources(self, vault: str, agent_id: str, mock: bool = False) -> list[dict]:
        """
        List documents in a vault.

        Args:
            vault: Vault name
            agent_id: Agent to authenticate as
            mock: If True, return simulated data

        Returns:
            List of document metadata dicts
        """
        call = self._call_mcp_mock if mock else self._call_mcp
        result = call("list_vault", {"vault": vault}, agent_id)
        return result.get("documents", [])

    def get_metadata(self, uri: str, agent_id: str, mock: bool = False) -> DocumentMetadata:
        """
        Get document metadata including access policies.

        Args:
            uri: SLOS URI (slos://vaults/{vault}/{doc_id})
            agent_id: Agent to authenticate as
            mock: If True, return simulated data

        Returns:
            DocumentMetadata with policy fields
        """
        call = self._call_mcp_mock if mock else self._call_mcp
        result = call("get_metadata", {"uri": uri}, agent_id)

        return DocumentMetadata(
            uri=result.get("uri", uri),
            id=result.get("id", ""),
            domain=result.get("domain", []),
            sensitivity=result.get("sensitivity", "unknown"),
            allowed_agents=result.get("allowed_agents", []),
            denied_agents=result.get("denied_agents", []),
            requires_approval=result.get("requires_approval", []),
        )

    def check_access(
        self,
        envelope: Any,  # AuthorityEnvelope
        action: str,
        uri: str,
        mock: bool = False,
    ) -> PolicyResult:
        """
        Main policy evaluation.

        Evaluates in order:
        1. Explicit deny (denied_agents in document)
        2. Requires approval (requires_approval in document)
        3. Explicit allow (allowed_agents in document)
        4. Scope-based allow (envelope has vault:action scope)
        5. Default deny

        Args:
            envelope: Authority envelope with agent identity and scopes
            action: Action being requested (read, write, delete)
            uri: SLOS URI of the resource

        Returns:
            PolicyResult with decision and reason
        """
        agent_id = envelope.agent_id

        # Get document metadata (this call is also authenticated)
        try:
            metadata = self.get_metadata(uri, agent_id, mock=mock)
        except FileNotFoundError as e:
            # Agent key not found
            return PolicyResult(
                decision=Decision.DENY,
                reason=f"Agent key not found: {e}",
                metadata={"uri": uri, "error": str(e)},
            )
        except RuntimeError as e:
            # SLOS rejected the request
            return PolicyResult(
                decision=Decision.DENY,
                reason=f"SLOS rejected agent authentication: {e}",
                metadata={"uri": uri, "error": str(e)},
            )

        # 1. Explicit deny takes precedence
        if agent_id in metadata.denied_agents:
            return PolicyResult(
                decision=Decision.DENY,
                reason=f"Agent '{agent_id}' explicitly denied by document",
                metadata={"uri": uri, "rule": "denied_agents"},
            )

        # 2. Requires approval
        if agent_id in metadata.requires_approval:
            return PolicyResult(
                decision=Decision.REQUIRE_APPROVAL,
                reason=f"Agent '{agent_id}' requires approval for this document",
                metadata={"uri": uri, "rule": "requires_approval"},
            )

        # 3. Explicit allow in document
        if metadata.allowed_agents and agent_id in metadata.allowed_agents:
            return PolicyResult(
                decision=Decision.ALLOW,
                reason=f"Agent '{agent_id}' explicitly allowed by document",
                metadata={"uri": uri, "rule": "allowed_agents"},
            )

        # 4. Fall back to envelope scopes
        vault, _ = parse_slos_uri(uri)
        required_scope = f"vault:{vault}:{action}"

        if required_scope in envelope.authority.scopes:
            return PolicyResult(
                decision=Decision.ALLOW,
                reason=f"Envelope has scope '{required_scope}'",
                metadata={"uri": uri, "rule": "envelope_scope", "scope": required_scope},
            )

        # Also check wildcard scopes
        wildcard_scope = f"vault:{vault}:*"
        if wildcard_scope in envelope.authority.scopes:
            return PolicyResult(
                decision=Decision.ALLOW,
                reason=f"Envelope has wildcard scope '{wildcard_scope}'",
                metadata={"uri": uri, "rule": "envelope_scope", "scope": wildcard_scope},
            )

        # 5. Default deny
        return PolicyResult(
            decision=Decision.DENY,
            reason=f"No permission for {vault}:{action}",
            metadata={"uri": uri, "required_scope": required_scope},
        )

    def read_document(
        self, document_id: str, purpose: str, agent_id: str,
        domain: Optional[str] = None, mock: bool = False,
    ) -> dict:
        """
        Read document content from SLOS by UUID, decrypting if necessary.

        Args:
            document_id: Document UUID
            purpose: Why the agent needs this document (audit trail)
            agent_id: Agent to authenticate as
            domain: Vault domain (needed for decryption key lookup)
            mock: If True, return simulated data

        Returns:
            Dict with document content from SLOS (decrypted if encrypted)
        """
        call = self._call_mcp_mock if mock else self._call_mcp
        result = call("read_document", {"id": document_id, "purpose": purpose}, agent_id)

        # Decrypt document body if encrypted
        if self.encryption_keys_dir and domain:
            content = result.get("content", "")
            if isinstance(content, str) and is_encrypted(content):
                try:
                    result["content"] = decrypt_document(content, domain, self.encryption_keys_dir)
                    logger.debug(f"Decrypted document {document_id} from vault {domain}")
                except Exception as e:
                    logger.error(f"Failed to decrypt document {document_id}: {e}")
                    raise RuntimeError(f"Decryption failed for document {document_id}: {e}")

        return result

    def write_document(
        self,
        domain: str,
        content: str,
        metadata: dict,
        agent_id: str,
        document_id: str = None,
        mock: bool = False,
    ) -> dict:
        """
        Write/create a document in a SLOS vault, encrypting if sensitivity warrants it.

        Args:
            domain: Vault domain to write to (finance, startup, etc.)
            content: Document content (markdown)
            metadata: Document metadata (title, subpath, sensitivity, data_type)
            agent_id: Agent to authenticate as
            document_id: Optional UUID for updating existing documents
            mock: If True, return simulated data

        Returns:
            Dict with created document info from SLOS
        """
        # Encrypt document body before writing to disk
        if self.encryption_keys_dir:
            sensitivity = metadata.get("sensitivity", "internal")
            content, was_encrypted = encrypt_document(
                content, domain, sensitivity, self.encryption_keys_dir
            )
            if was_encrypted:
                metadata["encrypted"] = True
                logger.debug(f"Encrypted document for vault {domain} (sensitivity={sensitivity})")

        params = {
            "domain": domain,
            "content": content,
            "metadata": metadata,
        }
        if document_id:
            params["id"] = document_id
        call = self._call_mcp_mock if mock else self._call_mcp
        return call("write_document", params, agent_id)

    def query_documents(
        self,
        domain: str,
        query: str,
        agent_id: str,
        include_content: bool = False,
        limit: int = 10,
        mock: bool = False,
    ) -> dict:
        """
        Query documents within a domain.

        Args:
            domain: Vault domain to search (finance, startup, etc.)
            query: Search query string
            agent_id: Agent to authenticate as
            include_content: Whether to include document content in results
            limit: Maximum number of results
            mock: If True, return simulated data

        Returns:
            Dict with matching documents from SLOS
        """
        call = self._call_mcp_mock if mock else self._call_mcp
        return call("query_documents", {
            "domain": domain,
            "query": query,
            "include_content": include_content,
            "limit": limit,
        }, agent_id)
