"""
Authority Runtime - Input Validation and Error Handling

This module provides comprehensive validation for envelope creation,
better error messages, and input sanitization.
"""

from typing import List, Optional, Any
import re
from .types import Authority, AuthorityEnvelope


class ValidationError(Exception):
    """Raised when validation fails with detailed error information."""

    def __init__(self, message: str, field: Optional[str] = None, value: Any = None):
        self.field = field
        self.value = value
        super().__init__(message)


def validate_agent_id(agent_id: str) -> None:
    """
    Validate agent ID format.

    Rules:
    - Not empty
    - Alphanumeric, hyphens, underscores only
    - Max 100 characters

    Raises:
        ValidationError: If agent_id is invalid
    """
    if not agent_id:
        raise ValidationError(
            "agent_id cannot be empty",
            field="agent_id",
            value=agent_id
        )

    if not isinstance(agent_id, str):
        raise ValidationError(
            f"agent_id must be a string, got {type(agent_id).__name__}",
            field="agent_id",
            value=agent_id
        )

    if len(agent_id) > 100:
        raise ValidationError(
            f"agent_id must be <= 100 characters, got {len(agent_id)}",
            field="agent_id",
            value=agent_id
        )

    if not re.match(r'^[a-zA-Z0-9_-]+$', agent_id):
        raise ValidationError(
            "agent_id must contain only alphanumeric characters, hyphens, and underscores",
            field="agent_id",
            value=agent_id
        )


def validate_provider(provider: str) -> None:
    """
    Validate provider name.

    Args:
        provider: LLM provider name

    Raises:
        ValidationError: If provider is invalid
    """
    if not provider:
        raise ValidationError(
            "provider cannot be empty",
            field="provider",
            value=provider
        )

    if not isinstance(provider, str):
        raise ValidationError(
            f"provider must be a string, got {type(provider).__name__}",
            field="provider",
            value=provider
        )

    # Match Pydantic's Literal type constraint in types.py
    valid_providers = {'openai', 'claude', 'gemini', 'custom'}
    if provider.lower() not in valid_providers:
        raise ValidationError(
            f"Unknown provider '{provider}'. Valid providers: {', '.join(sorted(valid_providers))}. "
            f"Use 'custom' for other providers (e.g., Anthropic, Bedrock, Azure).",
            field="provider",
            value=provider
        )


def validate_step_number(step_number: int) -> None:
    """
    Validate step number.

    Args:
        step_number: Step number in execution chain

    Raises:
        ValidationError: If step_number is invalid
    """
    if not isinstance(step_number, int):
        raise ValidationError(
            f"step_number must be an integer, got {type(step_number).__name__}",
            field="step_number",
            value=step_number
        )

    if step_number < 0:
        raise ValidationError(
            f"step_number must be >= 0, got {step_number}",
            field="step_number",
            value=step_number
        )

    if step_number > 10000:
        raise ValidationError(
            f"step_number must be <= 10000, got {step_number}. "
            "This likely indicates an infinite loop.",
            field="step_number",
            value=step_number
        )


def validate_scopes(scopes: List[str]) -> None:
    """
    Validate permission scopes.

    Args:
        scopes: List of permission scopes

    Raises:
        ValidationError: If scopes are invalid
    """
    if not isinstance(scopes, list):
        raise ValidationError(
            f"scopes must be a list, got {type(scopes).__name__}",
            field="scopes",
            value=scopes
        )

    if not scopes:
        raise ValidationError(
            "scopes cannot be empty. Every envelope must grant at least one permission.",
            field="scopes",
            value=scopes
        )

    for i, scope in enumerate(scopes):
        if not isinstance(scope, str):
            raise ValidationError(
                f"scope at index {i} must be a string, got {type(scope).__name__}",
                field=f"scopes[{i}]",
                value=scope
            )

        if not scope:
            raise ValidationError(
                f"scope at index {i} cannot be empty",
                field=f"scopes[{i}]",
                value=scope
            )

        # Validate scope format - allow multiple colon-separated segments
        # Supported patterns:
        # - action:resource (e.g., 'read:users', 'write:data')
        # - namespace:resource:action (e.g., 'vault:finance:read', 'wallet:transfer')
        if ':' in scope:
            parts = scope.split(':')
            if len(parts) < 2:
                raise ValidationError(
                    f"Scope '{scope}' has invalid format. Expected 'action:resource' or 'namespace:resource:action'",
                    field=f"scopes[{i}]",
                    value=scope
                )

            # Check that no part is empty
            for j, part in enumerate(parts):
                if not part:
                    raise ValidationError(
                        f"Scope '{scope}' has empty segment at position {j}",
                        field=f"scopes[{i}]",
                        value=scope
                    )


