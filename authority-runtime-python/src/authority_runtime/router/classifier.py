"""SensitivityClassifier: determine sensitivity level of an inbound query."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Sensitivity:
    """The classifier's verdict on a single query.

    `level` is a deployment-defined label (e.g. "public", "internal",
    "sensitive"). The Router consults a deployment-supplied mapping from
    level → tier name; Carryall does not assume any particular taxonomy.
    """

    level: str
    reasons: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.level or not isinstance(self.level, str):
            raise ValueError("Sensitivity.level must be a non-empty string")


class SensitivityClassifier(ABC):
    """Abstract sensitivity classifier. Deployments subclass."""

    @abstractmethod
    def classify(self, query: str) -> Sensitivity:
        """Return the sensitivity verdict for `query`."""


class NeverSensitiveClassifier(SensitivityClassifier):
    """Classifier that always returns the supplied default level.

    Useful for tests, public-only deployments, or as a starting point.
    """

    def __init__(self, level: str = "public") -> None:
        self._level = level

    def classify(self, query: str) -> Sensitivity:
        return Sensitivity(level=self._level, reasons=["default"])


__all__ = ["Sensitivity", "SensitivityClassifier", "NeverSensitiveClassifier"]
