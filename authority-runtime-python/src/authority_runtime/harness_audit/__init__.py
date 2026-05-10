"""
harness_audit — static config-surface audit framework.

Walks a deployment's config surface (settings.json, SKILL.md files, policy
YAML, hook scripts, etc.) and runs registered rules against it. Findings
emit to JSONL (append-only) so they can be re-processed downstream the same
way bjornswarm's sentinel findings are.

The framework ships universal rules. Deployments register their own.

Boundary: Carryall ships the auditor + the universal rules. Deployments
supply paths, deployment-specific patterns, threat models, and any rules
beyond the universal set.
"""

from .auditor import HarnessAuditor, Finding, AuditError
from .rules import Rule, RuleRegistry, register_rule, builtin_rules

__all__ = [
    "HarnessAuditor",
    "Finding",
    "AuditError",
    "Rule",
    "RuleRegistry",
    "register_rule",
    "builtin_rules",
]
