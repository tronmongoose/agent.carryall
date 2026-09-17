"""ModelRegistry: tiers, models, origins, sensitivity-level → tier mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Mapping

if TYPE_CHECKING:
    from ..models import ModelPolicy


@dataclass(frozen=True)
class Tier:
    """A registered model tier."""

    name: str
    model: str
    origin: str  # e.g. "Anthropic", "Google", "Mistral", "Meta", "Microsoft"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    provider: str = ""  # "anthropic" or "ollama"; required for the tier to pass policy


class ModelRegistry:
    """In-process registry of tiers and the sensitivity → tier map."""

    def __init__(self) -> None:
        self._tiers: Dict[str, Tier] = {}
        self._sensitivity_map: Dict[str, str] = {}

    def add_tier(
        self,
        name: str,
        model: str,
        origin: str,
        provider: str = "",
        **metadata: Any,
    ) -> Tier:
        if not name or not isinstance(name, str):
            raise ValueError("Tier name must be a non-empty string")
        if name in self._tiers:
            raise ValueError(f"Tier {name!r} already registered")
        if not model or not isinstance(model, str):
            raise ValueError(f"Tier {name!r} model must be a non-empty string")
        if not origin or not isinstance(origin, str):
            raise ValueError(f"Tier {name!r} origin must be a non-empty string")
        tier = Tier(name=name, model=model, origin=origin, metadata=dict(metadata),
                    provider=provider)
        self._tiers[name] = tier
        return tier

    def map_sensitivity(self, sensitivity_level: str, tier_name: str) -> None:
        if tier_name not in self._tiers:
            raise KeyError(f"Tier {tier_name!r} not registered")
        if not sensitivity_level or not isinstance(sensitivity_level, str):
            raise ValueError("Sensitivity level must be a non-empty string")
        self._sensitivity_map[sensitivity_level] = tier_name

    def tier(self, name: str) -> Tier:
        if name not in self._tiers:
            raise KeyError(f"Tier {name!r} not registered")
        return self._tiers[name]

    def tier_for_sensitivity(self, level: str) -> Tier:
        if level not in self._sensitivity_map:
            raise KeyError(f"No tier mapped for sensitivity level {level!r}")
        return self._tiers[self._sensitivity_map[level]]

    def tiers(self) -> List[Tier]:
        return list(self._tiers.values())

    def tiers_with_origin(self, origin: str) -> List[Tier]:
        return [t for t in self._tiers.values() if t.origin == origin]

    def assert_origins_allowed(self, policy: "ModelPolicy") -> None:
        """Raise unless every tier's (provider, model) is allowlisted and its origin matches.

        The origin label is caller-supplied text, so it is checked against the origin the
        allowlist records for that exact model rather than trusted on its own.
        Router calls this at construction; see Router.__init__.
        """
        for t in self._tiers.values():
            resolved = policy.resolve(t.provider, t.model)
            if t.origin != resolved.origin:
                raise ValueError(
                    f"Tier {t.name!r} claims origin {t.origin!r} but allowlist "
                    f"{policy.version} records {resolved.origin!r} for {t.model!r}"
                )

__all__ = ["ModelRegistry", "Tier"]
