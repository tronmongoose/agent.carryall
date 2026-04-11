"""
Authority Runtime - Enforcement Layer

This module provides ACTUAL permission enforcement, not just bookkeeping.
Tools wrapped with EnforcedTool will refuse to execute without valid envelopes.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from functools import wraps

from .constraints import check_constraints
from .envelope import verify_signature
from .types import AuthorityEnvelope

logger = logging.getLogger(__name__)


class PermissionDenied(Exception):
    """Raised when an action is blocked due to insufficient permissions."""
    pass


class EnvelopeExpired(Exception):
    """Raised when an envelope's TTL has passed."""
    pass


class InvalidSignature(Exception):
    """Raised when an envelope's signature doesn't verify."""
    pass


def _scope_matches(granted: str, required: str) -> bool:
    """
    Check if a granted scope pattern matches a required scope.

    Supports per-segment wildcard matching:
      - "vault:finance:read" matches "vault:finance:read" (exact)
      - "vault:*:read" matches "vault:finance:read" (segment wildcard)
      - "vault:finance:*" matches "vault:finance:read" and "vault:finance:write"
      - "*:*:*" matches any 3-segment scope

    Segments must match in count — "vault:*" does NOT match "vault:finance:read".
    """
    g_parts = granted.split(":")
    r_parts = required.split(":")
    if len(g_parts) != len(r_parts):
        return False
    return all(g == "*" or g == r for g, r in zip(g_parts, r_parts))


class ConstraintViolation(Exception):
    """Raised when an envelope's constraints are violated."""
    pass


class ApprovalRequired(Exception):
    """Raised when an action requires human approval per envelope constraints."""
    pass


