"""Predicate registry. Deployments register predicates by name; rules.yaml refers to them."""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

# A predicate evaluates a context dict and returns:
#   - None if the action is permitted
#   - A string explaining the violation if it is not
Predicate = Callable[[Mapping[str, Any]], Optional[str]]


class PredicateRegistry:
    """Name → Predicate. Deployments register; rule packs look up by name."""

    def __init__(self) -> None:
        self._predicates: Dict[str, Predicate] = {}

    def register(self, name: str, fn: Predicate) -> None:
        if not name or not isinstance(name, str):
            raise ValueError("Predicate name must be a non-empty string")
        if name in self._predicates:
            raise ValueError(f"Predicate {name!r} already registered")
        self._predicates[name] = fn

    def get(self, name: str) -> Predicate:
        if name not in self._predicates:
            raise KeyError(f"Predicate {name!r} not registered")
        return self._predicates[name]

    def has(self, name: str) -> bool:
        return name in self._predicates

    def names(self) -> list[str]:
        return list(self._predicates.keys())


_DEFAULT = PredicateRegistry()


def default_registry() -> PredicateRegistry:
    return _DEFAULT


def register_predicate(name: str) -> Callable[[Predicate], Predicate]:
    """Decorator form: @register_predicate("contains_financial_data")."""

    def _wrap(fn: Predicate) -> Predicate:
        _DEFAULT.register(name, fn)
        return fn

    return _wrap


__all__ = [
    "Predicate",
    "PredicateRegistry",
    "register_predicate",
    "default_registry",
]
