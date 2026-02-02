"""
Core types for Authority Runtime - Python port of TypeScript types
"""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime
import re


class Authority(BaseModel):
    """Permission scopes granted to an agent"""

    scopes: List[str] = Field(
        description="Allowed actions (e.g., 'read:user', 'write:user')"
    )
    resources: List[str] = Field(
        default=["*"], description="Allowed resources (e.g., 'user:123', '*')"
    )
    constraints: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional constraints (NOTE: Not yet enforced in current version)",
    )

    @field_validator('scopes')
    @classmethod
    def validate_scope_format(cls, v: List[str]) -> List[str]:
        """Validate that scopes follow valid scope patterns.

        Supported patterns:
        - action:resource (e.g., 'read:user', 'write:files')
        - namespace:resource:action (e.g., 'vault:finance:read', 'wallet:transfer')
        - wildcard support (e.g., 'vault:*:read', 'read:*')
        """
        # Allow alphanumeric, underscore, hyphen, asterisk, separated by colons
        scope_pattern = re.compile(r'^[a-z0-9_*-]+(?::[a-z0-9_*-]+)+$')
        for scope in v:
            if not scope_pattern.match(scope):
                raise ValueError(
                    f"Invalid scope format: '{scope}'. "
                    f"Scopes must use colon-separated segments (e.g., 'read:user', 'vault:finance:read')"
                )
        return v


class Context(BaseModel):
    """Agent context fields available at each step"""

    included: List[str] = Field(
        description="Context fields included in this envelope (e.g., 'email', 'name')"
    )
    excluded: List[str] = Field(
        default_factory=list, description="Context fields explicitly excluded"
    )
    max_size_bytes: int = Field(
        default=10000, description="Maximum context size to prevent DoS"
    )


class SkillParameters(BaseModel):
    """Parameters that a skill accepts"""

    allowed: List[str] = Field(description="Allowed parameter names")
    constraints: Dict[str, str] = Field(
        description="Parameter type constraints (e.g., {'user_id': 'string'})"
    )


class Skill(BaseModel):
    """A skill/tool that an agent can use"""

    id: str = Field(description="Unique skill identifier")
    name: str = Field(description="Human-readable skill name (e.g., 'getUserByEmail')")
    tool: str = Field(description="Tool description for the agent")
    parameters: SkillParameters = Field(description="Skill parameters")


class DecisionContext(BaseModel):
    """
    Decision-time context - captures WHY a decision was made, not just WHAT happened.

    This bridges the gap between records and understanding:
    - Records say "User deleted"
    - Decision context says "User requested GDPR Article 17 deletion,
      alternatives (soft delete, anonymization) were considered but rejected
      because user explicitly requested permanent deletion"
    """

    intent: str = Field(
        description="What was the agent trying to accomplish? (e.g., 'User requested account deletion')"
    )

    inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="What data was available at decision time (e.g., {'user_request': 'delete my account', 'user_tier': 'premium'})"
    )

    constraints_applied: List[str] = Field(
        default_factory=list,
        description="What rules/policies limited the decision? (e.g., ['GDPR Article 17', '30-day retention policy'])"
    )

    alternatives_considered: List[str] = Field(
        default_factory=list,
        description="What other options were evaluated? (e.g., ['soft delete', 'anonymization', 'account suspension'])"
    )

    selected_because: str = Field(
        description="Why was this specific action chosen? (e.g., 'User explicitly requested permanent deletion in support ticket #12345')"
    )

    policy_references: List[str] = Field(
        default_factory=list,
        description="Which policies governed this decision? (e.g., ['privacy-policy-v2.1', 'gdpr-compliance-guide'])"
    )

    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="How certain was the decision? (0.0 = uncertain, 1.0 = certain)"
    )

    escalation_reason: Optional[str] = Field(
        default=None,
        description="If this required human review, why? (e.g., 'High-value account detected')"
    )

    risk_factors: List[str] = Field(
        default_factory=list,
        description="What risks were identified? (e.g., ['irreversible action', 'affects 3 linked accounts'])"
    )


class ExecutionConfig(BaseModel):
    """Provider-specific execution configuration"""

    provider_config: Dict[str, Dict[str, Any]] = Field(
        description="Provider-specific settings (e.g., {'claude': {'skill_name': 'root'}})"
    )


