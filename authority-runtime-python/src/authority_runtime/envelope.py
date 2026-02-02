"""
Authority Envelope implementation with Ed25519 signing

Port of the TypeScript envelope system to Python.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

import nacl.signing
import nacl.encoding

from .types import (
    AuthorityEnvelope,
    Authority,
    Context,
    Skill,
    SkillParameters,
    ExecutionConfig,
    NarrowingResult,
    DecisionContext,
)
from .validation import ValidationError


def generate_key_pair() -> tuple[str, str]:
    """
    Generate an Ed25519 key pair for envelope signing.

    Returns:
        Tuple of (private_key_hex, public_key_hex)
    """
    signing_key = nacl.signing.SigningKey.generate()
    verify_key = signing_key.verify_key

    private_key = signing_key.encode(encoder=nacl.encoding.HexEncoder).decode("utf-8")
    public_key = verify_key.encode(encoder=nacl.encoding.HexEncoder).decode("utf-8")

    return (private_key, public_key)


def _canonical_json(data: Dict[str, Any]) -> str:
    """
    Create canonical JSON string for deterministic signing.

    Ensures:
    - Keys are sorted alphabetically
    - No whitespace
    - Consistent serialization
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _sign_envelope(envelope_data: Dict[str, Any], private_key: str) -> str:
    """
    Sign envelope data with Ed25519 private key.

    Args:
        envelope_data: Envelope dict (signature field will be removed if present)
        private_key: Hex-encoded Ed25519 private key

    Returns:
        Hex-encoded signature
    """
    # Remove signature field if present
    data_to_sign = {k: v for k, v in envelope_data.items() if k != "signature"}

    # Create canonical JSON
    canonical = _canonical_json(data_to_sign)

    # Sign with Ed25519
    signing_key = nacl.signing.SigningKey(
        private_key, encoder=nacl.encoding.HexEncoder
    )
    signed = signing_key.sign(canonical.encode("utf-8"))

    # Return signature as hex string
    return signed.signature.hex()


def verify_signature(
    envelope: AuthorityEnvelope, public_key: str
) -> bool:
    """
    Verify envelope signature with Ed25519 public key.

    Args:
        envelope: Complete envelope with signature
        public_key: Hex-encoded Ed25519 public key

    Returns:
        True if signature is valid
    """
    # Extract signature and remove from envelope
    signature = envelope.signature
    envelope_dict = envelope.model_dump()
    del envelope_dict["signature"]

    # Create canonical JSON
    canonical = _canonical_json(envelope_dict)

    try:
        # Verify signature
        verify_key = nacl.signing.VerifyKey(
            public_key, encoder=nacl.encoding.HexEncoder
        )
        verify_key.verify(
            canonical.encode("utf-8"),
            bytes.fromhex(signature),
        )
        return True
    except nacl.exceptions.BadSignatureError:
        return False


def create_envelope(
    agent_id: str,
    provider: str,
    step_number: int,
    root_policy_id: str,
    skill: Skill,
    authority: Authority,
    context: Context,
    execution: ExecutionConfig,
    private_key: str,
    parent_envelope_id: Optional[str] = None,
    ttl_seconds: int = 300,
    decision_context: Optional["DecisionContext"] = None,
) -> AuthorityEnvelope:
    """
    Create a new authority envelope with cryptographic signature.

    Args:
        agent_id: Unique agent identifier
        provider: LLM provider ('claude', 'openai', 'gemini', 'custom')
        step_number: Step number in execution chain (1-indexed)
        root_policy_id: Root policy ID
        skill: Skill being authorized
        authority: Permission scopes
        context: Available context fields
        execution: Provider-specific execution config
        private_key: Hex-encoded Ed25519 private key for signing
        parent_envelope_id: Parent envelope ID (None for root)
        ttl_seconds: Time-to-live in seconds
        decision_context: Optional decision-time context explaining WHY this action was chosen

    Returns:
        Signed AuthorityEnvelope

    Raises:
        ValidationError: If input validation fails
    """
    # Import validation functions
    from .validation import (
        validate_agent_id,
        validate_provider,
        validate_step_number,
        validate_scopes,
        validate_resources,
        validate_ttl_seconds,
        validate_private_key,
    )

    # Validate inputs
    validate_agent_id(agent_id)
    validate_provider(provider)
    validate_step_number(step_number)
    validate_scopes(authority.scopes)
    validate_resources(authority.resources)
    validate_ttl_seconds(ttl_seconds)
    validate_private_key(private_key)

    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)

    # Create envelope without signature
    # Format timestamps without timezone offset, then add Z suffix
    created_at_str = now.replace(tzinfo=None).isoformat() + "Z"
    expires_at_str = expires.replace(tzinfo=None).isoformat() + "Z"

    envelope_data = {
        "envelope_id": f"env-{uuid.uuid4().hex[:12]}",
        "version": "1.0.0",
        "created_at": created_at_str,
        "expires_at": expires_at_str,
        "agent_id": agent_id,
        "provider": provider,
        "step_number": step_number,
        "parent_envelope_id": parent_envelope_id,
        "root_policy_id": root_policy_id,
        "skill": skill.model_dump(),
        "authority": authority.model_dump(),
        "context": context.model_dump(),
        "decision_context": decision_context.model_dump() if decision_context else None,
        "execution": execution.model_dump(),
        "ttl_seconds": ttl_seconds,
        "signature": "",  # Placeholder
    }

    # Sign envelope
    signature = _sign_envelope(envelope_data, private_key)
    envelope_data["signature"] = signature

    # Return as Pydantic model
    return AuthorityEnvelope(**envelope_data)


