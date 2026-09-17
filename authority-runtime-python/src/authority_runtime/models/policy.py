"""Pure, deterministic model resolution. No I/O except loading an explicit additions file.

Exports: ModelPolicyError, ResolvedModel, ModelPolicy.

Resolution order for a single (provider, model_id):
  1. type/shape checks            -> invalid_provider / invalid_model_id
  2. forbidden or unknown provider -> forbidden_provider / unknown_provider
  3. banned-vendor slug            -> banned_vendor   (second layer)
  4. exact (provider, id) lookup   -> not_allowlisted (primary control)
There is no normalization, alias expansion, or "closest match". Every refusal raises.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, Union

from .allowlist import (
    ALLOWED_ORIGINS,
    ALLOWLIST_VERSION,
    BUILTIN_ENTRIES,
    FORBIDDEN_PROVIDERS,
    PROVIDER_LOCALITY,
    ModelEntry,
    banned_slug,
)

ADDITIONS_ENV = "CARRYALL_MODEL_ALLOWLIST"


class ModelPolicyError(Exception):
    """A model choice was refused. Not a ValueError/OSError so generic handlers don't mask it."""

    def __init__(self, reason: str, detail: str, provider: Any = None, model_id: Any = None):
        super().__init__(f"model refused ({reason}): {detail}")
        self.reason = reason
        self.detail = detail
        self.provider = provider
        self.model_id = model_id


@dataclass(frozen=True)
class ResolvedModel:
    """An allowlisted model plus the provenance needed to audit and replay the decision."""

    provider: str
    model_id: str
    origin: str
    locality: str
    status: str
    source: str
    policy_version: str


@dataclass(frozen=True)
class ModelPolicy:
    """An immutable allowlist snapshot identified by `version`."""

    entries: Mapping[Tuple[str, str], ModelEntry]
    version: str

    @classmethod
    def builtin(cls) -> "ModelPolicy":
        """The reviewed allowlist shipped in allowlist.py, with no local additions."""
        return cls({(e.provider, e.model_id): e for e in BUILTIN_ENTRIES}, ALLOWLIST_VERSION)

    @classmethod
    def load(cls, additions_path: Union[str, Path, None] = None) -> "ModelPolicy":
        """Builtin list plus local additions from `additions_path` or $CARRYALL_MODEL_ALLOWLIST."""
        raw_path = additions_path if additions_path is not None else os.environ.get(ADDITIONS_ENV)
        if raw_path is None:
            return cls.builtin()
        path = Path(os.path.expanduser(str(raw_path)))
        try:
            data = path.read_bytes()
        except OSError as e:
            raise ModelPolicyError("additions_unreadable", f"{path}: {e}") from None
        entries = dict(cls.builtin().entries)
        for entry in _parse_additions(data):
            key = (entry.provider, entry.model_id)
            if key in entries:
                raise ModelPolicyError("additions_duplicate", f"{key} already allowlisted")
            entries[key] = entry
        digest = hashlib.sha256(data).hexdigest()[:12]
        return cls(entries, f"{ALLOWLIST_VERSION}+local:{digest}")

    def resolve(self, provider: Any, model_id: Any, *, source: str = "explicit") -> ResolvedModel:
        """Resolve one exact (provider, model_id) or raise ModelPolicyError."""
        if not isinstance(provider, str) or not provider:
            raise ModelPolicyError("invalid_provider", repr(provider), provider, model_id)
        if not isinstance(model_id, str) or not model_id:
            raise ModelPolicyError("invalid_model_id", repr(model_id), provider, model_id)
        if provider.lower() in FORBIDDEN_PROVIDERS:
            raise ModelPolicyError("forbidden_provider", provider, provider, model_id)
        if provider not in PROVIDER_LOCALITY:
            raise ModelPolicyError("unknown_provider", provider, provider, model_id)
        slug = banned_slug(model_id)
        if slug is not None:
            raise ModelPolicyError("banned_vendor", f"{model_id!r} matches {slug!r}",
                                   provider, model_id)
        entry = self.entries.get((provider, model_id))
        if entry is None:
            raise ModelPolicyError("not_allowlisted",
                                   f"{provider}/{model_id} not in allowlist {self.version}",
                                   provider, model_id)
        return ResolvedModel(entry.provider, entry.model_id, entry.origin,
                             PROVIDER_LOCALITY[provider], entry.status, source, self.version)

    def resolve_choice(self, provider: Any, *, requested: Optional[str] = None,
                       configured: Optional[str] = None, default: Any) -> ResolvedModel:
        """Precedence requested > configured > default. The first non-None value is final.

        An invalid higher-precedence value raises; it never falls through to a lower one.
        """
        for source, value in (("requested", requested), ("configured", configured)):
            if value is not None:
                return self.resolve(provider, value, source=source)
        return self.resolve(provider, default, source="default")

    def list_entries(self) -> Iterable[ModelEntry]:
        """Entries sorted by provider then model ID."""
        return sorted(self.entries.values(), key=lambda e: (e.provider, e.model_id))


def _parse_additions(data: bytes) -> Iterable[ModelEntry]:
    """Validate a local additions document. Local (ollama) models only."""
    try:
        doc = json.loads(data.decode("utf-8"))
        items = doc["models"]
    except (ValueError, KeyError, TypeError) as e:
        raise ModelPolicyError("additions_malformed", f"expected {{'models': [...]}}: {e}") from None
    if not isinstance(items, list):
        raise ModelPolicyError("additions_malformed", "'models' must be a list")
    for item in items:
        yield _parse_addition(item)


def _parse_addition(item: Dict[str, Any]) -> ModelEntry:
    """Validate one additions entry against provider, origin and banned-vendor rules."""
    if not isinstance(item, dict) or not all(
        isinstance(item.get(k), str) and item.get(k) for k in ("provider", "model_id", "origin")
    ):
        raise ModelPolicyError("additions_malformed", f"entry needs provider/model_id/origin: {item!r}")
    provider, model_id, origin = item["provider"], item["model_id"], item["origin"]
    if PROVIDER_LOCALITY.get(provider) != "local":
        raise ModelPolicyError("additions_remote_provider",
                               f"{provider!r}: only local providers may be added locally")
    if origin not in ALLOWED_ORIGINS:
        raise ModelPolicyError("origin_not_allowed", f"{model_id!r} origin {origin!r}")
    slug = banned_slug(model_id)
    if slug is not None:
        raise ModelPolicyError("banned_vendor", f"{model_id!r} matches {slug!r}", provider, model_id)
    return ModelEntry(provider, model_id, origin, "local")
