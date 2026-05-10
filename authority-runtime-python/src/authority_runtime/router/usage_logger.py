"""UsageLogger: pluggable record-keeping for routing decisions.

Privacy posture: the JsonlUsageLogger writes only the query *length*, never
the body. Deployments that want richer logging supply their own UsageLogger.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .router import RouteDecision


class UsageLogger(ABC):
    @abstractmethod
    def record(self, query: str, decision: "RouteDecision") -> None: ...


class NullUsageLogger(UsageLogger):
    """Discards all records. Default when no logger is provided."""

    def record(self, query: str, decision: "RouteDecision") -> None:
        return None


class JsonlUsageLogger(UsageLogger):
    """Append one JSON record per decision to a file.

    Records: timestamp, query length (NEVER the body), tier, model, sensitivity
    level, sensitivity reasons, forced flag, reason string.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def record(self, query: str, decision: "RouteDecision") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query_len": len(query),
            "tier": decision.tier,
            "model": decision.model,
            "sensitivity_level": decision.sensitivity.level,
            "sensitivity_reasons": list(decision.sensitivity.reasons),
            "forced": decision.forced,
            "reason": decision.reason,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")


__all__ = ["UsageLogger", "NullUsageLogger", "JsonlUsageLogger"]
