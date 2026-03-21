#!/usr/bin/env python3
"""ARGUS Runner — Entrypoint for the data locality scanner.

Usage:
    python -m agents.argus.runner --config agents/argus/config/domains.yaml
    python -m agents.argus.runner --config agents/argus/config/domains.yaml --dry-run
    python -m agents.argus.runner --config agents/argus/config/domains.yaml --daemon --interval-seconds 3600

ARGUS never opens an inbound socket or message queue.
It has no API. Other agents cannot invoke it.
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "usecases"))
from notify import notify

from agents.argus.models.finding import Finding, FindingType, Severity
from agents.argus.scanner.data_locality import DataLocalityScanner, ScanConfig

# Poisoned environment variables — if any are set, someone is trying
# to influence ARGUS. Emit a CRITICAL finding and escalate.
POISONED_ENV_VARS = [
    "ARGUS_TASK",
    "ARGUS_OVERRIDE",
    "ARGUS_SKIP_DOMAIN",
    "SLOS_AGENT_INSTRUCTION",
]

DEFAULT_AUDIT_LOG = "agents/argus/audit.jsonl"
DEFAULT_ESCALATION_LOG = "agents/argus/escalation.jsonl"


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter."""

    def format(self, record):
        return json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }, separators=(",", ":"))


def setup_logging(level: str):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("argus")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)
    return root


def check_isolation(escalation_log: str) -> list[Finding]:
    """Check for poisoned environment variables before any other work."""
    findings = []
    for var in POISONED_ENV_VARS:
        if var in os.environ:
            finding = Finding.create(
                finding_type=FindingType.AGENT_INVOKED_AUDITOR,
                severity=Severity.CRITICAL,
                source_path="<environment>",
                source_domain="unknown",
                detected_in_domain="argus",
                pattern_type="poisoned_env_var",
                matched_value=f"{var}={os.environ[var]}",
                description=(
                    f"Poisoned environment variable detected: {var}. "
                    "An agent may be attempting to influence ARGUS."
                ),
            )
            findings.append(finding)

    if findings:
        _write_escalation(findings, escalation_log)

    return findings


def _write_escalation(findings: list[Finding], escalation_log: str):
    """Write findings to the escalation log with fsync."""
    Path(escalation_log).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(escalation_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        for f in findings:
            line = f.to_log_line() + "\n"
            os.write(fd, line.encode())
            os.fsync(fd)
    finally:
        os.close(fd)


def run_scan(config_path: str, audit_log: str, escalation_log: str,
             dry_run: bool, logger: logging.Logger) -> int:
    """Execute a single scan. Returns exit code (1 if CRITICAL findings)."""
    # Isolation check first
    isolation_findings = check_isolation(escalation_log)
    for f in isolation_findings:
        logger.critical("Isolation violation: %s", f.description)

    # Load config and run scanner
    logger.info("Loading config: %s", config_path)
    config = ScanConfig(config_path)

    logger.info("Starting scan across %d domains", len(config.domains))
    scanner = DataLocalityScanner(config)

    audit_path = None if dry_run else audit_log
    if audit_path:
        Path(audit_path).parent.mkdir(parents=True, exist_ok=True)

    scan_findings = scanner.run(audit_log_path=audit_path)
    all_findings = isolation_findings + scan_findings

    # Separate critical findings for escalation
    critical = [f for f in scan_findings if f.severity == Severity.CRITICAL]
    if critical and not dry_run:
        _write_escalation(critical, escalation_log)

    # Print structured summary
    summary = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "domains_scanned": len(config.domains),
        "total_findings": len(all_findings),
        "by_severity": {
            "CRITICAL": sum(1 for f in all_findings if f.severity == Severity.CRITICAL),
            "HIGH": sum(1 for f in all_findings if f.severity == Severity.HIGH),
            "MEDIUM": sum(1 for f in all_findings if f.severity == Severity.MEDIUM),
            "LOW": sum(1 for f in all_findings if f.severity == Severity.LOW),
        },
        "dry_run": dry_run,
    }

    if dry_run:
        summary["findings"] = [
            {
                "severity": f.severity.value,
                "type": f.finding_type.value,
                "pattern": f.pattern_type,
                "source_domain": f.source_domain,
                "detected_in": f.detected_in_domain,
                "file": f.source_path,
                "excerpt": f.matched_excerpt,
            }
            for f in all_findings
        ]

    print(json.dumps(summary, indent=2))

    logger.info(
        "Scan complete: %d findings (%d critical)",
        len(all_findings), summary["by_severity"]["CRITICAL"],
    )

    return 1 if summary["by_severity"]["CRITICAL"] > 0 else 0


