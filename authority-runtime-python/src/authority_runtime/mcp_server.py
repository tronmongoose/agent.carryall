"""
Carryall MCP Server - Authority Runtime enforcement for MCP tool calls.

This server wraps existing MCP tools with envelope-based permission enforcement.
Clawdbot (or any MCP client) connects to this server, which then:
1. Validates the agent's envelope before each tool call
2. Proxies allowed calls to the underlying backend (SLOS, etc.)
3. Logs all access decisions to the audit trail

Usage:
    # Start the server (stdio mode - for local MCP clients)
    python -m authority_runtime.mcp_server

    # Or via CLI (stdio mode)
    carryall mcp serve

    # HTTP mode (for Kubernetes sidecar)
    carryall mcp serve --transport http --port 8765

Configuration (~/.carryall/config.json):
    {
        "mcp": {
            "host": "localhost",
            "port": 8765,
            "backends": ["slos"]
        }
    }
"""

import asyncio
import json
import os
import signal
import sys
import time
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .logging_config import configure_logging, new_request_id

configure_logging()
logger = logging.getLogger(__name__)

from .keys import AgentKeyStore
from .storage import EnvelopeStore
from .types import AuthorityEnvelope, Skill, Authority, Context
from .enforce import check_envelope, create_audit_entry, PermissionDenied, InvalidSignature, EnvelopeExpired, ConstraintViolation, ApprovalRequired
from .approvals import ApprovalQueue
from .backends.slos import SlosBackend, Decision
from .backends.memory import MemoryBackend

try:
    from .compiler import OpenAICompiler, AnthropicCompiler, compile_policy
except ImportError:
    OpenAICompiler = None
    AnthropicCompiler = None
    compile_policy = None


