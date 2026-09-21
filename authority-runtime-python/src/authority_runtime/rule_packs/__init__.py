"""
rule_packs — numbered hard-rules enforcement.

A RulePack is a deployment-supplied YAML file declaring numbered runtime
rules. Each rule names a predicate (registered separately by deployment
code) and one or more enforcement points (e.g., "pre-notify", "pre-llm-call").

When a rule fires, the enforce call raises RuleViolation with the rule's
number and description — so failures are traceable back to the deployment's
canonical rule list (e.g., bjornswarm "rule #14").

Predicate convention (matches authority_runtime.constraints): the predicate
returns None if the action is permitted, or a string explaining the violation
if it is not.

Boundary: Carryall ships the loader, registry, enforcer, decorator, the
RuleViolation type, and a builtin pack of hard deny rules (see builtin.py).
Deployments supply additional rules.yaml files and predicate functions.
"""

from .pack import (
    Rule,
    RulePack,
    RulePackError,
    RuleViolation,
)
from .registry import (
    Predicate,
    PredicateRegistry,
    register_predicate,
    default_registry,
)
from .builtin import (
    BUILTIN_RULES,
    EXEC_POINT,
    READ_POINT,
    WRITE_POINT,
    builtin_pack,
    builtin_predicates,
)
from .decorators import enforces

__all__ = [
    "Rule",
    "RulePack",
    "RulePackError",
    "RuleViolation",
    "Predicate",
    "PredicateRegistry",
    "register_predicate",
    "default_registry",
    "enforces",
    "BUILTIN_RULES",
    "EXEC_POINT",
    "READ_POINT",
    "WRITE_POINT",
    "builtin_pack",
    "builtin_predicates",
]