def format_brief(config_path: str, audit_log: str, escalation_log: str,
                  logger: logging.Logger) -> str:
    """Run a scan and return a formatted Telegram brief."""
    config = ScanConfig(config_path)
    scanner = DataLocalityScanner(config)
    scan_findings = scanner.run(audit_log_path=None)  # dry-run scan
    isolation_findings = check_isolation(escalation_log)
    all_findings = isolation_findings + scan_findings

    now = datetime.now(timezone.utc)
    n_domains = len(config.domains)
    n_crit = sum(1 for f in all_findings if f.severity == Severity.CRITICAL)
    n_high = sum(1 for f in all_findings if f.severity == Severity.HIGH)
    n_med = sum(1 for f in all_findings if f.severity == Severity.MEDIUM)

    status = "CLEAN" if len(all_findings) == 0 else f"{len(all_findings)} FINDINGS"
    if n_crit > 0:
        status = f"CRITICAL ({n_crit})"

    lines = [
        "\U0001f6e1 *ARGUS Security Report*",
        "",
        f"Domains scanned: {n_domains}",
        f"Findings: {len(all_findings)}",
        f"  CRITICAL: {n_crit} | HIGH: {n_high} | MEDIUM: {n_med}",
        "",
        f"Status: *{status}*",
        f"Last scan: {now.strftime('%Y-%m-%d %H:%M')} UTC",
    ]

    if all_findings:
        lines.append("")
        lines.append("Top findings:")
        for f in all_findings[:5]:
            lines.append(f"  {f.severity.value}: {f.pattern_type} in {f.source_domain}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="ARGUS — Data Locality & Cross-Domain Scanner"
    )
    parser.add_argument(
        "--config", default="agents/argus/config/domains.yaml",
        help="Path to domains.yaml config",
    )
    parser.add_argument(
        "--audit-log", default=DEFAULT_AUDIT_LOG,
        help="Path to append-only audit log",
    )
    parser.add_argument(
        "--escalation-log", default=DEFAULT_ESCALATION_LOG,
        help="Path to critical findings escalation log",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print findings without writing audit log",
    )
    parser.add_argument(
        "--brief", action="store_true",
        help="Print scan summary as formatted Telegram brief",
    )
    parser.add_argument(
        "--send", action="store_true",
        help="Run scan and send brief to Telegram",
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Run continuously with --interval-seconds between scans",
    )
    parser.add_argument(
        "--interval-seconds", type=int, default=3600,
        help="Seconds between scans in daemon mode (default: 3600)",
    )
    args = parser.parse_args()

    logger = setup_logging(args.log_level)

    # Ensure log file permissions
    for log_path in [args.audit_log, args.escalation_log]:
        if os.path.exists(log_path):
            os.chmod(log_path, 0o600)

    if args.brief or args.send:
        brief = format_brief(args.config, args.audit_log, args.escalation_log, logger)
        print(brief)
        if args.send:
            notify(brief, title="ARGUS Security Report", topic="argus", sensitive=False)
        return

    if not args.daemon:
        exit_code = run_scan(
            args.config, args.audit_log, args.escalation_log,
            args.dry_run, logger,
        )
        sys.exit(exit_code)

    # Daemon mode
    shutdown = False

    def handle_signal(signum, _frame):
        nonlocal shutdown
        logger.info("Received signal %d, shutting down", signum)
        shutdown = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("ARGUS daemon starting (interval: %ds)", args.interval_seconds)

    while not shutdown:
        run_scan(
            args.config, args.audit_log, args.escalation_log,
            args.dry_run, logger,
        )
        # Sleep in small increments to respond to signals promptly
        for _ in range(args.interval_seconds):
            if shutdown:
                break
            time.sleep(1)

    logger.info("ARGUS daemon stopped")


if __name__ == "__main__":
    main()
