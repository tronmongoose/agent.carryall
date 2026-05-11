"""RulePack: load YAML, hold Rules, enforce at named points."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, cast

import yaml

from .registry import PredicateRegistry, default_registry


class RulePackError(ValueError):
    """Raised when a rule pack file cannot be parsed or is malformed."""


class RuleViolation(Exception):
    """Raised when a registered rule fires during enforcement.

    The structured fields are designed to be machine-readable: deployments
    can map RuleViolation instances onto their canonical rule list (e.g.,
    bjornswarm "rule #14") via `rule_number`.
    """

    def __init__(
        self,
        rule_id: str,
        rule_number: Optional[int],
        description: str,
        message: str,
        enforcement_point: str,
    ) -> None:
        self.rule_id = rule_id
        self.rule_number = rule_number
        self.description = description
        self.message = message
        self.enforcement_point = enforcement_point
        prefix = (
            f"Rule #{rule_number} ({rule_id})"
            if rule_number is not None
            else f"Rule {rule_id}"
        )
        super().__init__(f"{prefix} violated at {enforcement_point}: {message}")


@dataclass
class Rule:
    """A single numbered hard rule."""

    id: str
    description: str
    predicate: str
    enforcement: List[str] = field(default_factory=list)
    number: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise RulePackError(f"Rule id must be a non-empty string; got {self.id!r}")
        if not self.predicate or not isinstance(self.predicate, str):
            raise RulePackError(
                f"Rule {self.id!r} predicate must be a non-empty string"
            )
        if not self.enforcement:
            raise RulePackError(
                f"Rule {self.id!r} must list at least one enforcement point"
            )
        for point in self.enforcement:
            if not isinstance(point, str) or not point:
                raise RulePackError(
                    f"Rule {self.id!r} enforcement point must be a non-empty string"
                )
        if self.number is not None and not isinstance(self.number, int):
            raise RulePackError(
                f"Rule {self.id!r} number must be an int or null; got {self.number!r}"
            )


class RulePack:
    """A loaded rule pack. Look up by enforcement point and run predicates."""

    def __init__(
        self,
        rules: Iterable[Rule],
        registry: Optional[PredicateRegistry] = None,
        version: int = 1,
    ) -> None:
        self.version = version
        self._registry = registry or default_registry()
        self._rules: List[Rule] = list(rules)
        self._validate_unique_ids()
        self._by_point: dict[str, list[Rule]] = {}
        for rule in self._rules:
            for point in rule.enforcement:
                self._by_point.setdefault(point, []).append(rule)

    def _validate_unique_ids(self) -> None:
        seen: set[str] = set()
        for rule in self._rules:
            if rule.id in seen:
                raise RulePackError(f"Duplicate rule id in pack: {rule.id!r}")
            seen.add(rule.id)

    @property
    def rules(self) -> List[Rule]:
        return list(self._rules)

    def rules_for(self, enforcement_point: str) -> List[Rule]:
        return list(self._by_point.get(enforcement_point, []))

    def enforce_point(
        self, enforcement_point: str, context: Mapping[str, Any]
    ) -> None:
        """Run every rule attached to this point. Raises RuleViolation on first hit.

        Each rule's predicate must be registered; otherwise RulePackError.
        """
        rules = self.rules_for(enforcement_point)
        for rule in rules:
            if not self._registry.has(rule.predicate):
                raise RulePackError(
                    f"Rule {rule.id!r} references unregistered predicate "
                    f"{rule.predicate!r}"
                )
            predicate = self._registry.get(rule.predicate)
            result = predicate(context)
            if result is None:
                continue
            if not isinstance(result, str):
                raise RulePackError(
                    f"Predicate {rule.predicate!r} for rule {rule.id!r} returned "
                    f"{type(result).__name__}; must return Optional[str]"
                )
            raise RuleViolation(
                rule_id=rule.id,
                rule_number=rule.number,
                description=rule.description,
                message=result,
                enforcement_point=enforcement_point,
            )

    @classmethod
    def load(
        cls,
        path: Path | str,
        registry: Optional[PredicateRegistry] = None,
    ) -> "RulePack":
        """Load a YAML rule pack from disk."""
        rule_path = Path(path)
        if not rule_path.is_file():
            raise RulePackError(f"Rule pack not found: {rule_path}")
        try:
            data = yaml.safe_load(rule_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise RulePackError(f"Rule pack at {rule_path} is not valid YAML: {e}") from e
        return cls.from_dict(data, registry=registry, source=str(rule_path))

    @classmethod
    def from_dict(
        cls,
        data: Any,
        registry: Optional[PredicateRegistry] = None,
        source: str = "<dict>",
    ) -> "RulePack":
        if not isinstance(data, dict):
            raise RulePackError(f"Rule pack at {source} must be a YAML mapping")
        version = data.get("version", 1)
        if version != 1:
            raise RulePackError(
                f"Rule pack at {source} has unsupported version {version!r}"
            )
        rules_raw = data.get("rules", [])
        if not isinstance(rules_raw, list):
            raise RulePackError(f"Rule pack at {source}: `rules` must be a list")
        rules: List[Rule] = []
        for i, item in enumerate(rules_raw):
            if not isinstance(item, dict):
                raise RulePackError(
                    f"Rule pack at {source}: rule[{i}] must be a mapping"
                )
            try:
                # Rule.__post_init__ rejects empty/non-str id and predicate; pass
                # the raw values through as `str | None` and let the validator
                # produce the standard error. The cast() keeps mypy happy without
                # silently coercing — Rule's own validation is authoritative.
                rules.append(
                    Rule(
                        id=cast(str, item.get("id")),
                        description=item.get("description", ""),
                        predicate=cast(str, item.get("predicate")),
                        enforcement=list(item.get("enforcement", [])),
                        number=item.get("number"),
                    )
                )
            except RulePackError:
                raise
            except Exception as e:
                raise RulePackError(
                    f"Rule pack at {source}: rule[{i}] failed validation: {e}"
                ) from e
        return cls(rules=rules, registry=registry, version=version)


__all__ = ["Rule", "RulePack", "RulePackError", "RuleViolation"]