def validate_resources(resources: List[str]) -> None:
    """
    Validate resource patterns.

    Args:
        resources: List of resource patterns

    Raises:
        ValidationError: If resources are invalid
    """
    if not isinstance(resources, list):
        raise ValidationError(
            f"resources must be a list, got {type(resources).__name__}",
            field="resources",
            value=resources
        )

    if not resources:
        raise ValidationError(
            "resources cannot be empty. Use ['*'] to grant access to all resources.",
            field="resources",
            value=resources
        )

    for i, resource in enumerate(resources):
        if not isinstance(resource, str):
            raise ValidationError(
                f"resource at index {i} must be a string, got {type(resource).__name__}",
                field=f"resources[{i}]",
                value=resource
            )

        if not resource:
            raise ValidationError(
                f"resource at index {i} cannot be empty",
                field=f"resources[{i}]",
                value=resource
            )


def validate_ttl_seconds(ttl_seconds: int, parent_ttl: Optional[int] = None) -> None:
    """
    Validate TTL (time-to-live).

    Args:
        ttl_seconds: TTL in seconds
        parent_ttl: Parent envelope TTL (if this is a child envelope)

    Raises:
        ValidationError: If TTL is invalid
    """
    if not isinstance(ttl_seconds, int):
        raise ValidationError(
            f"ttl_seconds must be an integer, got {type(ttl_seconds).__name__}",
            field="ttl_seconds",
            value=ttl_seconds
        )

    if ttl_seconds < 60:
        raise ValidationError(
            f"ttl_seconds must be >= 60 (1 minute), got {ttl_seconds}. "
            "Short-lived envelopes are a security risk.",
            field="ttl_seconds",
            value=ttl_seconds
        )

    if ttl_seconds > 86400:
        raise ValidationError(
            f"ttl_seconds must be <= 86400 (24 hours), got {ttl_seconds}. "
            "Long-lived envelopes are a security risk.",
            field="ttl_seconds",
            value=ttl_seconds
        )

    # Validate child TTL <= parent TTL
    if parent_ttl is not None:
        if ttl_seconds > parent_ttl:
            raise ValidationError(
                f"Child envelope TTL ({ttl_seconds}s) cannot exceed parent TTL ({parent_ttl}s). "
                "Children must have equal or shorter lifetimes than their parents.",
                field="ttl_seconds",
                value=ttl_seconds
            )


def validate_private_key(private_key: str) -> None:
    """
    Validate Ed25519 private key format.

    Args:
        private_key: Hex-encoded Ed25519 private key

    Raises:
        ValidationError: If private key is invalid
    """
    if not isinstance(private_key, str):
        raise ValidationError(
            f"private_key must be a string, got {type(private_key).__name__}",
            field="private_key",
            value="<redacted>"
        )

    if not private_key:
        raise ValidationError(
            "private_key cannot be empty",
            field="private_key",
            value="<redacted>"
        )

    # Ed25519 private key is 32 bytes = 64 hex characters
    if len(private_key) != 64:
        raise ValidationError(
            f"private_key must be 64 hex characters (32 bytes), got {len(private_key)}. "
            "Use generate_key_pair() to create valid keys.",
            field="private_key",
            value="<redacted>"
        )

    # Validate hex encoding
    try:
        int(private_key, 16)
    except ValueError:
        raise ValidationError(
            "private_key must be hex-encoded (0-9, a-f). "
            "Use generate_key_pair() to create valid keys.",
            field="private_key",
            value="<redacted>"
        )


