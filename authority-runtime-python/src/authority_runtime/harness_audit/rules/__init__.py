"""Rule protocol + registry + built-in universal rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Sequence

from ..auditor import Finding


@dataclass
class Rule:
    """A single audit rule.

    `check_fn` receives the config_root and returns an iterable of Findings.
    Rules are pure functions of the config surface — they should not write
    to disk, network, or mutate state.
    """

    id: str
    severity: str
    description: str
    check_fn: Callable[[Path], Sequence[Finding]]

    def check(self, config_root: Path) -> Sequence[Finding]:
        return self.check_fn(config_root)


class RuleRegistry:
    """In-process rule registry. Keyed by rule id."""

    def __init__(self) -> None:
        self._rules: Dict[str, Rule] = {}

    def register(self, rule: Rule) -> None:
        if rule.id in self._rules:
            raise ValueError(f"Rule {rule.id!r} already registered")
        self._rules[rule.id] = rule

    def get(self, rule_id: str) -> Rule:
        if rule_id not in self._rules:
            raise KeyError(f"Rule {rule_id!r} not registered")
        return self._rules[rule_id]

    def all(self) -> List[Rule]:
        return list(self._rules.values())


_DEFAULT_REGISTRY = RuleRegistry()


def register_rule(rule: Rule) -> None:
    """Register a rule into the default global registry."""
    _DEFAULT_REGISTRY.register(rule)


def _import_builtins() -> List[Rule]:
    from . import builtin

    return builtin.RULES


def builtin_rules() -> List[Rule]:
    """Return the list of universal rules Carryall ships."""
    return list(_import_builtins())


__all__ = [
    "Rule",
    "RuleRegistry",
    "register_rule",
    "builtin_rules",
]
