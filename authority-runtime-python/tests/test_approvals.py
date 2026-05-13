"""Tests for the approval queue, including auto-deny semantics."""

import time
import pytest

from authority_runtime.approvals import ApprovalQueue


@pytest.fixture
def queue(tmp_path):
    return ApprovalQueue(tmp_path / "approvals")


def test_legacy_ttl_still_produces_expired_status(queue):
    """Without auto_deny_seconds, behaviour is unchanged: TTL -> 'expired'."""
    rid = queue.create_request(
        agent_id="a", action="read", resource_uri="slos://vaults/x/y",
        purpose="p", ttl_seconds=1,
    )
    time.sleep(1.1)
    record = queue.check(rid)
    assert record["status"] == "expired"
    assert "decided_by" not in record


def test_auto_deny_transitions_before_ttl(queue):
    """With auto_deny_seconds < ttl, request transitions to 'denied' first."""
    rid = queue.create_request(
        agent_id="a", action="read", resource_uri="slos://vaults/x/y",
        purpose="p", ttl_seconds=300, auto_deny_seconds=1,
    )
    time.sleep(1.1)
    record = queue.check(rid)
    assert record["status"] == "denied"
    assert record["decided_by"] == "auto-deny"
    assert "auto-denied" in record["decision_reason"]
    assert queue.is_denied(rid) is True
    assert queue.is_approved(rid) is False


def test_auto_deny_ignored_when_larger_than_ttl(queue):
    """auto_deny_seconds >= ttl is a configuration mistake: TTL wins."""
    rid = queue.create_request(
        agent_id="a", action="read", resource_uri="slos://vaults/x/y",
        purpose="p", ttl_seconds=1, auto_deny_seconds=999,
    )
    time.sleep(1.1)
    record = queue.check(rid)
    assert record["status"] == "expired"


def test_human_decision_beats_auto_deny(queue):
    """A human approval before auto_deny_at sticks."""
    rid = queue.create_request(
        agent_id="a", action="read", resource_uri="slos://vaults/x/y",
        purpose="p", ttl_seconds=300, auto_deny_seconds=300,
    )
    assert queue.decide(rid, "approved", decided_by="erik") is True
    record = queue.check(rid)
    assert record["status"] == "approved"
    assert record["decided_by"] == "erik"


def test_expire_stale_returns_per_status_counts(queue):
    """expire_stale counts both 'denied' and 'expired' transitions."""
    queue.create_request(
        agent_id="a1", action="read", resource_uri="slos://vaults/x/y",
        purpose="p", ttl_seconds=300, auto_deny_seconds=1,
    )
    queue.create_request(
        agent_id="a2", action="read", resource_uri="slos://vaults/x/z",
        purpose="p", ttl_seconds=1,
    )
    time.sleep(1.1)
    counts = queue.expire_stale()
    assert counts == {"denied": 1, "expired": 1}


def test_list_pending_excludes_auto_denied(queue):
    rid = queue.create_request(
        agent_id="a", action="read", resource_uri="slos://vaults/x/y",
        purpose="p", ttl_seconds=300, auto_deny_seconds=1,
    )
    time.sleep(1.1)
    pending = queue.list_pending()
    assert all(p["id"] != rid for p in pending)
    # And the record is now persisted as 'denied'
    assert queue.check(rid)["status"] == "denied"


def test_is_denied_handles_human_deny(queue):
    rid = queue.create_request(
        agent_id="a", action="read", resource_uri="slos://vaults/x/y", purpose="p",
    )
    queue.decide(rid, "denied", decided_by="erik", reason="no")
    assert queue.is_denied(rid) is True
