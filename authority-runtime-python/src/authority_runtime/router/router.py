"""Router: combine classifier + registry + logger to make routing decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .classifier import Sensitivity, SensitivityClassifier
from .registry import ModelRegistry, Tier
from .usage_logger import NullUsageLogger, UsageLogger


class RouteError(RuntimeError):
    """Raised when routing cannot produce a decision."""


@dataclass(frozen=True)
class RouteDecision:
    """The Router's decision for a single query."""

    tier: str
    model: str
    sensitivity: Sensitivity
    reason: str
    forced: bool = False


class Router:
    """Sensitivity-aware tiered routing primitive.

    Composition: classifier produces a Sensitivity; registry maps that
    level to a Tier; logger records the decision. force_tier overrides
    the classification path but is still logged with `forced=True` so
    overrides are auditable.
    """

    def __init__(
        self,
        classifier: SensitivityClassifier,
        registry: ModelRegistry,
        logger: Optional[UsageLogger] = None,
    ) -> None:
        self.classifier = classifier
        self.registry = registry
        self.logger = logger or NullUsageLogger()

    def route(
        self,
        query: str,
        *,
        force_tier: Optional[str] = None,
    ) -> RouteDecision:
        if not isinstance(query, str):
            raise RouteError("query must be a string")
        sensitivity = self.classifier.classify(query)

        tier: Tier
        if force_tier is not None:
            try:
                tier = self.registry.tier(force_tier)
            except KeyError as e:
                raise RouteError(str(e)) from e
            decision = RouteDecision(
                tier=tier.name,
                model=tier.model,
                sensitivity=sensitivity,
                reason=f"forced tier={force_tier}",
                forced=True,
            )
        else:
            try:
                tier = self.registry.tier_for_sensitivity(sensitivity.level)
            except KeyError as e:
                raise RouteError(str(e)) from e
            decision = RouteDecision(
                tier=tier.name,
                model=tier.model,
                sensitivity=sensitivity,
                reason=f"sensitivity={sensitivity.level}",
                forced=False,
            )
        self.logger.record(query, decision)
        return decision


__all__ = ["Router", "RouteDecision", "RouteError"]