def narrow_authority(
    parent_envelope: AuthorityEnvelope,
    required_scopes: List[str],
    required_context_fields: List[str],
    required_resources: Optional[List[str]] = None,
) -> NarrowingResult:
    """
    Narrow authority from parent envelope to minimal required scopes and context.

    Enforces the core invariant: child authority ⊆ parent authority

    Args:
        parent_envelope: Parent envelope with broader authority
        required_scopes: Minimal scopes needed for next step
        required_context_fields: Minimal context fields needed
        required_resources: Minimal resources needed (defaults to parent's resources)

    Returns:
        NarrowingResult with narrowed authority and metrics

    Raises:
        ValueError: If required scopes/context/resources exceed parent authority
    """
    # Validate child ⊆ parent for scopes
    parent_scopes = set(parent_envelope.authority.scopes)
    required_scopes_set = set(required_scopes)

    if not required_scopes_set.issubset(parent_scopes):
        invalid = required_scopes_set - parent_scopes
        raise ValidationError(
            f"Authority narrowing failed: Required scopes {invalid} not in parent. "
            f"(Security violation - attempting privilege escalation)",
            field="required_scopes",
            value=list(invalid)
        )

    # Validate child ⊆ parent for context
    parent_context = set(parent_envelope.context.included)
    required_context_set = set(required_context_fields)

    if not required_context_set.issubset(parent_context):
        invalid = required_context_set - parent_context
        raise ValidationError(
            f"Authority narrowing failed: Required context fields {invalid} not in parent. "
            f"(Security violation - attempting to access unauthorized data)",
            field="required_context_fields",
            value=list(invalid)
        )

    # Validate child ⊆ parent for resources (CRITICAL SECURITY FIX)
    if required_resources is None:
        required_resources = parent_envelope.authority.resources
    else:
        parent_resources = set(parent_envelope.authority.resources)
        required_resources_set = set(required_resources)

        # Special case: parent has ["*"] means allow any specific resources
        if "*" not in parent_resources:
            if not required_resources_set.issubset(parent_resources):
                invalid = required_resources_set - parent_resources
                raise ValidationError(
                    f"Authority narrowing failed: Required resources {invalid} not in parent. "
                    f"(Security violation - attempting resource bypass)",
                    field="required_resources",
                    value=list(invalid)
                )

    # Calculate narrowed authority
    narrowed_authority = Authority(
        scopes=required_scopes,
        resources=required_resources,
        constraints=parent_envelope.authority.constraints,  # Inherit constraints
    )

    # Calculate narrowed context
    narrowed_context = Context(
        included=required_context_fields,
        excluded=parent_envelope.context.excluded,
        max_size_bytes=parent_envelope.context.max_size_bytes,
    )

    # Calculate reduction ratios
    authority_reduction = 1.0 - (
        len(required_scopes) / len(parent_envelope.authority.scopes)
        if len(parent_envelope.authority.scopes) > 0
        else 0.0
    )

    context_reduction = 1.0 - (
        len(required_context_fields) / len(parent_envelope.context.included)
        if len(parent_envelope.context.included) > 0
        else 0.0
    )

    return NarrowingResult(
        narrowed_authority=narrowed_authority,
        narrowed_context=narrowed_context,
        authority_reduction_ratio=authority_reduction,
        context_reduction_ratio=context_reduction,
    )


