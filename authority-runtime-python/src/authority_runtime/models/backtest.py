"""Replay historical model decisions against a ModelPolicy. Pure: no network, no writes.

Exports: ReplayRecord, BacktestReport, replay, records_from_audit, records_from_usage_jsonl.

Two sources are supported:
  - audit rows written by models.audit.resolve_audited (provider, model, recorded result)
  - usage JSONL from clawrouter log_usage ({"route", "model"}) or router.JsonlUsageLogger
    ({"tier", "model"}); the caller maps route/tier -> provider since those logs lack it
A "flip" is a record whose recorded allow/deny differs from the replayed outcome, i.e.
exactly the traffic a policy change would have affected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from .audit import AUDIT_ACTION_PREFIX
from .policy import ModelPolicy, ModelPolicyError

MAX_FLIPS_REPORTED = 1000


@dataclass(frozen=True)
class ReplayRecord:
    """One historical model choice."""

    provider: Any
    model_id: Any
    recorded: Optional[str] = None
    ref: str = ""
    timestamp: Optional[str] = None


@dataclass
class BacktestReport:
    """Aggregate outcome of replaying records against one policy version."""

    policy_version: str
    total: int = 0
    allowed: int = 0
    denied: int = 0
    by_reason: Dict[str, int] = field(default_factory=dict)
    by_model: Dict[str, Dict[str, int]] = field(default_factory=dict)
    flips: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable form with deterministic key order."""
        return {
            "policy_version": self.policy_version, "total": self.total,
            "allowed": self.allowed, "denied": self.denied,
            "by_reason": dict(sorted(self.by_reason.items())),
            "by_model": {k: dict(sorted(v.items())) for k, v in sorted(self.by_model.items())},
            "flips": self.flips,
        }


def replay(records: Iterable[ReplayRecord], policy: ModelPolicy) -> BacktestReport:
    """Resolve every record against `policy` and tally allow/deny, reasons and flips."""
    report = BacktestReport(policy.version)
    for rec in records:
        report.total += 1
        try:
            policy.resolve(rec.provider, rec.model_id)
            now, reason = "allow", None
            report.allowed += 1
        except ModelPolicyError as e:
            now, reason = "deny", e.reason
            report.denied += 1
            report.by_reason[e.reason] = report.by_reason.get(e.reason, 0) + 1
        key = f"{rec.provider}/{rec.model_id}"
        counts = report.by_model.setdefault(key, {})
        counts[now] = counts.get(now, 0) + 1
        if rec.recorded in ("allow", "deny") and rec.recorded != now:
            if len(report.flips) < MAX_FLIPS_REPORTED:
                report.flips.append({"ref": rec.ref, "model": key, "was": rec.recorded,
                                     "now": now, "reason": reason, "timestamp": rec.timestamp})
    return report


def records_from_audit(rows: Iterable[Mapping[str, Any]]) -> Iterator[ReplayRecord]:
    """Extract model decisions from EnvelopeStore.get_audit_trail rows."""
    for row in rows:
        if not str(row.get("action", "")).startswith(AUDIT_ACTION_PREFIX):
            continue
        md = row.get("metadata") or {}
        if not isinstance(md, dict) or "model_id" not in md:
            continue
        yield ReplayRecord(md.get("provider"), md.get("model_id"), row.get("result"),
                           ref=f"audit:{row.get('id')}", timestamp=row.get("timestamp"))


def records_from_usage_jsonl(lines: Iterable[str], provider_for_route: Mapping[str, str]
                             ) -> Tuple[List[ReplayRecord], int]:
    """Parse usage JSONL lines. Returns (records, skipped). Unmapped routes are skipped."""
    records: List[ReplayRecord] = []
    skipped = 0
    for n, line in enumerate(lines, 1):
        try:
            obj = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        route = obj.get("route", obj.get("tier")) if isinstance(obj, dict) else None
        provider = provider_for_route.get(route) if isinstance(route, str) else None
        if provider is None or "model" not in obj:
            skipped += 1
            continue
        records.append(ReplayRecord(provider, obj["model"], None, ref=f"line:{n}",
                                    timestamp=obj.get("timestamp")))
    return records, skipped
