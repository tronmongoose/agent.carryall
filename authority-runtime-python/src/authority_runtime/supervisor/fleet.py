"""FleetAdapter interface and the fleet-wide status call.

Exports FleetAdapter, FleetReport, fleet_status and to_json. A deployment
subclasses FleetAdapter to read its own sources. Sources that could not be
read go in FleetAdapter.gaps so the caller can report them instead of
rendering a quiet board.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from .contract import AgentStatus
from .derive import Observation, Thresholds, sort_key, to_status


class FleetAdapter(ABC):
    """Reads one host's sources. Must never write to any of them."""

    def __init__(self) -> None:
        self.gaps: List[str] = []

    @abstractmethod
    def observe(self, now: datetime) -> List[Observation]:
        """Return one Observation per agent, including idle and unknown ones."""


@dataclass(frozen=True)
class FleetReport:
    """Sorted statuses plus the sources that could not be read."""

    generated_at: datetime
    agents: Tuple[AgentStatus, ...]
    gaps: Tuple[str, ...]


def fleet_status(
    adapter: FleetAdapter,
    now: Optional[datetime] = None,
    limits: Optional[Thresholds] = None,
) -> FleetReport:
    """Observe, derive and sort. Duplicate agent ids are an adapter bug."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    limits = limits or Thresholds()
    observations = adapter.observe(now)
    ids = [o.agent_id for o in observations]
    if len(ids) != len(set(ids)):
        raise ValueError("adapter returned duplicate agent ids")
    statuses = sorted((to_status(o, now, limits) for o in observations), key=sort_key)
    return FleetReport(generated_at=now, agents=tuple(statuses), gaps=tuple(adapter.gaps))


def to_json(statuses: Sequence[AgentStatus], indent: Optional[int] = 2) -> str:
    """Render the contract list exactly as `supervisor status --json` emits it."""
    return json.dumps([s.to_dict() for s in statuses], indent=indent)


__all__ = ["FleetAdapter", "FleetReport", "fleet_status", "to_json"]
