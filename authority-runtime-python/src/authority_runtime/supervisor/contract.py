"""AgentStatus: the one record a supervisor emits per agent.

Exports the contract types (AgentStatus and its parts), the State and Flag
vocabularies, and to_dict for JSON rendering. Datetimes are timezone-aware
and render as ISO 8601. A field with no source renders as null, never a guess.

Kept Python 3.9-compatible: deployments import this on 3.9 venvs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Literal, Optional, Tuple

State = Literal["running", "idle", "scheduled", "blocked_on_human", "failed", "unknown"]
STATES: Tuple[str, ...] = (
    "running", "idle", "scheduled", "blocked_on_human", "failed", "unknown",
)

Flag = Literal["stale_heartbeat", "repeated_failure", "approval_aging", "over_budget"]
FLAGS: Tuple[str, ...] = (
    "stale_heartbeat", "repeated_failure", "approval_aging", "over_budget",
)

Host = Literal["home", "work"]


def _iso(value: Optional[datetime]) -> Optional[str]:
    """Render an aware datetime as ISO 8601, or None."""
    return value.isoformat() if value is not None else None


@dataclass(frozen=True)
class TaskRef:
    """The task an agent holds right now, as named by its tracker."""

    id: str
    title: str
    source: str
    url: Optional[str] = None


@dataclass(frozen=True)
class ActionRef:
    """One action, summarized to a single line."""

    at: datetime
    summary: str


@dataclass(frozen=True)
class ApprovalRef:
    """A request waiting on the operator, with where it gets decided today."""

    id: str
    summary: str
    requested_at: datetime


@dataclass(frozen=True)
class ArtifactRef:
    """A file or URL an agent produced."""

    path_or_url: str
    at: datetime


@dataclass(frozen=True)
class Spend:
    """Spend so far today. None means no source attributes spend to this agent."""

    tokens: Optional[int] = None
    usd: Optional[float] = None


@dataclass(frozen=True)
class AgentStatus:
    """One card on the fleet board."""

    agent_id: str
    display_name: str
    host: Host
    state: State
    current_task: Optional[TaskRef] = None
    last_heartbeat: Optional[datetime] = None
    last_action: Optional[ActionRef] = None
    pending_approvals: Tuple[ApprovalRef, ...] = ()
    next_scheduled_run: Optional[datetime] = None
    artifacts_recent: Tuple[ArtifactRef, ...] = ()
    spend_today: Spend = field(default_factory=Spend)
    flags: Tuple[Flag, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        """Render to the JSON shape of the AgentStatus contract."""
        task = self.current_task
        action = self.last_action
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "host": self.host,
            "state": self.state,
            "current_task": None if task is None else {
                "id": task.id, "title": task.title,
                "source": task.source, "url": task.url,
            },
            "last_heartbeat": _iso(self.last_heartbeat),
            "last_action": None if action is None else {
                "at": _iso(action.at), "summary": action.summary,
            },
            "pending_approvals": [
                {"id": a.id, "summary": a.summary, "requested_at": _iso(a.requested_at)}
                for a in self.pending_approvals
            ],
            "next_scheduled_run": _iso(self.next_scheduled_run),
            "artifacts_recent": [
                {"path_or_url": a.path_or_url, "at": _iso(a.at)}
                for a in self.artifacts_recent
            ],
            "spend_today": {"tokens": self.spend_today.tokens, "usd": self.spend_today.usd},
            "flags": list(self.flags),
        }


__all__ = [
    "State", "STATES", "Flag", "FLAGS", "Host",
    "TaskRef", "ActionRef", "ApprovalRef", "ArtifactRef", "Spend", "AgentStatus",
]
