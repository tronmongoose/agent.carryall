"""Turn raw per-agent signals into AgentStatus state, flags and board order.

Exports Observation (what an adapter saw), Thresholds, derive_state,
derive_flags, to_status and sort_key. The rules here decide state only from
signals an adapter actually observed. A missing signal stays missing: an
agent with no signal at all is "unknown".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Literal, Optional, Tuple

from .contract import (
    ActionRef,
    AgentStatus,
    ApprovalRef,
    ArtifactRef,
    Flag,
    Host,
    Spend,
    State,
    TaskRef,
)

Outcome = Literal["ok", "fail"]


def _require_aware(name: str, value: Optional[datetime]) -> None:
    """Reject naive datetimes; mixing them with aware ones breaks ordering."""
    if value is not None and value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class Observation:
    """Everything an adapter observed about one agent.

    running is None when no source says whether the agent is executing now.
    heartbeat_max_age is None when nothing expects the agent to check in.
    overdue names each source the adapter found past its own freshness limit
    (a job past its schedule, a stamp past its age). A row with several
    sources can be overdue on one while its newest heartbeat is fresh.
    """

    agent_id: str
    display_name: str
    host: Host
    running: Optional[bool] = None
    paused: bool = False
    last_heartbeat: Optional[datetime] = None
    heartbeat_max_age: Optional[timedelta] = None
    last_outcome: Optional[Outcome] = None
    consecutive_failures: int = 0
    current_task: Optional[TaskRef] = None
    last_action: Optional[ActionRef] = None
    pending_approvals: Tuple[ApprovalRef, ...] = ()
    next_scheduled_run: Optional[datetime] = None
    artifacts_recent: Tuple[ArtifactRef, ...] = ()
    spend_today: Spend = Spend()
    overdue: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate adapter output at the trust boundary."""
        if not self.agent_id:
            raise ValueError("agent_id is required")
        _require_aware("last_heartbeat", self.last_heartbeat)
        _require_aware("next_scheduled_run", self.next_scheduled_run)
        if self.last_action is not None:
            _require_aware("last_action.at", self.last_action.at)
        for approval in self.pending_approvals:
            _require_aware("pending_approvals.requested_at", approval.requested_at)
        for artifact in self.artifacts_recent:
            _require_aware("artifacts_recent.at", artifact.at)


@dataclass(frozen=True)
class Thresholds:
    """Operator-tunable limits for flags."""

    repeated_failures: int = 2
    approval_age: timedelta = timedelta(hours=24)


def _has_any_signal(obs: Observation) -> bool:
    """True when any source said anything about this agent."""
    return (
        obs.paused
        or obs.running is not None
        or obs.last_heartbeat is not None
        or obs.last_outcome is not None
        or obs.last_action is not None
        or obs.current_task is not None
        or bool(obs.artifacts_recent)
        or obs.spend_today != Spend()
    )


def derive_state(obs: Observation) -> State:
    """Pick one state. Waiting on the operator outranks everything else."""
    if obs.pending_approvals:
        return "blocked_on_human"
    if obs.running:
        return "running"
    if obs.last_outcome == "fail":
        return "failed"
    if obs.next_scheduled_run is not None and not obs.paused:
        return "scheduled"
    if _has_any_signal(obs):
        return "idle"
    return "unknown"


def _heartbeat_late(obs: Observation, now: datetime) -> bool:
    """The newest heartbeat is missing or older than the row's own limit."""
    if obs.heartbeat_max_age is None:
        return False
    hb = obs.last_heartbeat
    return hb is None or now - hb > obs.heartbeat_max_age


def derive_flags(obs: Observation, now: datetime, limits: Thresholds) -> Tuple[Flag, ...]:
    """Flags that need operator attention. over_budget has no source yet."""
    _require_aware("now", now)
    flags: List[Flag] = []
    if not obs.paused and (obs.overdue or _heartbeat_late(obs, now)):
        flags.append("stale_heartbeat")
    if obs.consecutive_failures >= limits.repeated_failures:
        flags.append("repeated_failure")
    if any(now - a.requested_at > limits.approval_age for a in obs.pending_approvals):
        flags.append("approval_aging")
    return tuple(flags)


def to_status(obs: Observation, now: datetime, limits: Thresholds) -> AgentStatus:
    """Build the contract record from one observation."""
    return AgentStatus(
        agent_id=obs.agent_id,
        display_name=obs.display_name,
        host=obs.host,
        state=derive_state(obs),
        current_task=obs.current_task,
        last_heartbeat=obs.last_heartbeat,
        last_action=obs.last_action,
        pending_approvals=tuple(sorted(obs.pending_approvals, key=lambda a: a.requested_at)),
        next_scheduled_run=obs.next_scheduled_run,
        artifacts_recent=obs.artifacts_recent,
        spend_today=obs.spend_today,
        flags=derive_flags(obs, now, limits),
    )


_RANK = {"blocked_on_human": 0, "failed": 1, "unknown": 3,
         "running": 4, "scheduled": 5, "idle": 6}
_FLAGGED_RANK = 2


def sort_key(status: AgentStatus) -> Tuple[int, str]:
    """Board order: blocked, failed, flagged, unknown, running, scheduled, idle."""
    rank = _RANK[status.state]
    if status.flags and rank > _FLAGGED_RANK:
        rank = _FLAGGED_RANK
    return (rank, status.agent_id)


__all__ = [
    "Outcome", "Observation", "Thresholds",
    "derive_state", "derive_flags", "to_status", "sort_key",
]
