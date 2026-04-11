"""Vault-scoped enforcement primitives for multi-tenant agent deployments.

Provides VaultScope (domain-specific scope generation), envelope creation,
access checking, and a decorator for wrapping agent functions with the
envelope lifecycle.

Ported from Fast Forward agents (bjornswarm dogfood deployment).
Original: fastforward.agents/carryall/enforcement.py

Dogfooding notes preserved from the original:
- DOGFOOD-1: BUILTIN_SCOPE_RULES are generic vault patterns. Real deployments
  need client-slug-scoped rules. VaultScope builds scope strings manually.
- DOGFOOD-2: MemoryBackend is demo-only. check_vault_access() implements
  scope-checking against the envelope's granted scopes directly.
- DOGFOOD-3: No built-in decorator pattern for wrapping agent functions with
  envelope lifecycle. enforce_envelope() is that pattern — now upstream.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .envelope import create_simple_envelope, generate_key_pair


@dataclass(frozen=True)
class VaultScope:
    """Defines the read/write boundaries for an agent operating on a client vault."""

    slug: str
    read: list[str] = field(default_factory=list)
    write: list[str] = field(default_factory=list)

    @property
    def scopes(self) -> list[str]:
        """Convert to authority_runtime scope strings."""
        result = []
        for r in self.read:
            result.append(f"vault:{self.slug}:read:{r}")
        for w in self.write:
            result.append(f"vault:{self.slug}:write:{w}")
        return result

    @property
    def domains(self) -> list[str]:
        return [self.slug]

    @property
    def operations(self) -> list[str]:
        ops = []
        if self.read:
            ops.append("read")
        if self.write:
            ops.append("write")
        return ops


def create_vault_envelope(
    agent_id: str,
    scope: VaultScope,
    private_key: Any,
    ttl_seconds: int = 3600,
) -> Any:
    """Create a signed envelope scoped to a specific client vault.

    Uses authority_runtime.create_simple_envelope with vault-specific scope
    strings derived from the VaultScope.
    """
    envelope = create_simple_envelope(
        agent_id=agent_id,
        scopes=scope.scopes,
        private_key=private_key,
        skill_name=f"vault-{agent_id}",
        resources=[f"slos://vaults/{scope.slug}/"],
        ttl_seconds=ttl_seconds,
    )
    return envelope


def check_vault_access(envelope: Any, scope: VaultScope) -> dict:
    """Check whether an envelope authorizes the requested vault operations.

    Returns dict with 'allowed' (bool) and 'reason' (str).
    """
    if not envelope:
        return {"allowed": False, "reason": "No envelope provided"}

    # Check expiration (expires_at is ISO 8601 string)
    expires_at = getattr(envelope, "expires_at", None)
    if expires_at:
        try:
            expires_str = expires_at.replace("Z", "+00:00") if expires_at.endswith("Z") else expires_at
            expires_dt = datetime.fromisoformat(expires_str)
            if datetime.now(timezone.utc) > expires_dt:
                return {"allowed": False, "reason": "Envelope expired"}
        except (ValueError, AttributeError):
            return {"allowed": False, "reason": "Invalid expires_at format"}

    # Check scopes
    granted = set()
    authority = getattr(envelope, "authority", None)
    if authority:
        granted = set(getattr(authority, "scopes", []))

    required = set(scope.scopes)
    missing = required - granted
    if missing:
        return {"allowed": False, "reason": f"Missing scopes: {missing}"}

    # Check cross-client access
    for s in granted:
        parts = s.split(":")
        if len(parts) >= 2 and parts[1] != scope.slug:
            return {
                "allowed": False,
                "reason": f"Cross-client access denied: envelope for '{parts[1]}', requested '{scope.slug}'",
            }

    return {"allowed": True, "reason": "Access granted"}


def enforce_envelope(
    scope: VaultScope | Callable[..., VaultScope],
    agent_id: str = "vault-agent",
    private_key: Any = None,
    ttl: int = 3600,
    deny_cross_client: bool = True,
):
    """Decorator that wraps an agent function with Carryall envelope lifecycle.

    The pattern: create envelope -> check access -> execute -> audit.

    Args:
        scope: VaultScope instance, or a callable that extracts it from the
               decorated function's arguments (for dynamic slug resolution).
        agent_id: Identity string for the agent.
        private_key: Ed25519 private key. If None, a fresh keypair is generated
                     (useful for testing, not production).
        ttl: Envelope time-to-live in seconds.
        deny_cross_client: If True, reject envelopes that grant access to
                          a different client's vault.
    """

    def decorator(func: Callable) -> Callable:
        _cached_key = private_key
        if _cached_key is None:
            _cached_key, _ = generate_key_pair()

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            resolved_scope = scope(*args, **kwargs) if callable(scope) else scope

            envelope = create_vault_envelope(
                agent_id=agent_id,
                scope=resolved_scope,
                private_key=_cached_key,
                ttl_seconds=ttl,
            )

            access = check_vault_access(envelope, resolved_scope)
            if not access["allowed"]:
                raise PermissionError(
                    f"Carryall denied access for {agent_id} on vault "
                    f"'{resolved_scope.slug}': {access['reason']}"
                )

            kwargs["_envelope"] = envelope
            return func(*args, **kwargs)

        return wrapper

    return decorator