def create_simple_envelope(
    agent_id: str,
    scopes: List[str],
    private_key: str,
    skill_name: str = "default",
    resources: Optional[List[str]] = None,
    context_fields: Optional[List[str]] = None,
    provider: str = "claude",
    ttl_seconds: int = 300,
    root_policy_id: Optional[str] = None,
) -> AuthorityEnvelope:
    """
    Create an envelope with sensible defaults for common use cases.

    This is the recommended way to create root envelopes. It reduces boilerplate
    by providing defaults for skill, context, and execution configuration.

    Args:
        agent_id: Unique agent identifier (e.g., "devops-assistant-001")
        scopes: Permission scopes (e.g., ["read:logs", "execute:kubectl"])
        private_key: Hex-encoded Ed25519 private key for signing
        skill_name: Name of the skill (default: "default")
        resources: Resource patterns (default: ["*"] - all resources)
        context_fields: Context fields to include (default: ["user_id", "session_id"])
        provider: LLM provider (default: "claude")
        ttl_seconds: Time-to-live in seconds (default: 300)
        root_policy_id: Policy ID (default: auto-generated from agent_id)

    Returns:
        Signed AuthorityEnvelope ready for use

    Example:
        ```python
        private_key, public_key = generate_key_pair()

        envelope = create_simple_envelope(
            agent_id="my-agent",
            scopes=["read:files", "write:files"],
            private_key=private_key,
        )
        ```
    """
    # Apply defaults
    if resources is None:
        resources = ["*"]
    if context_fields is None:
        context_fields = ["user_id", "session_id"]
    if root_policy_id is None:
        root_policy_id = f"policy-{agent_id}"

    # Create the envelope with full API
    return create_envelope(
        agent_id=agent_id,
        provider=provider,
        step_number=1,
        root_policy_id=root_policy_id,
        skill=Skill(
            id=f"skill-{skill_name}",
            name=skill_name,
            tool=f"Tool for {skill_name}",
            parameters=SkillParameters(
                allowed=[],
                constraints={},
            ),
        ),
        authority=Authority(
            scopes=scopes,
            resources=resources,
            constraints={},
        ),
        context=Context(
            included=context_fields,
            excluded=[],
            max_size_bytes=10000,
        ),
        execution=ExecutionConfig(
            provider_config={
                provider: {"skill_name": skill_name}
            }
        ),
        private_key=private_key,
        ttl_seconds=ttl_seconds,
    )


def create_child_envelope(
    parent_envelope: AuthorityEnvelope,
    scopes: List[str],
    private_key: str,
    context_fields: Optional[List[str]] = None,
    resources: Optional[List[str]] = None,
    ttl_seconds: Optional[int] = None,
    skill_name: Optional[str] = None,
    decision_context: Optional[DecisionContext] = None,
) -> AuthorityEnvelope:
    """
    Create a child envelope with enforced authority narrowing.

    This function ALWAYS validates that child authority ⊆ parent authority.
    Unlike create_envelope(), validation is not optional - it's built in.

    Args:
        parent_envelope: Parent envelope to derive from
        scopes: Required scopes (must be subset of parent's scopes)
        private_key: Hex-encoded Ed25519 private key for signing
        context_fields: Context fields (must be subset of parent's; default: same as parent)
        resources: Resources (must be subset of parent's; default: same as parent)
        ttl_seconds: TTL in seconds (default: remaining parent TTL, never exceeds parent)
        skill_name: Skill name (default: parent's skill name)
        decision_context: Optional context explaining why this action was chosen

    Returns:
        Signed child AuthorityEnvelope

    Raises:
        ValidationError: If child authority would exceed parent authority

    Example:
        ```python
        # Parent has ["read:files", "write:files", "delete:files"]
        # Child only needs read access
        child = create_child_envelope(
            parent_envelope=parent,
            scopes=["read:files"],
            private_key=private_key,
        )
        ```
    """
    from datetime import datetime, timezone

    # Use narrow_authority to validate and compute narrowed values
    # This enforces child ⊆ parent invariant
    narrowing_result = narrow_authority(
        parent_envelope=parent_envelope,
        required_scopes=scopes,
        required_context_fields=context_fields if context_fields else parent_envelope.context.included,
        required_resources=resources,
    )

    # Calculate TTL - child cannot outlive parent
    now = datetime.now(timezone.utc)
    parent_expires_str = parent_envelope.expires_at.replace("Z", "+00:00") if parent_envelope.expires_at.endswith("Z") else parent_envelope.expires_at
    parent_expires = datetime.fromisoformat(parent_expires_str)
    remaining_seconds = int((parent_expires - now).total_seconds())

    if remaining_seconds <= 0:
        raise ValidationError(
            "Parent envelope has expired - cannot create child",
            field="parent_envelope",
            value=parent_envelope.envelope_id,
        )

    # Use provided TTL or remaining parent TTL, whichever is smaller
    if ttl_seconds is None:
        effective_ttl = remaining_seconds
    else:
        effective_ttl = min(ttl_seconds, remaining_seconds)

    # Create child envelope - use parent skill or override name
    child_skill = Skill(
        id=parent_envelope.skill.id,
        name=skill_name if skill_name else parent_envelope.skill.name,
        tool=parent_envelope.skill.tool,
        parameters=parent_envelope.skill.parameters,
    )

    return create_envelope(
        agent_id=parent_envelope.agent_id,
        provider=parent_envelope.provider,
        step_number=parent_envelope.step_number + 1,
        root_policy_id=parent_envelope.root_policy_id,
        skill=child_skill,
        authority=narrowing_result.narrowed_authority,
        context=narrowing_result.narrowed_context,
        execution=parent_envelope.execution,
        private_key=private_key,
        parent_envelope_id=parent_envelope.envelope_id,
        ttl_seconds=effective_ttl,
        decision_context=decision_context,
    )


