"""
supervisor — read-only fleet status, with the agent as the primitive.

A supervisor is a projection, not a store. It reads each source of truth
(schedulers, trackers, audit logs, approval queues) and renders one
AgentStatus per agent. It never writes back to any of them.

Boundary: Carryall ships the AgentStatus contract, the rules that derive
state and flags from observed signals, board ordering, JSON rendering, and the read-only HTML and chat views.
Deployments supply:
  - a FleetAdapter that reads their own sources
  - the mapping from their jobs and identities to agents
  - freshness expectations per agent (heartbeat_max_age)

No invented signals: an Observation field with no source stays None, and an
agent with no signal at all is "unknown". over_budget is part of the
vocabulary but is never derived until a per-agent budget source exists.

Python 3.9-compatible and stdlib-only, so a deployment on an older venv can
import it.
"""

from .contract import (
    FLAGS,
    STATES,
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
from .derive import (
    Observation,
    Outcome,
    Thresholds,
    derive_flags,
    derive_state,
    sort_key,
    to_status,
)
from .fleet import FleetAdapter, FleetReport, fleet_status, to_json
from .render import (
    InboxEntry,
    InboxSection,
    approvals_section,
    attention_lines,
    needs_attention,
    render_html,
)

__all__ = [
    "FLAGS",
    "STATES",
    "ActionRef",
    "AgentStatus",
    "ApprovalRef",
    "ArtifactRef",
    "Flag",
    "Host",
    "Spend",
    "State",
    "TaskRef",
    "Observation",
    "Outcome",
    "Thresholds",
    "derive_flags",
    "derive_state",
    "sort_key",
    "to_status",
    "FleetAdapter",
    "FleetReport",
    "fleet_status",
    "to_json",
    "InboxEntry",
    "InboxSection",
    "approvals_section",
    "attention_lines",
    "needs_attention",
    "render_html",
]
