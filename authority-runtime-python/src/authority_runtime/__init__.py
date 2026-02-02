"""
Authority Runtime - Python

Cross-platform IAM layer for AI agents.
"""

from .envelope import (
    create_envelope,
    create_simple_envelope,
    create_child_envelope,
    validate_envelope,
    generate_key_pair,
    verify_signature,
)
from .compiler import LLMCompiler, OpenAICompiler, AnthropicCompiler
from .types import (
    Skill,
    Authority,
    Context,
    SkillSelection,
    SkillParameters,
    ExecutionConfig,
    AuthorityEnvelope,
    DecisionContext,
)
from .enforce import (
    EnforcedTool,
    EnforcedToolkit,
    enforce,
    check_envelope,
    check_context_field,
    PermissionDenied,
    EnvelopeExpired,
    InvalidSignature,
    AuditEntry,
    export_audit_trail,
    create_audit_entry,
)
from .storage import EnvelopeStore
from .validation import ValidationError
from .langgraph import (
    AuthorityState,
    create_authority_node,
    create_authority_graph,
)
from .keys import AgentKeyStore
from .backends.slos import (
    SlosBackend,
    Decision,
    PolicyResult,
    DocumentMetadata,
    parse_slos_uri,
)

__version__ = "0.1.0"

__all__ = [
    # Envelope core
    "create_envelope",
    "create_simple_envelope",
    "create_child_envelope",
    "validate_envelope",
    "generate_key_pair",
    "verify_signature",
    # Compilers
    "LLMCompiler",
    "OpenAICompiler",
    "AnthropicCompiler",
    # Types
    "Skill",
    "Authority",
    "Context",
    "SkillSelection",
    "SkillParameters",
    "ExecutionConfig",
    "AuthorityEnvelope",
    "DecisionContext",
    # Enforcement (the real value)
    "EnforcedTool",
    "EnforcedToolkit",
    "enforce",
    "check_envelope",
    "check_context_field",
    "PermissionDenied",
    "EnvelopeExpired",
    "InvalidSignature",
    "ValidationError",
    # Audit trail (enterprise compliance)
    "AuditEntry",
    "export_audit_trail",
    "create_audit_entry",
    # Persistence (production storage)
    "EnvelopeStore",
    # LangGraph integration
    "AuthorityState",
    "create_authority_node",
    "create_authority_graph",
    # Key management
    "AgentKeyStore",
    # SLOS backend
    "SlosBackend",
    "Decision",
    "PolicyResult",
    "DocumentMetadata",
    "parse_slos_uri",
]