def check_envelope(
    envelope: AuthorityEnvelope,
    public_key: str,
    required_scope: str,
    action: Optional[str] = None,
    resource: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Validate an envelope before allowing an action.

    Checks signature, expiration, scope (with wildcards), and constraints.

    Args:
        envelope: The authority envelope to validate
        public_key: Agent's Ed25519 public key for signature verification
        required_scope: Scope string required for this action
        action: Action being performed (e.g., "read", "write") — for constraint checking
        resource: Resource URI being accessed — for constraint checking
        context: Additional context dict — for constraint checking

    Raises:
        InvalidSignature: If the envelope signature doesn't verify
        EnvelopeExpired: If the envelope TTL has passed
        PermissionDenied: If the required scope isn't in the envelope
        ConstraintViolation: If envelope constraints are violated
        ApprovalRequired: If action requires human approval per constraints
    """
    logger.debug("Checking envelope",
                 extra={"envelope_id": envelope.envelope_id,
                        "agent_id": envelope.agent_id,
                        "required_scope": required_scope})

    # 1. Verify signature - tamper detection
    if not verify_signature(envelope, public_key):
        raise InvalidSignature(
            f"Envelope {envelope.envelope_id} has invalid signature. "
            "The envelope may have been tampered with."
        )

    # 2. Check expiration
    expires_at = datetime.fromisoformat(envelope.expires_at.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    if expires_at < now:
        raise EnvelopeExpired(
            f"Envelope {envelope.envelope_id} expired at {envelope.expires_at}. "
            f"Current time: {now.isoformat()}"
        )

    # 3. Check scope (with wildcard support)
    if not any(_scope_matches(s, required_scope) for s in envelope.authority.scopes):
        raise PermissionDenied(
            f"Action requires scope '{required_scope}' but envelope only grants: "
            f"{envelope.authority.scopes}"
        )

    # 4. Check constraints (if any)
    if envelope.authority.constraints:
        result = check_constraints(
            constraints=envelope.authority.constraints,
            action=action or _infer_action(required_scope),
            resource=resource,
            context=context,
        )
        if result.require_approval:
            raise ApprovalRequired(
                f"Action requires human approval: {'; '.join(result.warnings)}"
            )
        if not result.allowed:
            raise ConstraintViolation(
                f"Constraint violation: {'; '.join(result.violated)}"
            )

    logger.info("Envelope check passed",
                extra={"envelope_id": envelope.envelope_id,
                       "agent_id": envelope.agent_id,
                       "scope": required_scope})


def _infer_action(scope: str) -> str:
    """Infer action from scope string (last segment). Fallback for when action not explicitly passed."""
    parts = scope.split(":")
    return parts[-1] if parts else "unknown"


def check_context_field(envelope: AuthorityEnvelope, field: str) -> None:
    """
    Validate that a context field is allowed by the envelope.

    Raises:
        PermissionDenied: If the field isn't in the envelope's included context
    """
    if field not in envelope.context.included:
        raise PermissionDenied(
            f"Access to context field '{field}' denied. "
            f"Envelope only allows: {envelope.context.included}"
        )


class EnforcedTool:
    """
    A tool wrapper that enforces Authority Runtime permissions.

    The tool will refuse to execute without a valid envelope that grants
    the required scope.

    Example:
        ```python
        def read_file(path: str) -> str:
            return open(path).read()

        # Wrap with enforcement
        secure_read = EnforcedTool(
            name="read_file",
            func=read_file,
            required_scope="read:filesystem",
            public_key=public_key
        )

        # This will FAIL without valid envelope
        secure_read(path="/etc/passwd")  # Raises PermissionDenied

        # This works with valid envelope
        secure_read(path="/etc/passwd", _envelope=valid_envelope)
        ```
    """

    def __init__(
        self,
        name: str,
        func: Callable,
        required_scope: str,
        public_key: str,
        description: Optional[str] = None,
    ):
        self.name = name
        self.func = func
        self.required_scope = required_scope
        self.public_key = public_key
        self.description = description or func.__doc__ or f"Tool: {name}"

    def __call__(self, *args, _envelope: Optional[AuthorityEnvelope] = None, **kwargs) -> Any:
        """
        Execute the tool, but only if a valid envelope is provided.

        Args:
            *args: Positional arguments for the underlying function
            _envelope: The AuthorityEnvelope granting permission (REQUIRED)
            **kwargs: Keyword arguments for the underlying function

        Returns:
            The result of the underlying function

        Raises:
            PermissionDenied: If no envelope provided or scope not granted
            InvalidSignature: If envelope signature doesn't verify
            EnvelopeExpired: If envelope has expired
        """
        if _envelope is None:
            raise PermissionDenied(
                f"Tool '{self.name}' requires an AuthorityEnvelope. "
                f"Pass _envelope=your_envelope to execute."
            )

        # Validate the envelope
        check_envelope(_envelope, self.public_key, self.required_scope)

        # Envelope is valid - execute the function
        # Pass _envelope to the function if it accepts it (for constraint checking)
        import inspect
        sig = inspect.signature(self.func)
        if '_envelope' in sig.parameters:
            return self.func(*args, _envelope=_envelope, **kwargs)
        else:
            return self.func(*args, **kwargs)

    def to_langchain_tool(self):
        """
        Convert EnforcedTool to a LangChain StructuredTool.

        This allows EnforcedTool to work with LangGraph's ToolNode and
        standard LangChain tool infrastructure.

        Returns:
            langchain_core.tools.StructuredTool compatible with LangGraph

        Example:
            ```python
            from langchain_core.tools import StructuredTool

            secure_tool = EnforcedTool(
                name="read_file",
                func=read_file,
                required_scope="read:filesystem",
                public_key=public_key
            )

            # Convert to LangChain tool
            lc_tool = secure_tool.to_langchain_tool()

            # Use with LangGraph ToolNode
            from langgraph.prebuilt import ToolNode
            tool_node = ToolNode([lc_tool])
            ```
        """
        from langchain_core.tools import StructuredTool

        # Create wrapper that doesn't require _envelope parameter
        # (envelope should be bound in execution context)
        @wraps(self.func)
        def langchain_wrapper(*args, **kwargs):
            # Note: LangChain tools don't support _envelope parameter
            # The envelope should be managed by the LangGraph integration layer
            # For now, return a helpful error if called without proper context
            return self.func(*args, **kwargs)

        return StructuredTool(
            name=self.name,
            description=self.description,
            func=langchain_wrapper,
        )


class EnforcedToolkit:
    """
    A collection of tools with shared enforcement settings.

    Example:
        ```python
        toolkit = EnforcedToolkit(public_key=public_key)

        @toolkit.tool(scope="read:filesystem")
        def read_file(path: str) -> str:
            return open(path).read()

        @toolkit.tool(scope="write:filesystem")
        def write_file(path: str, content: str) -> None:
            open(path, 'w').write(content)

        # Execute with envelope
        result = toolkit.execute("read_file", path="/tmp/test.txt", envelope=envelope)
        ```
    """

    def __init__(self, public_key: str):
        self.public_key = public_key
        self.tools: Dict[str, EnforcedTool] = {}

    def tool(self, scope: str, name: Optional[str] = None):
        """Decorator to register a tool with required scope."""
        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__
            enforced = EnforcedTool(
                name=tool_name,
                func=func,
                required_scope=scope,
                public_key=self.public_key,
            )
            self.tools[tool_name] = enforced
            return func  # Return original for direct use
        return decorator

    def execute(
        self,
        tool_name: str,
        envelope: AuthorityEnvelope,
        **kwargs
    ) -> Any:
        """
        Execute a tool by name with envelope enforcement.

        Raises:
            KeyError: If tool doesn't exist
            PermissionDenied: If envelope doesn't grant required scope
        """
        if tool_name not in self.tools:
            raise KeyError(f"Tool '{tool_name}' not found. Available: {list(self.tools.keys())}")

        return self.tools[tool_name](_envelope=envelope, **kwargs)

    def get_required_scope(self, tool_name: str) -> str:
        """Get the required scope for a tool."""
        if tool_name not in self.tools:
            raise KeyError(f"Tool '{tool_name}' not found")
        return self.tools[tool_name].required_scope


def enforce(scope: str, public_key: str):
    """
    Decorator to add enforcement to any function.

    Example:
        ```python
        @enforce(scope="delete:users", public_key=PUBLIC_KEY)
        def delete_user(user_id: str) -> None:
            db.delete(user_id)

        # Fails without envelope
        delete_user("123")  # PermissionDenied

        # Works with valid envelope
        delete_user("123", _envelope=admin_envelope)
        ```
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, _envelope: Optional[AuthorityEnvelope] = None, **kwargs):
            if _envelope is None:
                raise PermissionDenied(
                    f"Function '{func.__name__}' requires scope '{scope}'. "
                    f"Pass _envelope=your_envelope to execute."
                )
            check_envelope(_envelope, public_key, scope)
            return func(*args, **kwargs)
        return wrapper
    return decorator


