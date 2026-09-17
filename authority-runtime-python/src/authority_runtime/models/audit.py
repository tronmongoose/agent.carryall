"""Audited model resolution: the runtime shell around the pure ModelPolicy.

Exports: AUDIT_ACTION_PREFIX, resolve_audited.

Every call appends exactly one hash-chained record (allow or deny) carrying the
provider, model ID, precedence source and policy version, which is what
backtest.records_from_audit replays. If the record cannot be written the
resolution is refused.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..enforce import SystemAuditEntry
from ..storage import EnvelopeStore, default_audit_store
from .policy import ModelPolicy, ModelPolicyError, ResolvedModel

AUDIT_ACTION_PREFIX = "model:"


def resolve_audited(policy: ModelPolicy, provider: Any, *, purpose: str, default: Any,
                    requested: Optional[str] = None, configured: Optional[str] = None,
                    store: Optional[EnvelopeStore] = None,
                    note: Optional[str] = None) -> ResolvedModel:
    """Resolve via policy.resolve_choice and audit the outcome. Raises ModelPolicyError."""
    try:
        audit_store = store if store is not None else default_audit_store()
    except Exception as e:
        raise ModelPolicyError("audit_unavailable", f"{type(e).__name__}: {e}") from e
    try:
        resolved = policy.resolve_choice(provider, requested=requested, configured=configured,
                                         default=default)
    except ModelPolicyError as denied:
        chosen = next((v for v in (requested, configured) if v is not None), default)
        _write(audit_store, purpose, policy, provider, chosen, denied, None, note)
        raise
    _write(audit_store, purpose, policy, provider, resolved.model_id, None, resolved, note)
    return resolved


def _write(store: EnvelopeStore, purpose: str, policy: ModelPolicy, provider: Any, model_id: Any,
           denied: Optional[ModelPolicyError], resolved: Optional[ResolvedModel],
           note: Optional[str]) -> None:
    """Append one record; convert any storage failure into a refusal."""
    metadata: Dict[str, Any] = {
        "purpose": purpose, "provider": _text(provider), "model_id": _text(model_id),
        "policy_version": policy.version,
    }
    if resolved is not None:
        metadata.update(origin=resolved.origin, locality=resolved.locality,
                        source=resolved.source, status=resolved.status)
    if denied is not None:
        metadata["reason"] = denied.reason
    if note:
        metadata["note"] = note
    entry = SystemAuditEntry(
        action=f"{AUDIT_ACTION_PREFIX}{purpose}", subsystem="model_policy",
        result="deny" if denied else "allow", error=denied.detail[:500] if denied else None,
        resource=f"model://{metadata['provider']}/{metadata['model_id']}", metadata=metadata,
    )
    try:
        store.save_audit_entry(entry)
    except Exception as e:
        raise ModelPolicyError("audit_unavailable", f"{type(e).__name__}: {e}") from e


def _text(value: Any) -> str:
    """Render any caller-supplied value as a bounded string for the audit record."""
    return (value if isinstance(value, str) else repr(value))[:200]
