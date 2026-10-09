# Supervisor (read-only fleet status)

`authority_runtime.supervisor` gives an operator one record per agent. It
answers four questions fast. What is each agent doing? What waits on a human?
What failed or went quiet? What changed?

A supervisor is a projection, not a store. It reads each source of truth and
renders. It never writes back.

Carryall ships the contract, the state and flag rules, and board order.
Deployments supply a `FleetAdapter` that reads their own sources.

## Quick start

```python
from datetime import datetime, timedelta, timezone

from authority_runtime.supervisor import (
    FleetAdapter, Observation, fleet_status, to_json,
)

class MyFleet(FleetAdapter):
    def observe(self, now):
        try:
            last = read_my_scheduler()          # your source
        except OSError:
            self.gaps.append("scheduler: unreadable")
            last = None
        return [Observation(
            agent_id="indexer", display_name="Indexer", host="home",
            last_heartbeat=last, heartbeat_max_age=timedelta(hours=26),
        )]

report = fleet_status(MyFleet())
print(to_json(report.agents))
for gap in report.gaps:
    print("gap:", gap)
```

## The contract

`AgentStatus.to_dict()` emits exactly these keys:

| Key | Meaning |
|---|---|
| `agent_id`, `display_name` | The row |
| `host` | `home` or `work` |
| `state` | `running`, `idle`, `scheduled`, `blocked_on_human`, `failed`, `unknown` |
| `current_task` | `{id, title, source, url}` or null |
| `last_heartbeat` | ISO 8601 or null |
| `last_action` | `{at, summary}` or null |
| `pending_approvals` | `[{id, summary, requested_at}]`, oldest first |
| `next_scheduled_run` | ISO 8601 or null |
| `artifacts_recent` | `[{path_or_url, at}]` |
| `spend_today` | `{tokens, usd}`, null when no source attributes spend |
| `flags` | any of `stale_heartbeat`, `repeated_failure`, `approval_aging`, `over_budget` |

`last_heartbeat` is nullable. An agent that never checked in has none.

## State rules

The first match wins:

1. Any pending approval: `blocked_on_human`.
2. A source says it is executing now: `running`.
3. Its last outcome failed: `failed`.
4. It has a next run and is not paused: `scheduled`.
5. Any other signal at all, including paused: `idle`.
6. No signal: `unknown`.

## Flags

- `stale_heartbeat`: the adapter listed a source in `Observation.overdue`,
  or the agent has a freshness expectation and its newest heartbeat is older
  or missing. Never set on a paused agent. Use `overdue` when an agent has
  several sources: a fresh one must not hide a dead one.
- `repeated_failure`: consecutive failures at or above the threshold (2).
- `approval_aging`: any pending approval older than 24 hours.
- `over_budget`: in the vocabulary, never derived. No per-agent budget
  source exists yet. Add one before deriving it.

Thresholds are tunable through `Thresholds`.

## Board order

Blocked, failed, flagged, unknown, running, scheduled, idle. A flag lifts an
agent above its state's rank. Ties sort by `agent_id`.

## Views

`render_html(report, inbox=sections)` returns one static page. Blocked, failed
and flagged agents lead. Inbox sections follow, oldest item first. Quiet agents
fold into a closed list. Sources the adapter could not read close the page.
The page refreshes every 60 seconds.

`approvals_section(report, how=...)` builds the approvals inbox from every
agent's `pending_approvals`. `how` returns the text that says where an item is
cleared today, such as a chat command. A deployment can add its own sections
of `InboxEntry` rows for other items that wait on the operator.

`attention_lines(report)` returns the blocked, failed and flagged agents as
plain lines for a chat reply. With nothing loud it returns one line saying so.

The views link to nothing that writes. Actions stay where they are today.

## Rules for adapters

- Read only. A supervisor that needs write access to a source is out of scope.
- No invented signals. Leave a field `None` when no source has it.
- Report holes. Append to `self.gaps` when a source is missing or unreadable.
  A quiet board must never stand in for a healthy one.
- Datetimes must be timezone-aware. `Observation` rejects naive ones.
- One host per adapter. Home and work never share an adapter, a store or a view.

The package is stdlib-only and imports on Python 3.9.
