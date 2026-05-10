"""HarnessAuditor: walks a config root, runs rules, emits findings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Protocol, Sequence


class AuditError(RuntimeError):
    """Raised on auditor configuration errors (not on rule violations)."""


SEVERITIES = ("info", "low", "medium", "high", "critical")


@dataclass
class Finding:
    """A single rule violation observed during a scan."""

    rule_id: str
    severity: str
    summary: str
    file: Optional[str] = None
    detail: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise AuditError(
                f"Finding severity {self.severity!r} not in {SEVERITIES}"
            )

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


class _RuleProtocol(Protocol):
    id: str
    severity: str
    description: str

    def check(self, config_root: Path) -> Sequence[Finding]: ...


class HarnessAuditor:
    """Run a set of rules against a config root.

    Usage:
        auditor = HarnessAuditor(config_root="/path/to/deployment")
        auditor.register(my_rule)
        findings = auditor.scan()  # also writes to findings_log if set
    """

    def __init__(
        self,
        config_root: Path | str,
        rules: Optional[Sequence[_RuleProtocol]] = None,
        findings_log: Optional[Path | str] = None,
    ) -> None:
        root = Path(config_root)
        if not root.exists():
            raise AuditError(f"config_root does not exist: {root}")
        if not root.is_dir():
            raise AuditError(f"config_root must be a directory: {root}")
        self.config_root = root
        self._rules: List[_RuleProtocol] = list(rules or [])
        self.findings_log = Path(findings_log) if findings_log else None

    def register(self, rule: _RuleProtocol) -> None:
        """Register a rule. Order is preserved; the same id may not register twice."""
        existing = {r.id for r in self._rules}
        if rule.id in existing:
            raise AuditError(f"Rule {rule.id!r} already registered")
        self._rules.append(rule)

    @property
    def rules(self) -> List[_RuleProtocol]:
        return list(self._rules)

    def scan(self) -> List[Finding]:
        """Run every registered rule. Returns all findings; appends to log if set."""
        findings: List[Finding] = []
        for rule in self._rules:
            try:
                rule_findings = list(rule.check(self.config_root))
            except Exception as e:  # rule should never crash the scan
                findings.append(
                    Finding(
                        rule_id=rule.id,
                        severity="high",
                        summary=f"Rule {rule.id} raised an exception",
                        detail=f"{type(e).__name__}: {e}",
                    )
                )
                continue
            for f in rule_findings:
                if not isinstance(f, Finding):
                    findings.append(
                        Finding(
                            rule_id=rule.id,
                            severity="high",
                            summary=f"Rule {rule.id} yielded non-Finding object",
                            detail=repr(f),
                        )
                    )
                    continue
                findings.append(f)

        if self.findings_log is not None:
            self.findings_log.parent.mkdir(parents=True, exist_ok=True)
            with self.findings_log.open("a", encoding="utf-8") as fp:
                for f in findings:
                    fp.write(f.to_jsonl() + "\n")

        return findings


__all__ = ["HarnessAuditor", "Finding", "AuditError"]
