"""
In-Memory Backend for Authority Runtime

Provides a standalone backend that stores documents in Python dicts,
enabling Carryall to run without any external data store (SLOS, S3, etc.).

Use cases:
- Quick demos and pilots
- Testing and CI
- Environments where SLOS is not available

Implements the same interface as SlosBackend so it can be used as a drop-in
replacement via CarryallMCPServer(backend=MemoryBackend(...)).
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .slos import Decision, PolicyResult, DocumentMetadata, parse_slos_uri


class MemoryBackend:
    """
    In-memory document store with policy evaluation.

    Documents are organized by vault (domain). Each document has content,
    metadata, and access control fields (allowed_agents, denied_agents,
    requires_approval) that mirror SLOS document frontmatter.

    Example:
        ```python
        backend = MemoryBackend(initial_data={
            "finance": {
                "budget-2026": {
                    "content": "# Q1 Budget\\nTotal: $500k",
                    "title": "Q1 2026 Budget",
                    "sensitivity": "confidential",
                    "allowed_agents": ["finance-agent"],
                    "denied_agents": ["startup-agent"],
                }
            }
        })

        # List vaults
        vaults = backend.list_vaults("finance-agent")
        # ["finance"]

        # Check access
        result = backend.check_access(envelope, "read", "slos://vaults/finance/budget-2026")
        # PolicyResult(decision=Decision.ALLOW, ...)
        ```
    """

    def __init__(self, initial_data: Optional[dict] = None):
        """
        Initialize in-memory backend.

        Args:
            initial_data: Optional dict of {vault: {doc_id: {content, title, ...}}}
                Each document can have:
                - content (str): Document content (default: "")
                - title (str): Document title (default: doc_id)
                - sensitivity (str): internal|confidential|restricted (default: "internal")
                - allowed_agents (list[str]): Agents with explicit access (default: [])
                - denied_agents (list[str]): Agents explicitly denied (default: [])
                - requires_approval (list[str]): Agents needing approval (default: [])
                - data_type (str): note|profile|budget|etc. (default: "note")
                - subpath (str): Subdirectory within vault (default: "")
        """
        # {vault_name: {doc_id: doc_dict}}
        self._vaults: dict[str, dict[str, dict]] = {}

        if initial_data:
            for vault, documents in initial_data.items():
                self._vaults[vault] = {}
                for doc_id, doc_data in documents.items():
                    self._vaults[vault][doc_id] = {
                        "content": doc_data.get("content", ""),
                        "title": doc_data.get("title", doc_id),
                        "sensitivity": doc_data.get("sensitivity", "internal"),
                        "allowed_agents": doc_data.get("allowed_agents", []),
                        "denied_agents": doc_data.get("denied_agents", []),
                        "requires_approval": doc_data.get("requires_approval", []),
                        "data_type": doc_data.get("data_type", "note"),
                        "subpath": doc_data.get("subpath", ""),
                        "created_at": doc_data.get(
                            "created_at",
                            datetime.now(timezone.utc).isoformat(),
                        ),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }

    def list_vaults(self, agent_id: str = "executive-agent", mock: bool = False) -> list[str]:
        """List available vaults."""
        return list(self._vaults.keys())

    def list_resources(self, vault: str, agent_id: str, mock: bool = False) -> list[dict]:
        """List documents in a vault."""
        if vault not in self._vaults:
            return []
        return [
            {"id": doc_id, "title": doc.get("title", doc_id)}
            for doc_id, doc in self._vaults[vault].items()
        ]

    def get_metadata(self, uri: str, agent_id: str, mock: bool = False) -> DocumentMetadata:
        """Get document metadata including access policies."""
        vault, doc_id = parse_slos_uri(uri)

        if vault not in self._vaults or doc_id not in self._vaults[vault]:
            # Return empty metadata for missing documents (same as SLOS behavior)
            return DocumentMetadata(
                uri=uri,
                id=doc_id,
                domain=[vault],
                sensitivity="unknown",
                allowed_agents=[],
                denied_agents=[],
                requires_approval=[],
            )

        doc = self._vaults[vault][doc_id]
        return DocumentMetadata(
            uri=uri,
            id=doc_id,
            domain=[vault],
            sensitivity=doc.get("sensitivity", "internal"),
            allowed_agents=doc.get("allowed_agents", []),
            denied_agents=doc.get("denied_agents", []),
            requires_approval=doc.get("requires_approval", []),
        )

    def check_access(
        self,
        envelope: Any,
        action: str,
        uri: str,
        mock: bool = False,
    ) -> PolicyResult:
        """
        Policy evaluation — same priority chain as SlosBackend.

        1. Explicit deny (denied_agents)
        2. Requires approval (requires_approval)
        3. Explicit allow (allowed_agents)
        4. Scope-based allow (envelope scopes)
        5. Default deny
        """
        agent_id = envelope.agent_id

        metadata = self.get_metadata(uri, agent_id)

        # 1. Explicit deny
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

        # 3. Explicit allow
        if metadata.allowed_agents and agent_id in metadata.allowed_agents:
            return PolicyResult(
                decision=Decision.ALLOW,
                reason=f"Agent '{agent_id}' explicitly allowed by document",
                metadata={"uri": uri, "rule": "allowed_agents"},
            )

        # 4. Scope-based allow
        vault, _ = parse_slos_uri(uri)
        required_scope = f"vault:{vault}:{action}"

        if required_scope in envelope.authority.scopes:
            return PolicyResult(
                decision=Decision.ALLOW,
                reason=f"Envelope has scope '{required_scope}'",
                metadata={"uri": uri, "rule": "envelope_scope", "scope": required_scope},
            )

        # Wildcard scope
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

    def read_document(self, document_id: str, purpose: str, agent_id: str, mock: bool = False) -> dict:
        """Read document content by ID (searches all vaults)."""
        for vault_name, docs in self._vaults.items():
            if document_id in docs:
                doc = docs[document_id]
                return {
                    "id": document_id,
                    "vault": vault_name,
                    "content": doc.get("content", ""),
                    "title": doc.get("title", document_id),
                    "sensitivity": doc.get("sensitivity", "internal"),
                    "purpose": purpose,
                }

        return {"error": f"Document '{document_id}' not found", "id": document_id}

    def write_document(
        self,
        domain: str,
        content: str,
        metadata: dict,
        agent_id: str,
        document_id: str = None,
        mock: bool = False,
    ) -> dict:
        """Write/create a document in a vault."""
        if domain not in self._vaults:
            self._vaults[domain] = {}

        doc_id = document_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        self._vaults[domain][doc_id] = {
            "content": content,
            "title": metadata.get("title", doc_id),
            "sensitivity": metadata.get("sensitivity", "internal"),
            "allowed_agents": metadata.get("allowed_agents", []),
            "denied_agents": metadata.get("denied_agents", []),
            "requires_approval": metadata.get("requires_approval", []),
            "data_type": metadata.get("data_type", "note"),
            "subpath": metadata.get("subpath", ""),
            "created_at": now,
            "updated_at": now,
        }

        return {
            "id": doc_id,
            "domain": domain,
            "created_at": now,
            "status": "created" if not document_id else "updated",
        }

    def query_documents(
        self,
        domain: str,
        query: str,
        agent_id: str,
        include_content: bool = False,
        limit: int = 10,
        mock: bool = False,
    ) -> dict:
        """Search documents within a vault domain."""
        if domain not in self._vaults:
            return {"domain": domain, "query": query, "results": [], "total": 0}

        query_lower = query.lower()
        results = []

        for doc_id, doc in self._vaults[domain].items():
            # Simple text search across title and content
            title = doc.get("title", "").lower()
            content = doc.get("content", "").lower()

            if query_lower in title or query_lower in content:
                result = {
                    "id": doc_id,
                    "title": doc.get("title", doc_id),
                    "sensitivity": doc.get("sensitivity", "internal"),
                }
                if include_content:
                    result["content"] = doc.get("content", "")
                results.append(result)

            if len(results) >= limit:
                break

        return {
            "domain": domain,
            "query": query,
            "results": results,
            "total": len(results),
        }
