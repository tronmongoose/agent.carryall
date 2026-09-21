"""
Authority Runtime - Constraint Enforcement

Pure-function constraint checkers that evaluate an envelope's constraints dict
against the current action, resource, and context. Called by enforce.py after
scope validation passes.

Supported constraint keys:
  - require_purpose: bool — access must include a non-empty purpose string in context
  - denied_resources: list[str] — explicit deny list with glob matching (fnmatch)
  - max_records_per_request: int — limits returned record count (context must include "record_count")
  - write_requires_approval: bool — write actions return REQUIRE_APPROVAL instead of allowing
  - unconstrained: bool — must be literally True; declares "no constraints apply" so that
    an omitted or empty constraints dict can fail closed instead of skipping the gate
"""

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Dict, List, Optional

# An envelope must say something about its constraints. Bare {} is refused, so a
# deliberately unconstrained envelope has to carry this key and is greppable in the
# signed envelope and in the audit trail.
UNCONSTRAINED = "unconstrained"


@dataclass
class ConstraintResult:
    """Result of constraint evaluation."""

    allowed: bool
    violated: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    require_approval: bool = False


def check_constraints(
    constraints: Dict[str, Any],
    action: str,
    resource: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> ConstraintResult:
    """
    Evaluate all constraints against the current request.

    Returns a ConstraintResult. If any constraint is violated, allowed=False
    and violated contains human-readable descriptions of each violation.

    An empty constraints dict is refused: absence of constraints is not consent.
    Pass {"unconstrained": True} to declare explicitly that none apply.
    """
    if not constraints:
        return ConstraintResult(
            allowed=False,
            violated=[
                "Envelope declares no constraints. An empty constraints dict is refused; "
                f"set {{'{UNCONSTRAINED}': True}} to declare explicitly that none apply."
            ],
        )

    unconstrained = constraints.get(UNCONSTRAINED, False)
    if unconstrained is not True and len(constraints) == 1 and UNCONSTRAINED in constraints:
        return ConstraintResult(
            allowed=False,
            violated=[f"Constraint '{UNCONSTRAINED}' must be literally True; got {unconstrained!r}"],
        )

    ctx = context or {}
    violations: List[str] = []
    warnings: List[str] = []
    require_approval = False

    if unconstrained is True:
        warnings.append("Envelope is explicitly unconstrained")

    # Check each constraint
    if constraints.get("require_purpose"):
        v = _check_require_purpose(ctx)
        if v:
            violations.append(v)

    if "denied_resources" in constraints and resource:
        v = _check_denied_resources(constraints["denied_resources"], resource)
        if v:
            violations.append(v)

    if "max_records_per_request" in constraints:
        v = _check_max_records(constraints["max_records_per_request"], ctx)
        if v:
            violations.append(v)

    if constraints.get("write_requires_approval"):
        result = _check_write_requires_approval(action)
        if result == "require_approval":
            require_approval = True
        elif result:
            violations.append(result)

    if violations:
        return ConstraintResult(allowed=False, violated=violations, warnings=warnings)

    if require_approval:
        return ConstraintResult(
            allowed=False,
            require_approval=True,
            warnings=["Write action requires human approval"],
        )

    return ConstraintResult(allowed=True, warnings=warnings)


def _check_require_purpose(context: Dict[str, Any]) -> Optional[str]:
    """Access must include a non-empty purpose string."""
    purpose = context.get("purpose")
    if not purpose or (isinstance(purpose, str) and not purpose.strip()):
        return "Constraint 'require_purpose': access must include a non-empty purpose"
    return None


def _check_denied_resources(
    denied_patterns: List[str], resource: str
) -> Optional[str]:
    """Resource must not match any denied pattern (fnmatch glob)."""
    for pattern in denied_patterns:
        if fnmatch(resource, pattern):
            return (
                f"Constraint 'denied_resources': resource '{resource}' "
                f"matches denied pattern '{pattern}'"
            )
    return None


def _check_max_records(max_records: int, context: Dict[str, Any]) -> Optional[str]:
    """If context includes record_count, it must not exceed the limit."""
    record_count = context.get("record_count")
    if record_count is not None and record_count > max_records:
        return (
            f"Constraint 'max_records_per_request': requested {record_count} records "
            f"but limit is {max_records}"
        )
    return None


def _check_write_requires_approval(action: str) -> Optional[str]:
    """Write actions require human approval."""
    if action in ("write", "create", "update", "delete"):
        return "require_approval"
    return None
