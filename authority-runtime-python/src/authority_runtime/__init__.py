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
    from .compiler import LLMCompiler, OpenAICompiler, AnthropicCompiler, FakeCompiler
except ImportError:
    LLMCompiler = None
    OpenAICompiler = None
    AnthropicCompiler = None
    FakeCompiler = None

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
from .skill_loader import (
    SkillManifest,
    SkillManifestError,
    load_skill,
    enforce_tool_access,
)
from .harness_audit import (
    HarnessAuditor,
    Finding,
    AuditError,
    Rule,
    RuleRegistry,
    register_rule,
    builtin_rules,
)
from .rule_packs import (
    Rule as PackRule,
    RulePack,
    RulePackError,
    RuleViolation,
    Predicate,
    PredicateRegistry,
    register_predicate,
    default_registry,
    enforces,
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

from .vault_scope import (
    VaultScope,
    create_vault_envelope,
    check_vault_access,
    enforce_envelope,
)
from .keys import AgentKeyStore
from .backends import (
    Backend,
    SlosBackend,
    MemoryBackend,
    Decision,
    PolicyResult,
    DocumentMetadata,
    load_backend,
)
from .backends.slos import parse_slos_uri

__version__ = "0.4.0"

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
    "FakeCompiler",
    # Types
    "Skill",
    "Authority",
    "Context",
    "SkillSelection",
    "SkillParameters",
    "ExecutionConfig",
    "AuthorityEnvelope",
    "DecisionContext",
    # SKILL.md loader (port from bjornswarm)
    "SkillManifest",
    "SkillManifestError",
    "load_skill",
    "enforce_tool_access",
    # Harness audit framework (port from bjornswarm)
    "HarnessAuditor",
    "Finding",
    "AuditError",
    "Rule",
    "RuleRegistry",
    "register_rule",
    "builtin_rules",
    # Rule packs: numbered hard-rule enforcement (port from bjornswarm)
    "PackRule",
    "RulePack",
    "RulePackError",
    "RuleViolation",
    "Predicate",
    "PredicateRegistry",
    "register_predicate",
    "default_registry",
    "enforces",
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
    # Vault-scoped enforcement (multi-tenant)
    "VaultScope",
    "create_vault_envelope",
    "check_vault_access",
    "enforce_envelope",
    # Backends
    "Backend",
    "SlosBackend",
    "MemoryBackend",
    "Decision",
    "PolicyResult",
    "DocumentMetadata",
    "parse_slos_uri",
    "load_backend",
]
