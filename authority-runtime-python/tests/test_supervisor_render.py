"""Tests for the read-only supervisor views: attention, inbox, chat lines, HTML page."""

import re
from datetime import datetime, timedelta, timezone

import pytest

from authority_runtime.supervisor import (
    STATES,
    ActionRef,
    AgentStatus,
    ApprovalRef,
    ArtifactRef,
    FleetReport,
    InboxEntry,
    InboxSection,
    TaskRef,
    approvals_section,
    attention_lines,
    needs_attention,
    render_html,
)
from authority_runtime.supervisor.render import REFRESH_S

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
EVIL = '<script>alert("x")</script>&\''


def status(agent_id="a1", state="idle", **kw):
    """An AgentStatus with defaults for the required fields."""
    kw.setdefault("display_name", agent_id.upper())
    kw.setdefault("host", "home")
    return AgentStatus(agent_id=agent_id, state=state, **kw)


def approval(aid="ap1", age=timedelta(hours=1), summary=None):
    """An approval requested `age` before NOW."""
    return ApprovalRef(id=aid, summary=summary or f"approve {aid}", requested_at=NOW - age)


def report(*agents, gaps=()):
    """A FleetReport at NOW in the order given."""
    return FleetReport(generated_at=NOW, agents=tuple(agents), gaps=tuple(gaps))


def attention_block(page):
    """The slice of the page between the attention heading and the quiet fold."""
    start = page.index("<h2>Needs attention</h2>")
    return page[start : page.index("<details>")]


def details_block(page):
    """The folded quiet-agents block."""
    return page[page.index("<details>") : page.index("</details>")]


# ── needs_attention ───────────────────────────────────────────


@pytest.mark.parametrize("state", STATES)
def test_needs_attention_per_state_without_flags(state):
    expected = state in ("blocked_on_human", "failed")
    assert needs_attention(status(state=state)) is expected


@pytest.mark.parametrize("state", STATES)
def test_needs_attention_per_state_with_flag(state):
    assert needs_attention(status(state=state, flags=("stale_heartbeat",))) is True


# ── approvals_section ─────────────────────────────────────────


def test_approvals_section_oldest_first_across_agents():
    a = status(
        "a",
        pending_approvals=(approval("x1", timedelta(hours=1)), approval("x2", timedelta(hours=5))),
    )
    b = status("b", pending_approvals=(approval("y1", timedelta(hours=3)),))
    section = approvals_section(report(a, b))
    assert [e.item.id for e in section.entries] == ["x2", "y1", "x1"]
    assert [e.agent_id for e in section.entries] == ["a", "b", "a"]
    assert section.heading == "Approvals"
    assert all(e.how == "" for e in section.entries)
    assert section.note == ""


def test_approvals_section_id_breaks_ties():
    same = timedelta(hours=2)
    a = status("a", pending_approvals=(approval("zz", same),))
    b = status("b", pending_approvals=(approval("aa", same), approval("mm", same)))
    section = approvals_section(report(a, b))
    assert [e.item.id for e in section.entries] == ["aa", "mm", "zz"]


def test_approvals_section_applies_how_and_heading():
    a = status("a", pending_approvals=(approval("x1"), approval("x2", timedelta(hours=2))))
    section = approvals_section(report(a), how=lambda ap: f"/approve {ap.id}", heading="Deploys")
    assert section.heading == "Deploys"
    assert [e.how for e in section.entries] == ["/approve x2", "/approve x1"]
    assert isinstance(section.entries, tuple)


def test_approvals_section_empty_fleet():
    assert approvals_section(report(status("a"))).entries == ()


# ── attention_lines ───────────────────────────────────────────


def test_attention_lines_nothing_loud_is_one_line():
    lines = attention_lines(report(status("a"), status("b", "running")))
    assert lines == ["All 2 agents quiet. Nothing blocked, failed or flagged."]


def test_attention_lines_loud_agent_detail():
    loud = status(
        "a",
        "blocked_on_human",
        display_name="Alpha",
        flags=("approval_aging", "stale_heartbeat"),
        last_action=ActionRef(at=NOW - timedelta(hours=3), summary="asked for deploy"),
        pending_approvals=(approval("p1"), approval("p2")),
    )
    lines = attention_lines(report(loud, status("b"), status("c")))
    assert lines == [
        "Alpha: blocked_on_human [approval_aging, stale_heartbeat]",
        "  3h ago: asked for deploy",
        "  2 pending approval(s)",
        "Quiet: 2. Source gaps: 0.",
    ]