class CarryallMCPServer:
    """
    MCP Server that enforces Authority Runtime envelopes on tool calls.

    Protocol: JSON-RPC 2.0 over stdio

    Tools exposed:
    - carryall.check_access: Check if an envelope allows an action
    - carryall.list_vaults: List SLOS vaults (requires envelope)
    - carryall.get_metadata: Get document metadata (requires envelope)
    - carryall.read_document: Read document content (requires envelope)

    All tools require an _envelope parameter containing a valid AuthorityEnvelope.
    """

    def __init__(
        self,
        key_store: Optional[AgentKeyStore] = None,
        envelope_store: Optional[EnvelopeStore] = None,
        slos_backend: Optional[SlosBackend] = None,
        backend: Optional[Any] = None,
    ):
        self.key_store = key_store or AgentKeyStore()
        self.envelope_store = envelope_store or EnvelopeStore(
            str(Path("~/.carryall/authority.db").expanduser())
        )

        # Backend resolution: explicit backend > explicit slos_backend > env config > MemoryBackend
        if backend is not None:
            self.slos_backend = backend
        elif slos_backend is not None:
            self.slos_backend = slos_backend
        elif os.environ.get("CARRYALL_SLOS_CONFIG"):
            self.slos_backend = SlosBackend(
                config_path=os.environ["CARRYALL_SLOS_CONFIG"],
                key_store=self.key_store,
            )
        else:
            # Standalone mode — no external backend required
            logger.info("No CARRYALL_SLOS_CONFIG set. Starting with in-memory backend.")
            self.slos_backend = MemoryBackend()

        # Approval queue for cross-domain access requests
        approvals_dir = os.environ.get(
            "CARRYALL_APPROVALS_DIR",
            str(Path("~/slos/vaults/meta/approvals").expanduser()),
        )
        self.approval_queue = ApprovalQueue(approvals_dir)

        # Cache loaded envelopes
        self._envelope_cache: dict[str, AuthorityEnvelope] = {}

    def _load_envelope(self, envelope_data: dict) -> AuthorityEnvelope:
        """Load and validate an envelope from request data."""
        envelope_id = envelope_data.get("envelope_id")

        # Check cache first
        if envelope_id and envelope_id in self._envelope_cache:
            return self._envelope_cache[envelope_id]

        # Parse envelope
        envelope = AuthorityEnvelope(**envelope_data)

        # Cache it
        if envelope_id:
            self._envelope_cache[envelope_id] = envelope

        return envelope

    def _get_public_key(self, agent_id: str) -> str:
        """Get public key for an agent."""
        return self.key_store.get_public_key(agent_id)

    def _derive_scope_from_resource(self, resource: str, action: str) -> str:
        """
        Derive required scope from resource URI.

        Format: slos://vaults/<vault>/<doc> -> vault:<vault>:<action>
        """
        import re

        # Parse SLOS URI: slos://vaults/<vault>/<path>
        match = re.match(r'slos://vaults/([^/]+)(?:/.*)?', resource)
        if match:
            vault = match.group(1)
            return f"vault:{vault}:{action}"

        # Fallback to generic scope
        return f"access:{action}"

    async def _notify_approval_needed(
        self,
        request_id: str,
        agent_id: str,
        action: str,
        resource: str,
        purpose: str,
    ) -> None:
        """
        Send a Telegram notification for a pending approval request.

        Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS from environment.
        Non-blocking — failures are logged but don't block the response.
        """
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_ids = os.environ.get("TELEGRAM_CHAT_IDS", "")

        if not bot_token or not chat_ids:
            logger.warning(
                f"Approval {request_id} queued but Telegram not configured "
                "(set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS)"
            )
            return

        # Extract domain from resource URI
        domain = "unknown"
        if resource.startswith("slos://vaults/"):
            domain = resource.replace("slos://vaults/", "").split("/")[0]

        text = (
            f"\U0001F512 *Approval Required*\n\n"
            f"*Agent:* `{agent_id}`\n"
            f"*Action:* {action} `{domain}`\n"
            f"*Resource:* `{resource}`\n"
            f"*Purpose:* {purpose}\n"
            f"*Request:* `{request_id[:8]}...`\n"
            f"*Expires:* 24h"
        )

        # Inline keyboard with approve/deny buttons
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "\u2705 Approve", "callback_data": f"approve:{request_id}"},
                    {"text": "\u274C Deny", "callback_data": f"deny:{request_id}"},
                ]
            ]
        }

        import aiohttp

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        for chat_id in chat_ids.split(","):
            chat_id = chat_id.strip()
            if not chat_id:
                continue
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": json.dumps(keyboard),
            }
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            logger.warning(f"Telegram notification failed ({resp.status}): {body}")
                        else:
                            logger.info(f"Approval notification sent to chat {chat_id}")
            except Exception as e:
                logger.warning(f"Failed to send Telegram notification: {e}")

    async def handle_request(self, request: dict) -> dict:
        """Handle a JSON-RPC request."""
        rid = new_request_id()
        method = request.get("method", "")
        params = request.get("params", {})
        request_id = request.get("id")
        start = time.monotonic()

        logger.info("Handling request", extra={"method": method})

        try:
            if method == "initialize":
                result = await self._handle_initialize(params)
            elif method == "tools/list":
                result = await self._handle_list_tools(params)
            elif method == "tools/call":
                result = await self._handle_tool_call(params)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }

            duration = (time.monotonic() - start) * 1000
            logger.info("Request completed", extra={"method": method, "duration_ms": round(duration, 1)})
            return {"jsonrpc": "2.0", "id": request_id, "result": result}

        except PermissionDenied as e:
            logger.warning("Permission denied", extra={"method": method, "error": str(e)})
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": 403, "message": f"Permission denied: {e}"},
            }
        except InvalidSignature as e:
            logger.warning("Invalid signature", extra={"method": method, "error": str(e)})
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": 401, "message": f"Invalid signature: {e}"},
            }
        except EnvelopeExpired as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": 401, "message": f"Envelope expired: {e}"},
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(e)},
            }

    async def _handle_initialize(self, params: dict) -> dict:
        """Handle MCP initialize request."""
        from . import __version__
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {
                "name": "carryall",
                "version": __version__,
            },
            "capabilities": {
                "tools": {},
            },
        }

    async def _handle_list_tools(self, params: dict) -> dict:
        """List available tools."""
        return {
            "tools": [
                {
                    "name": "carryall_check_access",
                    "description": "Check if an envelope allows access to a resource",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "envelope": {
                                "type": "object",
                                "description": "The AuthorityEnvelope to validate",
                            },
                            "action": {
                                "type": "string",
                                "description": "Action to check (read, write, delete)",
                            },
                            "resource": {
                                "type": "string",
                                "description": "Resource URI (e.g., slos://vaults/finance/doc-001)",
                            },
                        },
                        "required": ["envelope", "action", "resource"],
                    },
                },
                {
                    "name": "carryall_list_vaults",
                    "description": "List available SLOS vaults",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "envelope": {
                                "type": "object",
                                "description": "The AuthorityEnvelope for authentication",
                            },
                        },
                        "required": ["envelope"],
                    },
                },
                {
                    "name": "carryall_get_metadata",
                    "description": "Get metadata for a document",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "envelope": {
                                "type": "object",
                                "description": "The AuthorityEnvelope for authentication",
                            },
                            "uri": {
                                "type": "string",
                                "description": "Document URI (e.g., slos://vaults/finance/doc-001)",
                            },
                        },
                        "required": ["envelope", "uri"],
                    },
                },
                {
                    "name": "carryall_audit_log",
                    "description": "Query the audit log",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "envelope": {
                                "type": "object",
                                "description": "The AuthorityEnvelope (requires audit:read scope)",
                            },
                            "agent_id": {
                                "type": "string",
                                "description": "Filter by agent ID (optional)",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum entries to return (default 100)",
                            },
                        },
                        "required": ["envelope"],
                    },
                },
                {
                    "name": "carryall_compile_policy",
                    "description": "Use LLM to compile natural language intent into a minimal permission envelope. This is the key differentiator - it translates 'I need to read Q4 finance report' into minimal scopes like 'vault:finance:read'.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "description": "ID of the agent requesting the envelope",
                            },
                            "intent": {
                                "type": "string",
                                "description": "Natural language description of what the agent wants to do (e.g., 'Read the Q4 finance report')",
                            },
                            "available_scopes": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of scopes the agent is allowed to request from (e.g., ['vault:finance:read', 'vault:hr:read'])",
                            },
                            "available_resources": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of resource patterns the agent can access (e.g., ['slos://vaults/finance/*'])",
                            },
                            "ttl_seconds": {
                                "type": "integer",
                                "description": "Time-to-live for the envelope in seconds (default 300)",
                            },
                            "llm_provider": {
                                "type": "string",
                                "enum": ["openai", "anthropic"],
                                "description": "LLM provider to use for policy compilation (default: openai)",
                            },
                        },
                        "required": ["agent_id", "intent", "available_scopes", "available_resources"],
                    },
                },
                {
                    "name": "carryall_read_document",
                    "description": "Read document content from a SLOS vault. Requires envelope with appropriate vault read scope. Accepts slos:// URI and automatically resolves to document UUID.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "envelope": {
                                "type": "object",
                                "description": "The AuthorityEnvelope for authentication",
                            },
                            "uri": {
                                "type": "string",
                                "description": "Document URI (e.g., slos://vaults/finance/budgets/q1-2026-budget.md)",
                            },
                            "purpose": {
                                "type": "string",
                                "description": "Why the agent needs this document (recorded in audit trail)",
                            },
                        },
                        "required": ["envelope", "uri", "purpose"],
                    },
                },
                {
                    "name": "carryall_write_document",
                    "description": "Create or update a document in a SLOS vault. Requires envelope with vault write scope for the target domain.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "envelope": {
                                "type": "object",
                                "description": "The AuthorityEnvelope for authentication",
                            },
                            "domain": {
                                "type": "string",
                                "description": "Vault domain to write to (e.g., finance, startup, personal)",
                            },
                            "content": {
                                "type": "string",
                                "description": "Document content (markdown)",
                            },
                            "title": {
                                "type": "string",
                                "description": "Document title",
                            },
                            "subpath": {
                                "type": "string",
                                "description": "Subdirectory within the vault (e.g., 'journal', 'budgets')",
                            },
                            "purpose": {
                                "type": "string",
                                "description": "Why this document is being created (recorded in audit trail)",
                            },
                            "sensitivity": {
                                "type": "string",
                                "description": "Sensitivity level: internal, confidential, or restricted (default: internal)",
                            },
                            "data_type": {
                                "type": "string",
                                "description": "Document type: note, profile, budget, project, etc. (default: note)",
                            },
                        },
                        "required": ["envelope", "domain", "content", "title", "purpose"],
                    },
                },
                {
                    "name": "carryall_query_documents",
                    "description": "Search documents within a SLOS vault domain. Requires envelope with vault read scope for the target domain.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "envelope": {
                                "type": "object",
                                "description": "The AuthorityEnvelope for authentication",
                            },
                            "domain": {
                                "type": "string",
                                "description": "Vault domain to search (e.g., finance, startup, health, personal)",
                            },
                            "query": {
                                "type": "string",
                                "description": "Search query string",
                            },
                            "include_content": {
                                "type": "boolean",
                                "description": "Include document content in results (default false)",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum results to return (default 10)",
                            },
                        },
                        "required": ["envelope", "domain", "query"],
                    },
                },
            ]
        }

    async def _handle_tool_call(self, params: dict) -> dict:
        """Handle a tool call."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "carryall_check_access":
            return await self._tool_check_access(arguments)
        elif tool_name == "carryall_list_vaults":
            return await self._tool_list_vaults(arguments)
        elif tool_name == "carryall_get_metadata":
            return await self._tool_get_metadata(arguments)
        elif tool_name == "carryall_audit_log":
            return await self._tool_audit_log(arguments)
        elif tool_name == "carryall_compile_policy":
            return await self._tool_compile_policy(arguments)
        elif tool_name == "carryall_read_document":
            return await self._tool_read_document(arguments)
        elif tool_name == "carryall_write_document":
            return await self._tool_write_document(arguments)
        elif tool_name == "carryall_query_documents":
            return await self._tool_query_documents(arguments)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    async def _tool_check_access(self, arguments: dict) -> dict:
        """Check if an envelope allows access to a resource."""
        envelope_data = arguments.get("envelope")
        action = arguments.get("action")
        resource = arguments.get("resource")

        if not envelope_data:
            raise PermissionDenied("Missing envelope")

        envelope = self._load_envelope(envelope_data)

        # Validate envelope signature
        public_key = self._get_public_key(envelope.agent_id)

        # Derive required scope from resource URI
        # Format: slos://vaults/<vault>/<doc> -> vault:<vault>:<action>
        required_scope = self._derive_scope_from_resource(resource, action)
        check_envelope(envelope, public_key, required_scope)

        # Check access via SLOS backend
        result = self.slos_backend.check_access(envelope, action, resource, mock=False)

        # Log the access check
        audit_entry = create_audit_entry(
            action=f"check_access:{action}",
            envelope=envelope,
            public_key=public_key,
            result=result.decision.value,
            resource=resource,
            reason=result.reason,
        )
        self.envelope_store.save_audit_entry(audit_entry)

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "decision": result.decision.value,
                        "reason": result.reason,
                        "metadata": result.metadata,
                    }),
                }
            ]
        }

    async def _tool_list_vaults(self, arguments: dict) -> dict:
        """List SLOS vaults."""
        envelope_data = arguments.get("envelope")

        if not envelope_data:
            raise PermissionDenied("Missing envelope")

        envelope = self._load_envelope(envelope_data)

        # Validate envelope
        public_key = self._get_public_key(envelope.agent_id)
        # List vaults requires vault:*:list or similar scope
        # For now, allow any valid envelope to list vaults

        vaults = self.slos_backend.list_vaults(envelope.agent_id, mock=False)

        # Log the action
        audit_entry = create_audit_entry(
            action="list_vaults",
            envelope=envelope,
            public_key=public_key,
            result="success",
        )
        self.envelope_store.save_audit_entry(audit_entry)

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"vaults": vaults}),
                }
            ]
        }

    async def _tool_get_metadata(self, arguments: dict) -> dict:
        """Get document metadata."""
        envelope_data = arguments.get("envelope")
        uri = arguments.get("uri")

        if not envelope_data:
            raise PermissionDenied("Missing envelope")

        envelope = self._load_envelope(envelope_data)

        # Check access first
        result = self.slos_backend.check_access(envelope, "read", uri, mock=False)

        if result.decision == Decision.DENY:
            public_key = self._get_public_key(envelope.agent_id)
            audit_entry = create_audit_entry(
                action="get_metadata",
                envelope=envelope,
                public_key=public_key,
                result="blocked",
                resource=uri,
                reason=result.reason,
            )
            self.envelope_store.save_audit_entry(audit_entry)
            raise PermissionDenied(result.reason)

        if result.decision == Decision.REQUIRE_APPROVAL:
            public_key = self._get_public_key(envelope.agent_id)
            audit_entry = create_audit_entry(
                action="get_metadata",
                envelope=envelope,
                public_key=public_key,
                result="pending_approval",
                resource=uri,
            )
            self.envelope_store.save_audit_entry(audit_entry)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "status": "require_approval",
                            "message": result.reason,
                        }),
                    }
                ]
            }

        # Get metadata
        metadata = self.slos_backend.get_metadata(uri, envelope.agent_id, mock=False)

        # Log success
        public_key = self._get_public_key(envelope.agent_id)
        audit_entry = create_audit_entry(
            action="get_metadata",
            envelope=envelope,
            public_key=public_key,
            result="success",
            resource=uri,
        )
        self.envelope_store.save_audit_entry(audit_entry)

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "uri": metadata.uri,
                        "id": metadata.id,
                        "domain": metadata.domain,
                        "sensitivity": metadata.sensitivity,
                    }),
                }
            ]
        }

    async def _tool_audit_log(self, arguments: dict) -> dict:
        """Query the audit log."""
        envelope_data = arguments.get("envelope")
        agent_id_filter = arguments.get("agent_id")
        limit = arguments.get("limit", 100)

        if not envelope_data:
            raise PermissionDenied("Missing envelope")

        envelope = self._load_envelope(envelope_data)

        # Check for audit:read scope
        if "audit:read" not in envelope.authority.scopes:
            raise PermissionDenied("Requires audit:read scope")

        entries = self.envelope_store.get_audit_trail(
            agent_id=agent_id_filter,
            limit=limit,
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"entries": entries, "count": len(entries)}),
                }
            ]
        }

    async def _tool_compile_policy(self, arguments: dict) -> dict:
        """
        Use LLM to compile natural language intent into a minimal permission envelope.

        This is the key differentiator - translates "I need to read Q4 finance report"
        into minimal scopes like "vault:finance:read" with cryptographic signing.
        """
        import os
        import uuid
        from datetime import datetime, timezone, timedelta
        from .envelope import create_envelope

        agent_id = arguments.get("agent_id")
        intent = arguments.get("intent")
        available_scopes = arguments.get("available_scopes", [])
        available_resources = arguments.get("available_resources", [])
        ttl_seconds = arguments.get("ttl_seconds", 300)
        llm_provider = arguments.get("llm_provider", "openai")

        if not agent_id or not intent:
            raise ValueError("agent_id and intent are required")

        if not available_scopes:
            raise ValueError("available_scopes cannot be empty")

        # Select LLM compiler
        if llm_provider == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set")
            compiler = AnthropicCompiler(api_key=api_key)
        else:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")
            compiler = OpenAICompiler(api_key=api_key)

        # Build minimal available skills based on scopes
        # The LLM will select which scopes are actually needed
        available_skills = self._generate_skills_from_scopes(available_scopes)

        # Create a parent authority representing the agent's maximum permissions
        parent_authority = Authority(
            scopes=available_scopes,
            resources=available_resources,
            constraints={},
        )

        # Context fields available (for now, minimal set)
        available_context_fields = ["intent", "agent_id", "timestamp"]

        # Call LLM to select minimal scopes
        selection = await compiler.select_skill(
            user_request=intent,
            current_step=1,
            parent_authority=parent_authority,
            available_context_fields=available_context_fields,
            available_skills=available_skills,
            available_scopes=available_scopes,
            temperature=0.0,
        )

        # Get or generate signing key for this agent
        try:
            signing_key = self.key_store.load_signing_key(agent_id)
        except FileNotFoundError:
            # Generate new key pair for this agent
            self.key_store.generate_keypair(agent_id)
            signing_key = self.key_store.load_signing_key(agent_id)

        # Get the private key bytes as hex for create_envelope
        private_key = bytes(signing_key).hex()

        public_key = self.key_store.get_public_key(agent_id)

        # Create the envelope with narrowed authority
        narrowed_authority = Authority(
            scopes=selection.required_scopes,
            resources=available_resources,  # Keep resource access for now
            constraints={},
        )

        narrowed_context = Context(
            included=selection.required_context_fields,
            excluded=[f for f in available_context_fields if f not in selection.required_context_fields],
            max_size_bytes=4096,
        )

        # Create default execution config for carryall
        from .types import ExecutionConfig
        default_execution = ExecutionConfig(
            provider_config={"carryall": {"llm_provider": llm_provider or "openai"}}
        )

        envelope = create_envelope(
            agent_id=agent_id,
            provider="custom",  # Use 'custom' as carryall is a custom system
            step_number=1,
            root_policy_id=str(uuid.uuid4()),
            skill=selection.selected_skill,
            authority=narrowed_authority,
            context=narrowed_context,
            execution=default_execution,
            private_key=private_key,
            ttl_seconds=ttl_seconds,
        )

        # Calculate token reduction estimate
        original_scope_count = len(available_scopes)
        narrowed_scope_count = len(selection.required_scopes)
        scope_reduction = 1.0 - (narrowed_scope_count / original_scope_count) if original_scope_count > 0 else 0.0

        # Log the compilation
        audit_entry = create_audit_entry(
            action="compile_policy",
            envelope=envelope,
            public_key=public_key,
            result="success",
            reason=selection.reasoning,
        )
        self.envelope_store.save_audit_entry(audit_entry)

        # Get metrics from compiler
        metrics = compiler.get_last_metrics()

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "envelope": envelope.model_dump(),
                        "compilation": {
                            "intent": intent,
                            "selected_scopes": selection.required_scopes,
                            "reasoning": selection.reasoning,
                            "confidence": selection.confidence,
                            "scope_reduction_ratio": scope_reduction,
                        },
                        "metrics": {
                            "input_tokens": metrics.input_tokens if metrics else None,
                            "output_tokens": metrics.output_tokens if metrics else None,
                            "cost_usd": metrics.total_cost_usd if metrics else None,
                            "latency_ms": metrics.latency_ms if metrics else None,
                        } if metrics else None,
                    }, default=str),
                }
            ]
        }

    async def _tool_read_document(self, arguments: dict) -> dict:
        """
        Read document content from SLOS with envelope enforcement.

        Accepts an slos:// URI, resolves it to a UUID via get_metadata,
        checks the envelope has the required vault:read scope,
        then reads the actual content.
        """
        envelope_data = arguments.get("envelope")
        uri = arguments.get("uri")
        purpose = arguments.get("purpose", "read")

        if not envelope_data:
            raise PermissionDenied("Missing envelope")
        if not uri:
            raise ValueError("Missing uri parameter")

        envelope = self._load_envelope(envelope_data)
        public_key = self._get_public_key(envelope.agent_id)

        # Check envelope has required scope for this vault (+ constraints)
        required_scope = self._derive_scope_from_resource(uri, "read")
        check_envelope(
            envelope, public_key, required_scope,
            action="read", resource=uri, context={"purpose": purpose},
        )

        # Resolve URI to document UUID via get_metadata
        try:
            metadata = self.slos_backend.get_metadata(uri, envelope.agent_id, mock=False)
        except Exception as e:
            raise ValueError(f"Failed to resolve URI '{uri}': {e}")

        if not metadata.id:
            raise ValueError(f"Could not resolve URI '{uri}' to a document ID")

        # Check document-level access policy
        result = self.slos_backend.check_access(envelope, "read", uri, mock=False)
        if result.decision == Decision.DENY:
            audit_entry = create_audit_entry(
                action="read_document",
                envelope=envelope,
                public_key=public_key,
                result="deny",
                resource=uri,
                reason=result.reason,
            )
            self.envelope_store.save_audit_entry(audit_entry)
            raise PermissionDenied(result.reason)

        if result.decision == Decision.REQUIRE_APPROVAL:
            # Check if there's an existing approved request for this access
            approved = self.approval_queue.find_approved(
                agent_id=envelope.agent_id, action="read", resource_uri=uri,
            )
            if not approved:
                # Queue new approval request and block access
                request_id = self.approval_queue.create_request(
                    agent_id=envelope.agent_id,
                    action="read",
                    resource_uri=uri,
                    purpose=purpose,
                )
                audit_entry = create_audit_entry(
                    action="read_document",
                    envelope=envelope,
                    public_key=public_key,
                    result="pending_approval",
                    resource=uri,
                    reason=f"Approval request {request_id}: {result.reason}",
                )
                self.envelope_store.save_audit_entry(audit_entry)

                # Send Telegram notification (non-blocking)
                await self._notify_approval_needed(
                    request_id, envelope.agent_id, "read", uri, purpose,
                )

                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "status": "requires_approval",
                                "approval_id": request_id,
                                "message": f"Cross-domain access requires human approval. "
                                           f"Request {request_id} queued for review.",
                            }),
                        }
                    ]
                }
            # If approved, fall through to read

        # Read the actual content (domain passed for decryption key lookup)
        vault_domain = metadata.domain[0] if metadata.domain else None
        content = self.slos_backend.read_document(
            metadata.id, purpose, envelope.agent_id,
            domain=vault_domain, mock=False,
        )

        # Audit the successful read
        audit_entry = create_audit_entry(
            action="read_document",
            envelope=envelope,
            public_key=public_key,
            result="success",
            resource=uri,
            reason=f"Read document {metadata.id} with purpose: {purpose}",
        )
        self.envelope_store.save_audit_entry(audit_entry)

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "uri": uri,
                        "id": metadata.id,
                        "domain": metadata.domain,
                        "sensitivity": metadata.sensitivity,
                        "document": content,
                    }, default=str),
                }
            ]
        }

    async def _tool_write_document(self, arguments: dict) -> dict:
        """
        Create or update a document in SLOS with envelope enforcement.

        Checks the envelope has vault:{domain}:write scope before writing.
        """
        envelope_data = arguments.get("envelope")
        domain = arguments.get("domain")
        content = arguments.get("content")
        title = arguments.get("title", "Untitled")
        subpath = arguments.get("subpath", "")
        purpose = arguments.get("purpose", "write")
        sensitivity = arguments.get("sensitivity", "internal")
        data_type = arguments.get("data_type", "note")

        if not envelope_data:
            raise PermissionDenied("Missing envelope")
        if not domain:
            raise ValueError("Missing domain parameter")
        if not content:
            raise ValueError("Missing content parameter")

        envelope = self._load_envelope(envelope_data)
        public_key = self._get_public_key(envelope.agent_id)

        # Check envelope has WRITE scope for this domain (+ constraints)
        required_scope = f"vault:{domain}:write"
        resource_uri = f"slos://vaults/{domain}/{subpath}" if subpath else f"slos://vaults/{domain}/"
        check_envelope(
            envelope, public_key, required_scope,
            action="write", resource=resource_uri, context={"purpose": purpose},
        )

        # Check if this write requires cross-domain approval
        # (e.g., an agent writing outside its home domain)
        agent_home = envelope.agent_id.replace("-agent", "")
        if agent_home != domain:
            approved = self.approval_queue.find_approved(
                agent_id=envelope.agent_id, action="write", resource_uri=resource_uri,
            )
            if not approved:
                request_id = self.approval_queue.create_request(
                    agent_id=envelope.agent_id,
                    action="write",
                    resource_uri=resource_uri,
                    purpose=purpose,
                    target_domain=domain,
                )
                audit_entry = create_audit_entry(
                    action="write_document",
                    envelope=envelope,
                    public_key=public_key,
                    result="pending_approval",
                    resource=resource_uri,
                    reason=f"Cross-domain write requires approval. Request: {request_id}",
                )
                self.envelope_store.save_audit_entry(audit_entry)

                await self._notify_approval_needed(
                    request_id, envelope.agent_id, "write", resource_uri, purpose,
                )

                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "status": "requires_approval",
                                "approval_id": request_id,
                                "message": f"Cross-domain write requires human approval. "
                                           f"Request {request_id} queued for review.",
                            }),
                        }
                    ]
                }

        # Build metadata for SLOS
        metadata = {
            "title": title,
            "subpath": subpath,
            "sensitivity": sensitivity,
            "data_type": data_type,
        }

        # Write via backend
        result = self.slos_backend.write_document(
            domain=domain,
            content=content,
            metadata=metadata,
            agent_id=envelope.agent_id,
        )

        # Audit the write
        audit_entry = create_audit_entry(
            action="write_document",
            envelope=envelope,
            public_key=public_key,
            result="success",
            resource=f"slos://vaults/{domain}/{subpath}",
            reason=f"Created document '{title}' with purpose: {purpose}",
        )
        self.envelope_store.save_audit_entry(audit_entry)

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "domain": domain,
                        "title": title,
                        "result": result,
                    }, default=str),
                }
            ]
        }

    async def _tool_query_documents(self, arguments: dict) -> dict:
        """
        Search documents within a SLOS vault domain with envelope enforcement.

        Checks the envelope has vault:{domain}:read scope before querying.
        """
        envelope_data = arguments.get("envelope")
        domain = arguments.get("domain")
        query = arguments.get("query")
        include_content = arguments.get("include_content", False)
        limit = arguments.get("limit", 10)

        if not envelope_data:
            raise PermissionDenied("Missing envelope")
        if not domain:
            raise ValueError("Missing domain parameter")
        if not query:
            raise ValueError("Missing query parameter")

        envelope = self._load_envelope(envelope_data)
        public_key = self._get_public_key(envelope.agent_id)

        # Check envelope has read scope for this domain
        required_scope = f"vault:{domain}:read"
        check_envelope(envelope, public_key, required_scope)

        # Query SLOS
        results = self.slos_backend.query_documents(
            domain=domain,
            query=query,
            agent_id=envelope.agent_id,
            include_content=include_content,
            limit=limit,
            mock=False,
        )

        # Audit the query
        audit_entry = create_audit_entry(
            action="query_documents",
            envelope=envelope,
            public_key=public_key,
            result="success",
            resource=f"slos://vaults/{domain}",
            reason=f"Query: '{query}' (limit={limit}, include_content={include_content})",
        )
        self.envelope_store.save_audit_entry(audit_entry)

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "domain": domain,
                        "query": query,
                        "results": results,
                    }, default=str),
                }
            ]
        }

    def _generate_skills_from_scopes(self, scopes: list[str]) -> list[Skill]:
        """Generate skill definitions from available scopes."""
        skills = []
        for i, scope in enumerate(scopes):
            # Parse scope format: namespace:resource:action
            parts = scope.split(":")
            if len(parts) >= 2:
                namespace = parts[0]
                action = parts[-1] if len(parts) > 2 else "access"
                resource = parts[1] if len(parts) > 1 else "*"

                skill = Skill(
                    id=f"skill-{namespace}-{action}",
                    name=f"{action.capitalize()} {namespace}",
                    tool=f"{namespace}_{action}",
                    description=f"Perform {action} operation on {namespace} resources",
                    parameters={
                        "allowed": [scope],
                        "constraints": {},
                    },
                )
                skills.append(skill)

        # Deduplicate by skill ID
        seen_ids = set()
        unique_skills = []
        for skill in skills:
            if skill.id not in seen_ids:
                seen_ids.add(skill.id)
                unique_skills.append(skill)

        return unique_skills if unique_skills else [
            Skill(
                id="skill-default",
                name="Default Access",
                tool="default_access",
                description="Default access skill",
                parameters={"allowed": scopes, "constraints": {}},
            )
        ]

    async def run_stdio(self):
        """Run the server using stdio transport."""
        print("Carryall MCP Server", file=sys.stderr)
        print("Listening on stdio for JSON-RPC requests...", file=sys.stderr)
        print("Press Ctrl+C to stop.", file=sys.stderr)

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, asyncio.get_event_loop())

        while True:
            try:
                line = await reader.readline()
                if not line:
                    break

                request = json.loads(line.decode())
                response = await self.handle_request(request)
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

            except json.JSONDecodeError:
                continue
            except Exception as e:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32000, "message": str(e)},
                }
                writer.write((json.dumps(error_response) + "\n").encode())
                await writer.drain()

    async def run_http(self, host: str = "0.0.0.0", port: int = 8765):
        """Run the server using HTTP transport (for Kubernetes sidecar)."""
        try:
            from aiohttp import web
        except ImportError:
            print("Error: aiohttp is required for HTTP transport.", file=sys.stderr)
            print("Install with: pip install aiohttp", file=sys.stderr)
            sys.exit(1)

        # Auth and rate limiting configuration
        api_key = os.environ.get("CARRYALL_API_KEY")
        rate_limit = int(os.environ.get("CARRYALL_RATE_LIMIT", "100"))
        rate_limiter = RateLimiter(max_requests=rate_limit)

        if not api_key:
            logger.warning("CARRYALL_API_KEY not set — HTTP endpoints are unauthenticated")

        @web.middleware
        async def auth_middleware(request: web.Request, handler):
            """Bearer token auth + rate limiting. Health endpoints bypass auth."""
            # Health endpoints always accessible
            if request.path in ("/health", "/healthz"):
                return await handler(request)

            # Rate limiting
            peer = request.remote or "unknown"
            if not rate_limiter.check(peer):
                logger.warning("Rate limit exceeded", extra={"peer": peer})
                return web.json_response(
                    {"error": "Rate limit exceeded"}, status=429
                )

            # Auth check
            if api_key:
                auth_header = request.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer ") or auth_header[7:] != api_key:
                    return web.json_response(
                        {"error": "Unauthorized", "message": "Invalid or missing API key"},
                        status=401,
                    )

            return await handler(request)

        async def health_handler(request: web.Request) -> web.Response:
            """Health check endpoint."""
            return web.json_response({"status": "healthy", "service": "carryall-mcp"})

        async def rpc_handler(request: web.Request) -> web.Response:
            """Handle JSON-RPC requests over HTTP POST."""
            try:
                body = await request.json()
                response = await self.handle_request(body)
                return web.json_response(response)
            except json.JSONDecodeError:
                return web.json_response(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                    status=400
                )
            except Exception as e:
                logger.exception("Error handling request")
                return web.json_response(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": str(e)}},
                    status=500
                )

        async def tools_handler(request: web.Request) -> web.Response:
            """REST-style endpoint to list tools."""
            response = await self.handle_request({
                "jsonrpc": "2.0",
                "id": "tools-list",
                "method": "tools/list",
                "params": {}
            })
            if "result" in response:
                return web.json_response(response["result"])
            return web.json_response(response.get("error", {}), status=500)

        async def tool_call_handler(request: web.Request) -> web.Response:
            """REST-style endpoint to call a tool."""
            tool_name = request.match_info.get("tool_name")
            try:
                body = await request.json()
            except json.JSONDecodeError:
                body = {}

            response = await self.handle_request({
                "jsonrpc": "2.0",
                "id": f"tool-call-{tool_name}",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": body
                }
            })

            if "result" in response:
                return web.json_response(response["result"])
            elif "error" in response:
                status = 403 if response["error"].get("code") in [401, 403] else 500
                return web.json_response(response["error"], status=status)
            return web.json_response(response, status=500)

        app = web.Application(middlewares=[auth_middleware])
        app.router.add_get("/health", health_handler)
        app.router.add_get("/healthz", health_handler)
        app.router.add_post("/rpc", rpc_handler)
        app.router.add_get("/tools", tools_handler)
        app.router.add_post("/tools/{tool_name}", tool_call_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)

        logger.info("Carryall MCP Server starting (HTTP)", extra={"host": host, "port": port})
        print(f"Carryall MCP Server (HTTP)", file=sys.stderr)
        print(f"Listening on http://{host}:{port}", file=sys.stderr)
        print(f"Auth: {'enabled (CARRYALL_API_KEY)' if api_key else 'DISABLED'}", file=sys.stderr)
        print(f"Rate limit: {rate_limit} req/min", file=sys.stderr)
        print(f"Endpoints:", file=sys.stderr)
        print(f"  GET  /health          - Health check", file=sys.stderr)
        print(f"  POST /rpc             - JSON-RPC endpoint", file=sys.stderr)
        print(f"  GET  /tools           - List available tools", file=sys.stderr)
        print(f"  POST /tools/<name>    - Call a tool", file=sys.stderr)
        print(f"Press Ctrl+C to stop.", file=sys.stderr)

        await site.start()

        # Graceful shutdown via signals
        shutdown_event = asyncio.Event()
        loop = asyncio.get_event_loop()

        def _signal_handler():
            logger.info("Shutdown signal received, draining connections...")
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        await shutdown_event.wait()
        logger.info("Shutting down...")
        await runner.cleanup()
        logger.info("Shutdown complete")


class RateLimiter:
    """Simple in-memory sliding-window rate limiter per IP."""

    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, ip: str) -> bool:
        now = time.monotonic()
        # Prune expired timestamps
        self._requests[ip] = [t for t in self._requests[ip] if now - t < self.window]
        if len(self._requests[ip]) >= self.max_requests:
            return False
        self._requests[ip].append(now)
        return True


def main(transport: str = "stdio", host: str = "0.0.0.0", port: int = 8765):
    """Entry point for MCP server.

    Args:
        transport: "stdio" or "http"
        host: Host to bind to (HTTP mode only)
        port: Port to listen on (HTTP mode only)
    """
    configure_logging()
    server = CarryallMCPServer()

    if transport == "http":
        asyncio.run(server.run_http(host, port))
    else:
        asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
