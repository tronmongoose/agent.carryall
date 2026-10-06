"""Tests for the supervisor primitive: state, flags, board order, contract shape."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from authority_runtime.supervisor import (
    FLAGS,
    STATES,
    ActionRef,
    AgentStatus,
    ApprovalRef,
    ArtifactRef,
    FleetAdapter,
    Observation,
    Spend,
    TaskRef,
    Thresholds,
    derive_flags,
    derive_state,
    fleet_status,
    sort_key,
    to_json,
    to_status,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
LIMITS = Thresholds()


def obs(agent_id="a1", **kw):
    """Build an Observation with defaults for the required fields."""
    kw.setdefault("display_name", agent_id.upper())
    kw.setdefault("host", "home")
    return Observation(agent_id=agent_id, **kw)


def approval(aid="ap1", age=timedelta(hours=1)):
    """An approval requested `age` before NOW."""
    return ApprovalRef(id=aid, summary=f"approve {aid}", requested_at=NOW - age)


def status(agent_id, state, flags=()):
    """A bare AgentStatus for ordering tests."""
    return AgentStatus(agent_id=agent_id, display_name=agent_id, host="home",
                       state=state, flags=flags)


class ListAdapter(FleetAdapter):
    """Adapter returning a fixed list, with optional gaps."""

    def __init__(self, observations, gaps=()):
        super().__init__()
        self._observations = list(observations)
        self.gaps.extend(gaps)
        self.seen_now = None

    def observe(self, now):
        self.seen_now = now
        return list(self._observations)


# ── Vocabulary ────────────────────────────────────────────────


def test_vocabularies():
    assert set(STATES) == {"running", "idle", "scheduled", "blocked_on_human",
                           "failed", "unknown"}
    assert set(FLAGS) == {"stale_heartbeat", "repeated_failure", "approval_aging",
                          "over_budget"}


# ── derive_state: one test per state ──────────────────────────


def test_state_running():
    assert derive_state(obs(running=True)) == "running"


def test_state_idle_from_heartbeat_only():
    assert derive_state(obs(last_heartbeat=NOW)) == "idle"


def test_state_idle_when_explicitly_not_running():
    assert derive_state(obs(running=False)) == "idle"


def test_state_scheduled():
    assert derive_state(obs(next_scheduled_run=NOW + timedelta(hours=1))) == "scheduled"


def test_state_blocked_on_human():
    assert derive_state(obs(pending_approvals=(approval(),))) == "blocked_on_human"


def test_state_failed():
    assert derive_state(obs(last_outcome="fail")) == "failed"


def test_state_ok_outcome_is_idle():
    assert derive_state(obs(last_outcome="ok")) == "idle"


def test_state_unknown_with_no_signal():
    assert derive_state(obs()) == "unknown"


# ── derive_state: precedence ──────────────────────────────────


def test_pending_approvals_beat_running():
    o = obs(running=True, last_outcome="fail", pending_approvals=(approval(),))
    assert derive_state(o) == "blocked_on_human"


def test_running_beats_failed_last_outcome():
    assert derive_state(obs(running=True, last_outcome="fail")) == "running"


def test_failed_beats_scheduled():
    o = obs(last_outcome="fail", next_scheduled_run=NOW + timedelta(hours=1))
    assert derive_state(o) == "failed"


def test_paused_agent_with_next_run_is_idle_not_scheduled():
    o = obs(paused=True, next_scheduled_run=NOW + timedelta(hours=1))
    assert derive_state(o) == "idle"


def test_paused_alone_is_idle():
    assert derive_state(obs(paused=True)) == "idle"


def test_last_action_alone_is_idle():
    o = obs(last_action=ActionRef(at=NOW, summary="did a thing"))
    assert derive_state(o) == "idle"


def test_current_task_is_a_signal_not_unknown():
    """A tracker naming the agent's task is a source saying something about it.

    Observation's contract: "an agent with no signal at all is unknown", and
    _has_any_signal is documented as "True when any source said anything
    about this agent". current_task comes from a tracker, so the agent has
    a signal and must not be "unknown". The source ignores current_task,
    artifacts_recent and spend_today in _has_any_signal.
    """
    o = obs(current_task=TaskRef(id="T-1", title="t", source="tracker"))
    assert derive_state(o) != "unknown"


# ── derive_flags: stale_heartbeat ─────────────────────────────


def test_stale_when_heartbeat_older_than_max_age():
    o = obs(last_heartbeat=NOW - timedelta(minutes=11),
            heartbeat_max_age=timedelta(minutes=10))
    assert "stale_heartbeat" in derive_flags(o, NOW, LIMITS)


def test_not_stale_within_max_age():
    o = obs(last_heartbeat=NOW - timedelta(minutes=9),
            heartbeat_max_age=timedelta(minutes=10))
    assert derive_flags(o, NOW, LIMITS) == ()


def test_not_stale_exactly_at_max_age():
    o = obs(last_heartbeat=NOW - timedelta(minutes=10),
            heartbeat_max_age=timedelta(minutes=10))
    assert derive_flags(o, NOW, LIMITS) == ()


def test_stale_when_heartbeat_missing_but_expected():
    o = obs(heartbeat_max_age=timedelta(minutes=10))
    assert derive_flags(o, NOW, LIMITS) == ("stale_heartbeat",)


def test_paused_agent_never_stale():
    old = obs(paused=True, last_heartbeat=NOW - timedelta(days=3),
              heartbeat_max_age=timedelta(minutes=10))
    missing = obs(paused=True, heartbeat_max_age=timedelta(minutes=10))
    assert derive_flags(old, NOW, LIMITS) == ()
    assert derive_flags(missing, NOW, LIMITS) == ()


def test_no_expectation_never_stale():
    old = obs(last_heartbeat=NOW - timedelta(days=30))
    assert derive_flags(old, NOW, LIMITS) == ()
    assert derive_flags(obs(), NOW, LIMITS) == ()


def test_overdue_alone_flags_stale():
    o = obs(overdue=("job nightly: past schedule",))
    assert derive_flags(o, NOW, LIMITS) == ("stale_heartbeat",)


def test_overdue_flags_stale_even_with_fresh_heartbeat():
    o = obs(last_heartbeat=NOW, heartbeat_max_age=timedelta(minutes=10),
            overdue=("stamp digest: missing",))
    assert derive_flags(o, NOW, LIMITS) == ("stale_heartbeat",)


def test_overdue_on_paused_agent_not_stale():
    o = obs(paused=True, overdue=("job nightly: past schedule",))
    assert derive_flags(o, NOW, LIMITS) == ()


def test_empty_overdue_with_fresh_heartbeat_not_stale():
    o = obs(last_heartbeat=NOW - timedelta(minutes=1),
            heartbeat_max_age=timedelta(minutes=10), overdue=())
    assert derive_flags(o, NOW, LIMITS) == ()


@pytest.mark.parametrize("base", [
    {},
    {"last_heartbeat": NOW},
    {"running": True},
    {"last_outcome": "fail"},
    {"next_scheduled_run": NOW + timedelta(hours=1)},
    {"paused": True},
])
def test_overdue_does_not_change_state(base):
    plain = obs(**base)
    late = obs(overdue=("job nightly: past schedule",), **base)
    assert derive_state(late) == derive_state(plain)


# ── derive_flags: repeated_failure, approval_aging, over_budget ──


def test_repeated_failure_at_threshold():
    o = obs(consecutive_failures=2)
    assert derive_flags(o, NOW, Thresholds(repeated_failures=2)) == ("repeated_failure",)


def test_repeated_failure_not_below_threshold():
    o = obs(consecutive_failures=1)
    assert derive_flags(o, NOW, Thresholds(repeated_failures=2)) == ()


def test_repeated_failure_custom_threshold():
    limits = Thresholds(repeated_failures=5)
    assert derive_flags(obs(consecutive_failures=4), NOW, limits) == ()
    assert derive_flags(obs(consecutive_failures=5), NOW, limits) == ("repeated_failure",)


def test_approval_aging_older_than_threshold():
    o = obs(pending_approvals=(approval(age=timedelta(hours=25)),))
    assert derive_flags(o, NOW, LIMITS) == ("approval_aging",)


def test_approval_aging_not_for_younger():
    o = obs(pending_approvals=(approval(age=timedelta(hours=23)),))
    assert derive_flags(o, NOW, LIMITS) == ()


def test_approval_aging_any_one_old_approval_flags():
    o = obs(pending_approvals=(approval("new", timedelta(minutes=5)),
                               approval("old", timedelta(hours=48))))
    assert "approval_aging" in derive_flags(o, NOW, LIMITS)


def test_over_budget_never_derived():
    o = obs(running=True, spend_today=Spend(tokens=10**12, usd=1e9),
            consecutive_failures=99, heartbeat_max_age=timedelta(seconds=1),
            pending_approvals=(approval(age=timedelta(days=9)),))
    flags = derive_flags(o, NOW, LIMITS)
    assert "over_budget" not in flags
    assert flags == ("stale_heartbeat", "repeated_failure", "approval_aging")


def test_derive_flags_rejects_naive_now():
    with pytest.raises(ValueError, match="now"):
        derive_flags(obs(), NOW.replace(tzinfo=None), LIMITS)


# ── sort_key ──────────────────────────────────────────────────


def test_board_order_by_state_and_flag():
    statuses = [
        status("idle", "idle"),
        status("sched", "scheduled"),
        status("run", "running"),
        status("unk", "unknown"),
        status("flagged", "idle", flags=("stale_heartbeat",)),
        status("fail", "failed"),
        status("blocked", "blocked_on_human"),
    ]
    ordered = [s.agent_id for s in sorted(statuses, key=sort_key)]
    assert ordered == ["blocked", "fail", "flagged", "unk", "run", "sched", "idle"]


def test_flagged_idle_sorts_above_unflagged_running():
    flagged_idle = status("z-idle", "idle", flags=("repeated_failure",))
    running = status("a-run", "running")
    assert sort_key(flagged_idle) < sort_key(running)


def test_flags_do_not_demote_blocked_or_failed():
    blocked = status("b", "blocked_on_human", flags=("approval_aging",))
    failed = status("f", "failed", flags=("repeated_failure",))
    flagged = status("a", "running", flags=("stale_heartbeat",))
    ordered = [s.agent_id for s in sorted([flagged, failed, blocked], key=sort_key)]
    assert ordered == ["b", "f", "a"]


def test_ties_broken_by_agent_id():
    statuses = [status("c", "idle"), status("a", "idle"), status("b", "idle")]
    assert [s.agent_id for s in sorted(statuses, key=sort_key)] == ["a", "b", "c"]


# ── Observation validation ────────────────────────────────────


def test_observation_rejects_empty_agent_id():
    with pytest.raises(ValueError, match="agent_id"):
        Observation(agent_id="", display_name="x", host="home")


@pytest.mark.parametrize("kw,name", [
    ({"last_heartbeat": datetime(2026, 10, 5)}, "last_heartbeat"),
    ({"next_scheduled_run": datetime(2026, 10, 5)}, "next_scheduled_run"),
    ({"last_action": ActionRef(at=datetime(2026, 10, 5), summary="s")}, "last_action"),
    ({"pending_approvals": (ApprovalRef(id="x", summary="s",
                                        requested_at=datetime(2026, 10, 5)),)},
     "pending_approvals"),
    ({"artifacts_recent": (ArtifactRef(path_or_url="p", at=datetime(2026, 10, 5)),)},
     "artifacts_recent"),
])
def test_observation_rejects_naive_datetimes(kw, name):
    with pytest.raises(ValueError, match=name):
        obs(**kw)


# ── AgentStatus.to_dict ───────────────────────────────────────


CONTRACT_KEYS = {
    "agent_id", "display_name", "host", "state", "current_task", "last_heartbeat",
    "last_action", "pending_approvals", "next_scheduled_run", "artifacts_recent",
    "spend_today", "flags",
}


def test_to_dict_keys_and_nulls_when_unset():
    d = status("a", "unknown").to_dict()
    assert set(d) == CONTRACT_KEYS
    assert d["current_task"] is None
    assert d["last_heartbeat"] is None
    assert d["last_action"] is None
    assert d["next_scheduled_run"] is None
    assert d["pending_approvals"] == []
    assert d["artifacts_recent"] == []
    assert d["spend_today"] == {"tokens": None, "usd": None}
    assert d["flags"] == []


def test_to_dict_full_shape():
    o = obs(
        "worker", display_name="Worker", host="work", running=True,
        last_heartbeat=NOW, heartbeat_max_age=timedelta(minutes=5),
        current_task=TaskRef(id="T-9", title="Ship", source="tracker", url="https://x/T-9"),
        last_action=ActionRef(at=NOW, summary="pushed"),
        next_scheduled_run=NOW + timedelta(hours=2),
        artifacts_recent=(ArtifactRef(path_or_url="out/report.md", at=NOW),),
        spend_today=Spend(tokens=1200, usd=0.42),
        consecutive_failures=3,
    )
    d = to_status(o, NOW, LIMITS).to_dict()
    assert set(d) == CONTRACT_KEYS
    assert d["agent_id"] == "worker"
    assert d["display_name"] == "Worker"
    assert d["host"] == "work"
    assert d["state"] == "running"
    assert d["current_task"] == {"id": "T-9", "title": "Ship", "source": "tracker",
                                 "url": "https://x/T-9"}
    assert d["last_heartbeat"] == NOW.isoformat()
    assert d["last_action"] == {"at": NOW.isoformat(), "summary": "pushed"}
    assert d["next_scheduled_run"] == (NOW + timedelta(hours=2)).isoformat()
    assert d["artifacts_recent"] == [{"path_or_url": "out/report.md", "at": NOW.isoformat()}]
    assert d["spend_today"] == {"tokens": 1200, "usd": 0.42}
    assert d["flags"] == ["repeated_failure"]


def test_to_dict_task_url_null_when_unset():
    s = AgentStatus(agent_id="a", display_name="a", host="home", state="idle",
                    current_task=TaskRef(id="1", title="t", source="s"))
    assert s.to_dict()["current_task"]["url"] is None


def test_pending_approvals_oldest_first():
    o = obs(pending_approvals=(approval("new", timedelta(minutes=1)),
                               approval("old", timedelta(hours=3)),
                               approval("mid", timedelta(hours=1))))
    rendered = to_status(o, NOW, LIMITS).to_dict()["pending_approvals"]
    assert [a["id"] for a in rendered] == ["old", "mid", "new"]
    assert set(rendered[0]) == {"id", "summary", "requested_at"}
    assert rendered[0]["requested_at"] == (NOW - timedelta(hours=3)).isoformat()


# ── fleet_status and to_json ──────────────────────────────────


def test_fleet_status_sorts_and_carries_gaps():
    adapter = ListAdapter(
        [obs("idle-a", last_heartbeat=NOW),
         obs("run-b", running=True),
         obs("blocked-c", pending_approvals=(approval(),)),
         obs("unk-d")],
        gaps=["scheduler: unreadable"],
    )
    report = fleet_status(adapter, now=NOW)
    assert [s.agent_id for s in report.agents] == ["blocked-c", "unk-d", "run-b", "idle-a"]
    assert report.gaps == ("scheduler: unreadable",)
    assert report.generated_at == NOW
    assert adapter.seen_now == NOW


def test_fleet_status_applies_custom_limits():
    adapter = ListAdapter([obs("a", consecutive_failures=1)])
    assert fleet_status(adapter, now=NOW).agents[0].flags == ()
    tight = fleet_status(adapter, now=NOW, limits=Thresholds(repeated_failures=1))
    assert tight.agents[0].flags == ("repeated_failure",)


def test_fleet_status_defaults_now_to_aware_utc():
    report = fleet_status(ListAdapter([obs("a")]))
    assert report.generated_at.tzinfo is not None


def test_fleet_status_rejects_duplicate_agent_ids():
    adapter = ListAdapter([obs("dup"), obs("dup", running=True)])
    with pytest.raises(ValueError, match="duplicate"):
        fleet_status(adapter, now=NOW)


def test_fleet_status_empty_fleet():
    report = fleet_status(ListAdapter([]), now=NOW)
    assert report.agents == ()
    assert report.gaps == ()


def test_adapter_is_abstract():
    with pytest.raises(TypeError):
        FleetAdapter()  # type: ignore[abstract]


def test_to_json_round_trips():
    adapter = ListAdapter([
        obs("a", running=True, last_heartbeat=NOW,
            pending_approvals=(approval("x", timedelta(hours=30)),)),
        obs("b"),
    ])
    agents = fleet_status(adapter, now=NOW).agents
    parsed = json.loads(to_json(agents))
    assert parsed == [s.to_dict() for s in agents]
    assert [p["agent_id"] for p in parsed] == ["a", "b"]
    assert parsed[0]["flags"] == ["approval_aging"]
    assert json.loads(to_json(agents, indent=None)) == parsed