def validate_envelope(
    envelope: AuthorityEnvelope,
    parent_envelope: Optional[AuthorityEnvelope] = None,
    public_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate an authority envelope.

    Checks:
    1. Signature validity (if public_key provided)
    2. TTL not expired
    3. Child ⊆ parent authority (if parent provided)
    4. Child ⊆ parent context (if parent provided)

    Args:
        envelope: Envelope to validate
        parent_envelope: Parent envelope (if validating child)
        public_key: Public key for signature verification

    Returns:
        Dict with validation result:
        {
            "valid": bool,
            "errors": List[str]
        }
    """
    errors: List[str] = []

    # Check signature if public key provided
    if public_key:
        if not verify_signature(envelope, public_key):
            errors.append("Invalid signature - envelope may have been tampered with")

    # Check TTL
    now = datetime.now(timezone.utc)
    # Handle both 'Z' suffix and explicit timezone
    expires_str = envelope.expires_at.replace("Z", "+00:00") if envelope.expires_at.endswith("Z") else envelope.expires_at
    expires = datetime.fromisoformat(expires_str)
    if now > expires:
        errors.append(f"Envelope expired at {envelope.expires_at}")

    # Check authority narrowing if parent provided
    if parent_envelope:
        # Check scopes
        child_scopes = set(envelope.authority.scopes)
        parent_scopes = set(parent_envelope.authority.scopes)
        if not child_scopes.issubset(parent_scopes):
            invalid = child_scopes - parent_scopes
            errors.append(
                f"Child has scopes not in parent: {invalid}. "
                f"This violates the authority narrowing invariant."
            )

        # Check context
        child_context = set(envelope.context.included)
        parent_context = set(parent_envelope.context.included)
        if not child_context.issubset(parent_context):
            invalid = child_context - parent_context
            errors.append(
                f"Child has context fields not in parent: {invalid}. "
                f"This violates the context narrowing invariant."
            )

        # Check TTL - MUST check absolute expiration times, not just ttl_seconds
        # Critical: A child created late could outlive parent with same TTL value
        child_expires_str = envelope.expires_at.replace("Z", "+00:00") if envelope.expires_at.endswith("Z") else envelope.expires_at
        parent_expires_str = parent_envelope.expires_at.replace("Z", "+00:00") if parent_envelope.expires_at.endswith("Z") else parent_envelope.expires_at

        child_expires = datetime.fromisoformat(child_expires_str)
        parent_expires = datetime.fromisoformat(parent_expires_str)

        if child_expires > parent_expires:
            errors.append(
                f"Child expires after parent "
                f"(child: {envelope.expires_at}, parent: {parent_envelope.expires_at}). "
                f"Child cannot outlive its parent."
            )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }
