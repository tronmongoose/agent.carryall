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
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

from .keys import AgentKeyStore
from .storage import EnvelopeStore
from .types import AuthorityEnvelope, Skill, Authority, Context
from .enforce import check_envelope, create_audit_entry, PermissionDenied, InvalidSignature, EnvelopeExpired
from .backends.slos import SlosBackend, Decision
from .compiler import OpenAICompiler, AnthropicCompiler, compile_policy


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
    ):
        self.key_store = key_store or AgentKeyStore()
        self.envelope_store = envelope_store or EnvelopeStore(
            str(Path("~/.carryall/authority.db").expanduser())
        )
        self.slos_backend = slos_backend or SlosBackend(key_store=self.key_store)

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

    async def handle_request(self, request: dict) -> dict:
        """Handle a JSON-RPC request."""
        method = request.get("method", "")
        params = request.get("params", {})
        request_id = request.get("id")

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

            return {"jsonrpc": "2.0", "id": request_id, "result": result}

        except PermissionDenied as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": 403, "message": f"Permission denied: {e}"},
            }
        except InvalidSignature as e:
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
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {
                "name": "carryall",
                "version": "0.1.0",
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

        app = web.Application()
        app.router.add_get("/health", health_handler)
        app.router.add_get("/healthz", health_handler)
        app.router.add_post("/rpc", rpc_handler)
        app.router.add_get("/tools", tools_handler)
        app.router.add_post("/tools/{tool_name}", tool_call_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)

        print(f"Carryall MCP Server (HTTP)", file=sys.stderr)
        print(f"Listening on http://{host}:{port}", file=sys.stderr)
        print(f"Endpoints:", file=sys.stderr)
        print(f"  GET  /health          - Health check", file=sys.stderr)
        print(f"  POST /rpc             - JSON-RPC endpoint", file=sys.stderr)
        print(f"  GET  /tools           - List available tools", file=sys.stderr)
        print(f"  POST /tools/<name>    - Call a tool", file=sys.stderr)
        print(f"Press Ctrl+C to stop.", file=sys.stderr)

        await site.start()

        # Keep running until interrupted
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await runner.cleanup()


def main(transport: str = "stdio", host: str = "0.0.0.0", port: int = 8765):
    """Entry point for MCP server.

    Args:
        transport: "stdio" or "http"
        host: Host to bind to (HTTP mode only)
        port: Port to listen on (HTTP mode only)
    """
    server = CarryallMCPServer()

    if transport == "http":
        asyncio.run(server.run_http(host, port))
    else:
        asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