class AuthorityEnvelope(BaseModel):
    """
    Core envelope structure - cryptographically signed permission bundle.

    This is the Python port of the TypeScript AuthorityEnvelope interface.
    """

    # Identity & versioning
    envelope_id: str = Field(description="Unique envelope identifier (UUID)")
    version: str = Field(default="1.0.0", description="Envelope format version")
    created_at: str = Field(
        description="ISO 8601 timestamp of envelope creation"
    )
    expires_at: str = Field(description="ISO 8601 timestamp when envelope expires")

    # Agent context
    agent_id: str = Field(description="Unique agent identifier")
    provider: Literal["claude", "openai", "gemini", "custom"] = Field(
        description="LLM provider for this agent"
    )

    # Envelope chain (for authority narrowing)
    step_number: int = Field(description="Step in the agent's execution (1-indexed)")
    parent_envelope_id: Optional[str] = Field(
        default=None, description="Parent envelope ID (None for root)"
    )
    root_policy_id: str = Field(
        description="Root policy that started this chain"
    )

    # Core authorization
    skill: Skill = Field(description="The skill being authorized for this step")
    authority: Authority = Field(description="Permissions granted for this step")
    context: Context = Field(description="Context fields available for this step")

    # Decision-time context (optional but recommended)
    decision_context: Optional[DecisionContext] = Field(
        default=None,
        description="WHY this decision was made - captures intent, constraints, alternatives, and reasoning"
    )

    # Execution metadata
    execution: ExecutionConfig = Field(
        description="Provider-specific execution configuration"
    )

    # Cryptographic security
    signature: str = Field(
        description="Ed25519 signature of the canonical envelope (hex-encoded)"
    )

    ttl_seconds: int = Field(
        default=300,
        ge=60,  # Minimum 1 minute
        le=86400,  # Maximum 24 hours
        description="Time-to-live in seconds (default: 5 minutes, max: 24 hours)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "envelope_id": "env-123abc",
                "version": "1.0.0",
                "created_at": "2024-12-26T10:00:00Z",
                "expires_at": "2024-12-26T10:05:00Z",
                "agent_id": "agent-001",
                "provider": "claude",
                "step_number": 1,
                "parent_envelope_id": None,
                "root_policy_id": "policy-root-001",
                "skill": {
                    "id": "skill-001",
                    "name": "getUserByEmail",
                    "tool": "Searches for a user by email",
                    "parameters": {
                        "allowed": ["email"],
                        "constraints": {"email": "string"},
                    },
                },
                "authority": {
                    "scopes": ["read:user"],
                    "resources": ["*"],
                    "constraints": {},
                },
                "context": {
                    "included": ["email"],
                    "excluded": [],
                    "max_size_bytes": 10000,
                },
                "execution": {
                    "provider_config": {"claude": {"skill_name": "getUserByEmail"}}
                },
                "signature": "abcd1234...",
                "ttl_seconds": 300,
            }
        }
    )


class SkillSelection(BaseModel):
    """Result of LLM skill selection"""

    selected_skill: Skill = Field(description="The skill chosen by the LLM")
    required_scopes: List[str] = Field(
        description="Minimal scopes needed for this skill"
    )
    required_context_fields: List[str] = Field(
        description="Minimal context fields needed"
    )
    reasoning: str = Field(description="LLM's reasoning for this selection")
    confidence: float = Field(
        ge=0.0, le=1.0, description="LLM confidence score (0-1)"
    )


class NarrowingResult(BaseModel):
    """Result of authority narrowing operation"""

    narrowed_authority: Authority = Field(description="Narrowed authority scopes")
    narrowed_context: Context = Field(description="Narrowed context fields")
    authority_reduction_ratio: float = Field(
        description="Ratio of authority reduction (0-1)"
    )
    context_reduction_ratio: float = Field(
        description="Ratio of context reduction (0-1)"
    )


class TokenMetrics(BaseModel):
    """Metrics for LLM token usage and cost"""

    input_tokens: int = Field(description="Input tokens used")
    output_tokens: int = Field(description="Output tokens used")
    total_cost_usd: float = Field(description="Total cost in USD")
    latency_ms: int = Field(description="Latency in milliseconds")
