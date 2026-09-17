"""models: exact-ID model allowlist, deterministic resolution, audit, and backtesting.

Exports: ALLOWLIST_VERSION, ModelPolicy, ModelPolicyError, ResolvedModel, banned_slug,
resolve_audited.

The allowlist in allowlist.py is the primary control; the banned-vendor slug check is a
second layer. ModelPolicy is pure so decisions can be replayed (see backtest.py);
resolve_audited is the call-site entry point that records every decision.
"""

from .allowlist import ALLOWLIST_VERSION, banned_slug
from .audit import resolve_audited
from .policy import ModelPolicy, ModelPolicyError, ResolvedModel

__all__ = [
    "ALLOWLIST_VERSION",
    "ModelPolicy",
    "ModelPolicyError",
    "ResolvedModel",
    "banned_slug",
    "resolve_audited",
]