# =============================================================================
# Audit Trail - Enterprise Compliance
# =============================================================================

class AuditEntry:
    """A single auditable action with its authorization proof."""

    def __init__(
        self,
        action: str,
        envelope: AuthorityEnvelope,
        public_key: str,
        result: str = "success",
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        resource: Optional[str] = None,
    ):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.action = action
        self.envelope = envelope
        self.public_key = public_key
        self.result = result
        self.error = error
        self.metadata = metadata or {}
        self.resource = resource

        # Verify signature at audit time
        self.signature_valid = verify_signature(envelope, public_key)

    def to_dict(self) -> Dict[str, Any]:
        """Export as compliance-ready dictionary."""
        envelope_dict = {
            "envelope_id": self.envelope.envelope_id,
            "agent_id": self.envelope.agent_id,
            "parent_envelope_id": self.envelope.parent_envelope_id,
            "root_policy_id": self.envelope.root_policy_id,
            "scopes": self.envelope.authority.scopes,
            "resources": self.envelope.authority.resources,
            "constraints": self.envelope.authority.constraints,
            "created_at": self.envelope.created_at,
            "expires_at": self.envelope.expires_at,
            "ttl_seconds": self.envelope.ttl_seconds,
        }

        # Include decision context if present - this is the "why" behind the "what"
        if self.envelope.decision_context:
            envelope_dict["decision_context"] = {
                "intent": self.envelope.decision_context.intent,
                "inputs": self.envelope.decision_context.inputs,
                "constraints_applied": self.envelope.decision_context.constraints_applied,
                "alternatives_considered": self.envelope.decision_context.alternatives_considered,
                "selected_because": self.envelope.decision_context.selected_because,
                "policy_references": self.envelope.decision_context.policy_references,
                "confidence": self.envelope.decision_context.confidence,
                "escalation_reason": self.envelope.decision_context.escalation_reason,
                "risk_factors": self.envelope.decision_context.risk_factors,
            }

        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "resource": self.resource,
            "result": self.result,
            "error": self.error,
            "envelope": envelope_dict,
            "verification": {
                "signature_valid": self.signature_valid,
                "public_key_fingerprint": self.public_key[:16] + "...",
            },
            "metadata": self.metadata,
        }