def validate_authority_narrowing(
    parent_authority: Authority,
    child_scopes: List[str],
    child_resources: List[str]
) -> None:
    """
    Validate that child authority is a proper subset of parent authority.

    This enforces the core security invariant: children cannot have MORE permissions than parents.

    Args:
        parent_authority: Parent envelope's authority
        child_scopes: Child envelope's requested scopes
        child_resources: Child envelope's requested resources

    Raises:
        ValidationError: If child authority exceeds parent authority
    """
    # Validate scopes: child ⊆ parent
    parent_scopes_set = set(parent_authority.scopes)
    child_scopes_set = set(child_scopes)

    if not child_scopes_set.issubset(parent_scopes_set):
        invalid_scopes = child_scopes_set - parent_scopes_set
        raise ValidationError(
            f"Child envelope has scopes not granted by parent: {sorted(invalid_scopes)}. "
            f"Parent grants: {sorted(parent_authority.scopes)}. "
            f"Child requested: {sorted(child_scopes)}. "
            "Children can only narrow permissions, not expand them.",
            field="authority.scopes",
            value=child_scopes
        )

    # Validate resources: For now, we allow any subset
    # More sophisticated validation could check resource patterns
    if '*' not in parent_authority.resources:
        # Parent has specific resources, validate child doesn't exceed
        parent_resources_set = set(parent_authority.resources)
        child_resources_set = set(child_resources)

        # If child requests specific resources, they must be in parent's list
        if '*' not in child_resources:
            if not child_resources_set.issubset(parent_resources_set):
                invalid_resources = child_resources_set - parent_resources_set
                raise ValidationError(
                    f"Child envelope has resources not granted by parent: {sorted(invalid_resources)}. "
                    f"Parent grants: {sorted(parent_authority.resources)}. "
                    f"Child requested: {sorted(child_resources)}.",
                    field="authority.resources",
                    value=child_resources
                )


def validate_envelope_for_execution(
    envelope: AuthorityEnvelope,
    required_scope: str
) -> List[str]:
    """
    Validate that an envelope is suitable for executing an action.

    Returns list of validation warnings (non-fatal issues).

    Args:
        envelope: Envelope to validate
        required_scope: Required scope for the action

    Returns:
        List of warning messages (empty if no warnings)

    Raises:
        ValidationError: If envelope is invalid for execution
    """
    warnings = []

    # Check if envelope is expiring soon (within 5% of TTL)
    from datetime import datetime, timezone
    import dateutil.parser

    expires_at = dateutil.parser.isoparse(envelope.expires_at)
    now = datetime.now(timezone.utc)
    time_remaining = (expires_at - now).total_seconds()

    if time_remaining < 0:
        raise ValidationError(
            f"Envelope {envelope.envelope_id} has expired. "
            f"Expired at: {envelope.expires_at}, Current time: {now.isoformat()}",
            field="expires_at",
            value=envelope.expires_at
        )

    if time_remaining < envelope.ttl_seconds * 0.05:  # Less than 5% remaining
        warnings.append(
            f"Envelope {envelope.envelope_id} is expiring soon "
            f"({time_remaining:.0f}s remaining)"
        )

    # Check if required scope is granted
    if required_scope not in envelope.authority.scopes:
        raise ValidationError(
            f"Envelope {envelope.envelope_id} does not grant required scope '{required_scope}'. "
            f"Granted scopes: {envelope.authority.scopes}",
            field="authority.scopes",
            value=envelope.authority.scopes
        )

    return warnings
