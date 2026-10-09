"""Read-only views of a FleetReport: the fleet board, the inbox, chat lines.

Exports InboxEntry, InboxSection, needs_attention, approvals_section,
attention_lines and render_html. Pure functions over the report: nothing
here reads a source or links to an action that writes one. The deployment
says how each pending item is cleared today (a chat command, a button), and
the page shows that text.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Sequence, Tuple

from .contract import AgentStatus, ApprovalRef
from .fleet import FleetReport

_LOUD_STATES = ("blocked_on_human", "failed")
REFRESH_S = 60


@dataclass(frozen=True)
class InboxEntry:
    """One item waiting on the operator, and where it is cleared today."""

    agent_id: str
    item: ApprovalRef
    how: str = ""


@dataclass(frozen=True)
class InboxSection:
    """A titled group of inbox entries, rendered oldest first."""

    heading: str
    entries: Tuple[InboxEntry, ...]
    note: str = ""


def needs_attention(status: AgentStatus) -> bool:
    """Blocked, failed or flagged. Everything else collapses on the board."""
    return status.state in _LOUD_STATES or bool(status.flags)


def approvals_section(
    report: FleetReport,
    how: Optional[Callable[[ApprovalRef], str]] = None,
    heading: str = "Approvals",
) -> InboxSection:
    """Every pending approval across the fleet, oldest first."""
    entries = [
        InboxEntry(s.agent_id, a, how(a) if how else "")
        for s in report.agents
        for a in s.pending_approvals
    ]
    entries.sort(key=lambda e: (e.item.requested_at, e.item.id))
    return InboxSection(heading=heading, entries=tuple(entries))


def _age(then: Optional[datetime], now: datetime) -> str:
    """Compact signed distance: "3h ago", "in 20m", or "never"."""
    if then is None:
        return "never"
    secs = int((now - then).total_seconds())
    span = abs(secs)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if span >= size:
            text = f"{span // size}{unit}"
            break
    else:
        text = f"{span}s"
    return f"{text} ago" if secs >= 0 else f"in {text}"


def attention_lines(report: FleetReport, limit: int = 15) -> List[str]:
    """Plain lines for a chat reply: blocked, failed and flagged agents only."""
    now = report.generated_at
    loud = [s for s in report.agents if needs_attention(s)]
    if not loud:
        return [f"All {len(report.agents)} agents quiet. Nothing blocked, failed or flagged."]
    lines = []
    for s in loud[:limit]:
        tail = f" [{', '.join(s.flags)}]" if s.flags else ""
        lines.append(f"{s.display_name}: {s.state}{tail}")
        if s.last_action is not None:
            lines.append(f"  {_age(s.last_action.at, now)}: {s.last_action.summary}")
        if s.pending_approvals:
            lines.append(f"  {len(s.pending_approvals)} pending approval(s)")
    if len(loud) > limit:
        lines.append(f"... and {len(loud) - limit} more")
    quiet = len(report.agents) - len(loud)
    lines.append(f"Quiet: {quiet}. Source gaps: {len(report.gaps)}.")
    return lines


_CSS = """
:root{--bg:#fafafa;--card:#fff;--line:#ddd;--fg:#1a1a1a;--mut:#666;--bad:#c62828;--warn:#a66b00;--ok:#2e6b3a}
@media (prefers-color-scheme:dark){:root{--bg:#0e0e12;--card:#17171d;--line:#2c2c36;--fg:#e4e4e4;--mut:#8a8a9a;--bad:#ff6b6b;--warn:#f4c95d;--ok:#7bc47f}}
*{box-sizing:border-box}body{margin:0 auto;padding:16px;max-width:860px;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
h1{font-size:20px;margin:0 0 2px}h2{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);margin:24px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-left-width:4px;padding:10px 12px;margin-bottom:8px}
.blocked_on_human,.failed{border-left-color:var(--bad)}.flagged{border-left-color:var(--warn)}
.st{font-weight:600}.mut{color:var(--mut);font-size:13px}.fl{color:var(--warn);font-size:13px}
code{font:13px ui-monospace,Menlo,monospace;word-break:break-all}summary{cursor:pointer;color:var(--mut)}
ul{padding-left:18px;margin:4px 0}
"""


def _esc(text: object) -> str:
    """HTML-escape any value, quotes included."""
    return html.escape(str(text), quote=True)


def _card(s: AgentStatus, now: datetime) -> str:
    """One agent card. Only fields with a source are shown."""
    cls = s.state if s.state in _LOUD_STATES else ("flagged" if s.flags else "")
    rows = [f'<b>{_esc(s.display_name)}</b> <span class="st">{_esc(s.state)}</span>']
    if s.flags:
        rows.append(f'<div class="fl">{_esc(", ".join(s.flags))}</div>')
    if s.current_task is not None:
        rows.append(f"<div>{_esc(s.current_task.id)}: {_esc(s.current_task.title)}</div>")
    if s.last_action is not None:
        rows.append(
            f'<div class="mut">{_esc(_age(s.last_action.at, now))}: '
            f"{_esc(s.last_action.summary)}</div>"
        )
    meta = [f"heartbeat {_age(s.last_heartbeat, now)}"]
    if s.next_scheduled_run is not None:
        meta.append(f"next {_age(s.next_scheduled_run, now)}")
    if s.pending_approvals:
        meta.append(f"{len(s.pending_approvals)} pending")
    if s.artifacts_recent:
        meta.append(f"{len(s.artifacts_recent)} recent artifacts")
    rows.append(f'<div class="mut">{_esc(" · ".join(meta))}</div>')
    return f'<div class="card {cls}" id="{_esc(s.agent_id)}">{"".join(rows)}</div>'


def _inbox(section: InboxSection, now: datetime) -> str:
    """One inbox section; an empty one says so in a line."""
    head = f"<h2>{_esc(section.heading)} ({len(section.entries)})</h2>"
    note = f'<p class="mut">{_esc(section.note)}</p>' if section.note else ""
    if not section.entries:
        return head + note + '<p class="mut">Nothing waiting.</p>'
    items = []
    for e in section.entries:
        how = f'<div class="mut">{_esc(e.how)}</div>' if e.how else ""
        items.append(
            f'<div class="card"><b>{_esc(e.item.summary)}</b>'
            f'<div class="mut">{_esc(e.agent_id)} · {_esc(_age(e.item.requested_at, now))}'
            f" · <code>{_esc(e.item.id)}</code></div>{how}</div>"
        )
    return head + note + "".join(items)


def render_html(
    report: FleetReport,
    inbox: Sequence[InboxSection] = (),
    title: str = "Fleet",
) -> str:
    """The whole page: loud agents, inbox sections, quiet agents folded, gaps."""
    now = report.generated_at
    loud = [s for s in report.agents if needs_attention(s)]
    quiet = [s for s in report.agents if not needs_attention(s)]
    waiting = sum(len(sec.entries) for sec in inbox)
    parts = [
        f"<h1>{_esc(title)}</h1>",
        f'<p class="mut">{len(report.agents)} agents · {len(loud)} need attention · '
        f"{waiting} waiting on you · as of {_esc(now.isoformat(timespec='seconds'))}</p>",
        "<h2>Needs attention</h2>",
        "".join(_card(s, now) for s in loud)
        or '<p class="mut">Nothing blocked, failed or flagged.</p>',
    ]
    parts += [_inbox(sec, now) for sec in inbox]
    parts.append(
        f"<h2>Quiet ({len(quiet)})</h2><details><summary>Show quiet agents</summary>"
        + "".join(_card(s, now) for s in quiet)
        + "</details>"
    )
    if report.gaps:
        parts.append(
            "<h2>Sources not read</h2><ul>"
            + "".join(f"<li>{_esc(g)}</li>" for g in report.gaps)
            + "</ul>"
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="refresh" content="{REFRESH_S}">'
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body>"
        + "".join(parts)
        + "</body></html>"
    )


__all__ = [
    "InboxEntry",
    "InboxSection",
    "needs_attention",
    "approvals_section",
    "attention_lines",
    "render_html",
]
