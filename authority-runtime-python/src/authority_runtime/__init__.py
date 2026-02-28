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
try:
    from .compiler import LLMCompiler, OpenAICompiler, AnthropicCompiler
except ImportError:
    LLMCompiler = None
    OpenAICompiler = None
    AnthropicCompiler = None

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
    ConstraintViolation,
    ApprovalRequired,
    AuditEntry,
    export_audit_trail,
    create_audit_entry,
)
from .constraints import check_constraints, ConstraintResult
from .storage import EnvelopeStore
from .validation import ValidationError
try:
    from .langgraph import (
        AuthorityState,
        create_authority_node,
        create_authority_graph,
    )
except ImportError:
    AuthorityState = None
    create_authority_node = None
    create_authority_graph = None

from .keys import AgentKeyStore
from .backends.slos import (
    SlosBackend,
    Decision,
    PolicyResult,
    DocumentMetadata,
    parse_slos_uri,
)
from .backends.memory import MemoryBackend

__version__ = "0.2.0"

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
    "ConstraintViolation",
    "ApprovalRequired",
    "ValidationError",
    # Constraints
    "check_constraints",
    "ConstraintResult",
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
    # Backends
    "SlosBackend",
    "MemoryBackend",
    "Decision",
    "PolicyResult",
    "DocumentMetadata",
    "parse_slos_uri",
]
