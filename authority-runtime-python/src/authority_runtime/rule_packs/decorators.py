"""Decorator: wrap a function so its kwargs are checked against a rule pack."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Mapping, Optional, TypeVar

from .pack import RulePack

F = TypeVar("F", bound=Callable[..., Any])


def enforces(
    pack: RulePack,
    enforcement_point: str,
    context_builder: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Callable[[F], F]:
    """Wrap a function with rule-pack enforcement.

    By default the wrapped function's kwargs become the context dict. For
    richer context, pass `context_builder(*args, **kwargs) -> Mapping[str, Any]`.

    Raises RuleViolation before the wrapped function runs if any rule fires.
    """

    def _decorate(fn: F) -> F:
        @wraps(fn)
        def _inner(*args: Any, **kwargs: Any) -> Any:
            if context_builder is not None:
                ctx = context_builder(*args, **kwargs)
            else:
                ctx = dict(kwargs)
            pack.enforce_point(enforcement_point, ctx)
            return fn(*args, **kwargs)

        return _inner  # type: ignore[return-value]

    return _decorate


__all__ = ["enforces"]