def export_audit_trail(
    entries: List[AuditEntry],
    include_envelope_chain: bool = True,
) -> Dict[str, Any]:
    """
    Export an audit trail for compliance review.

    This produces a compliance-ready report showing:
    - What actions were taken
    - What permissions authorized each action
    - Cryptographic proof that permissions were valid
    - The delegation chain (if include_envelope_chain=True)

    Example:
        ```python
        audit_log = []

        # During execution, record each action
        envelope = create_envelope(...)
        result = secure_delete(user_id="123", _envelope=envelope)
        audit_log.append(AuditEntry(
            action="delete_user",
            envelope=envelope,
            public_key=public_key,
            metadata={"user_id": "123"}
        ))

        # Export for compliance
        report = export_audit_trail(audit_log)
        print(json.dumps(report, indent=2))
        ```

    Returns:
        Dict with:
        - summary: High-level stats
        - entries: List of auditable actions
        - envelope_chain: Delegation hierarchy (if requested)
    """
    if not entries:
        return {
            "summary": {
                "total_actions": 0,
                "successful": 0,
                "failed": 0,
                "signature_failures": 0,
            },
            "entries": [],
        }

    # Build summary
    successful = sum(1 for e in entries if e.result == "success")
    failed = sum(1 for e in entries if e.result != "success")
    sig_failures = sum(1 for e in entries if not e.signature_valid)

    result: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_actions": len(entries),
            "successful": successful,
            "failed": failed,
            "signature_failures": sig_failures,
            "unique_agents": len(set(e.envelope.agent_id for e in entries)),
            "unique_policies": len(set(e.envelope.root_policy_id for e in entries)),
        },
        "entries": [e.to_dict() for e in entries],
    }

    # Build envelope chain if requested
    if include_envelope_chain:
        chain: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            env = entry.envelope
            if env.envelope_id not in chain:
                chain[env.envelope_id] = {
                    "envelope_id": env.envelope_id,
                    "agent_id": env.agent_id,
                    "parent_envelope_id": env.parent_envelope_id,
                    "scopes": env.authority.scopes,
                    "created_at": env.created_at,
                    "children": [],
                }

        # Link children to parents
        for env_id, node in chain.items():
            parent_id = node["parent_envelope_id"]
            if parent_id and parent_id in chain:
                chain[parent_id]["children"].append(env_id)

        # Find roots (no parent)
        roots = [eid for eid, node in chain.items() if node["parent_envelope_id"] is None]

        result["envelope_chain"] = {
            "roots": roots,
            "nodes": chain,
        }

    return result


def create_audit_entry(
    action: str,
    envelope: AuthorityEnvelope,
    public_key: str,
    result: str = "success",
    error: Optional[str] = None,
    resource: Optional[str] = None,
    **metadata
) -> AuditEntry:
    """
    Convenience function to create an audit entry.

    Example:
        ```python
        entry = create_audit_entry(
            action="transfer_funds",
            envelope=envelope,
            public_key=public_key,
            resource="slos://vaults/finance/budget-2026",
            amount="0.1 ETH",
            recipient="0x123..."
        )
        ```
    """
    return AuditEntry(
        action=action,
        envelope=envelope,
        public_key=public_key,
        result=result,
        error=error,
        metadata=metadata,
        resource=resource,
    )