def test_attention_lines_bare_loud_agent_has_no_invented_detail():
    lines = attention_lines(report(status("a", "failed", display_name="Alpha")))
    assert lines == ["Alpha: failed", "Quiet: 0. Source gaps: 0."]


def test_attention_lines_limit_and_more_line():
    agents = [status(f"a{i}", "failed") for i in range(5)] + [status("q")]
    lines = attention_lines(report(*agents, gaps=("cron", "beads")), limit=2)
    assert lines == [
        "A0: failed",
        "A1: failed",
        "... and 3 more",
        "Quiet: 1. Source gaps: 2.",
    ]


def test_attention_lines_at_limit_has_no_more_line():
    agents = [status(f"a{i}", "failed") for i in range(3)]
    lines = attention_lines(report(*agents), limit=3)
    assert not any(line.startswith("...") for line in lines)


def test_attention_lines_flagged_quiet_state_counts_as_loud():
    lines = attention_lines(report(status("a", "idle", flags=("repeated_failure",)), status("b")))
    assert lines[0] == "A: idle [repeated_failure]"
    assert lines[-1] == "Quiet: 1. Source gaps: 0."


# ── render_html: page frame ───────────────────────────────────


def test_page_has_refresh_meta_and_title():
    page = render_html(report(status("a")), title="My fleet")
    assert REFRESH_S == 60
    assert '<meta http-equiv="refresh" content="60">' in page
    assert "<title>My fleet</title>" in page
    assert "<h1>My fleet</h1>" in page
    assert page.startswith("<!doctype html>")


def test_page_summary_counts():
    a = status("a", "failed", pending_approvals=(approval("p1"),))
    page = render_html(report(a, status("b"), status("c")), inbox=[approvals_section(report(a))])
    assert (
        "3 agents · 1 need attention · 1 waiting on you · as of 2026-10-05T12:00:00+00:00" in page
    )
    assert "<h2>Quiet (2)</h2>" in page


def test_page_has_no_write_paths_or_links():
    a = status(
        "a",
        "blocked_on_human",
        current_task=TaskRef(id="t1", title="fix", source="beads", url="https://x.test/t1"),
        artifacts_recent=(ArtifactRef(path_or_url="https://x.test/a", at=NOW),),
        pending_approvals=(approval("p1"),),
    )
    section = approvals_section(report(a), how=lambda ap: "reply /approve in chat")
    page = render_html(report(a, status("b")), inbox=[section])
    lowered = page.lower()
    assert "<form" not in lowered
    assert "<button" not in lowered
    assert "href" not in lowered
    assert "https://x.test" not in page


# ── render_html: escaping ─────────────────────────────────────


def test_every_untrusted_string_is_escaped():
    a = status(
        EVIL,
        "blocked_on_human",
        display_name=EVIL,
        current_task=TaskRef(id=EVIL, title=EVIL, source="beads"),
        last_action=ActionRef(at=NOW - timedelta(minutes=5), summary=EVIL),
        pending_approvals=(ApprovalRef(id=EVIL, summary=EVIL, requested_at=NOW),),
    )
    q = status("quiet", display_name=EVIL)
    section = InboxSection(
        heading=EVIL,
        entries=(InboxEntry(agent_id=EVIL, item=a.pending_approvals[0], how=EVIL),),
        note=EVIL,
    )
    page = render_html(report(a, q, gaps=(EVIL,)), inbox=[section], title=EVIL)
    assert "<script>" not in page
    assert 'alert("x")' not in page
    assert "&'" not in page
    escaped = "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;&amp;&#x27;"
    assert f'id="{escaped}"' in page
    assert f"<title>{escaped}</title>" in page
    assert f"<h1>{escaped}</h1>" in page
    assert f"<li>{escaped}</li>" in page
    assert f'<p class="mut">{escaped}</p>' in page
    assert f'<div class="mut">{escaped}</div>' in page
    assert f"<b>{escaped}</b>" in page
    assert f"<div>{escaped}: {escaped}</div>" in page
    assert f"<code>{escaped}</code>" in page
    assert page.count(escaped) >= 12


def test_quote_in_agent_id_cannot_break_attribute():
    page = render_html(report(status('x" onmouseover="bad', "failed")))
    assert 'onmouseover="bad"' not in page
    assert 'id="x&quot; onmouseover=&quot;bad"' in page


# ── render_html: layout ───────────────────────────────────────


def test_loud_in_report_order_quiet_only_in_details():
    agents = (
        status("blocked", "blocked_on_human"),
        status("failed", "failed"),
        status("flagged", "running", flags=("stale_heartbeat",)),
        status("idle1"),
        status("run1", "running"),
    )
    page = render_html(report(*agents))
    loud = attention_block(page)
    ids = re.findall(r'id="([^"]+)"', loud)
    assert ids == ["blocked", "failed", "flagged"]
    folded = details_block(page)
    assert re.findall(r'id="([^"]+)"', folded) == ["idle1", "run1"]
    assert page.count('id="idle1"') == 1
    assert page.count('id="blocked"') == 1
    assert 'class="card blocked_on_human"' in loud
    assert 'class="card failed"' in loud
    assert 'class="card flagged"' in loud
    assert 'class="card "' in folded


def test_no_loud_agents_says_so():
    page = render_html(report(status("a")))
    assert "Nothing blocked, failed or flagged." in attention_block(page)


def test_empty_inbox_section_says_nothing_waiting():
    page = render_html(
        report(status("a")), inbox=[InboxSection(heading="Approvals", entries=(), note="via chat")]
    )
    assert "<h2>Approvals (0)</h2>" in page
    assert '<p class="mut">via chat</p>' in page
    assert "Nothing waiting." in page


def test_inbox_entries_render_in_given_order_with_how():
    a = status(
        "a",
        "blocked_on_human",
        pending_approvals=(
            approval("new", timedelta(minutes=10), "newer one"),
            approval("old", timedelta(days=2), "older one"),
        ),
    )
    section = approvals_section(report(a), how=lambda ap: f"say: approve {ap.id}")
    page = render_html(report(a), inbox=[section])
    assert "<h2>Approvals (2)</h2>" in page
    assert page.index("older one") < page.index("newer one")
    assert "a · 2d ago · <code>old</code>" in page
    assert "a · 10m ago · <code>new</code>" in page
    assert '<div class="mut">say: approve old</div>' in page
    assert "Nothing waiting." not in page


def test_gaps_section_absent_without_gaps():
    page = render_html(report(status("a")))
    assert "Sources not read" not in page
    assert "<ul>" not in page


def test_gaps_section_lists_each_gap():
    page = render_html(report(status("a"), gaps=("cron unreadable", "beads down")))
    assert "<h2>Sources not read</h2><ul><li>cron unreadable</li><li>beads down</li></ul>" in page


# ── render_html: times and absent fields ──────────────────────


def test_future_times_render_as_in():
    a = status(
        "a",
        "scheduled",
        flags=("stale_heartbeat",),
        last_heartbeat=NOW - timedelta(seconds=30),
        next_scheduled_run=NOW + timedelta(minutes=20),
    )
    page = render_html(report(a))
    assert "heartbeat 30s ago · next in 20m" in page


def test_missing_heartbeat_renders_never():
    page = render_html(report(status("a", "unknown", flags=("stale_heartbeat",))))
    assert "heartbeat never" in page


def test_absent_fields_are_not_shown():
    page = render_html(report(status("a", "failed")))
    card = attention_block(page)
    assert "next " not in card
    assert "pending" not in card
    assert "recent artifacts" not in card
    assert 'class="fl"' not in card
    assert card.count("<div") == 2


def test_present_fields_are_shown():
    a = status(
        "a",
        "failed",
        flags=("repeated_failure",),
        current_task=TaskRef(id="T-9", title="ship it", source="beads"),
        last_action=ActionRef(at=NOW - timedelta(days=1, hours=2), summary="tests red"),
        pending_approvals=(approval("p1"),),
        artifacts_recent=(
            ArtifactRef(path_or_url="out.txt", at=NOW),
            ArtifactRef(path_or_url="b.txt", at=NOW),
        ),
        last_heartbeat=NOW - timedelta(hours=2),
    )
    card = attention_block(render_html(report(a)))
    assert '<div class="fl">repeated_failure</div>' in card
    assert "<div>T-9: ship it</div>" in card
    assert "1d ago: tests red" in card
    assert "heartbeat 2h ago · 1 pending · 2 recent artifacts" in card
