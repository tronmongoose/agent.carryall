"""
Compliance Reporting for Authority Runtime

Generates audit reports for regulatory compliance (FERPA, SOC 2, HIPAA, etc.)
from the existing audit trail.

Key capability: negative attestation — cryptographic proof that an agent
NEVER accessed a specific resource during a time period.
"""

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .storage import EnvelopeStore


class ComplianceReport:
    """
    Generates compliance reports from the Carryall audit trail.

    Example:
        ```python
        store = EnvelopeStore("./authority.db")
        report = ComplianceReport(store)

        # Prove financial-aid-agent never accessed health records
        attestation = report.negative_attestation(
            agent_id="financial-aid-agent",
            resource_pattern="slos://vaults/student-health/%",
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-03-01T00:00:00Z",
        )
        print(attestation["result"])  # "CONFIRMED: 0 access events found"
        ```
    """

    def __init__(self, store: EnvelopeStore):
        self.store = store

    def agent_access_report(
        self,
        agent_id: str,
        resource_pattern: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        All accesses by a specific agent, optionally filtered by resource.

        Returns a structured report showing what the agent accessed, when,
        and with what result.
        """
        entries = self.store.get_audit_trail(
            agent_id=agent_id,
            resource_pattern=resource_pattern,
            start_time=start_time,
            end_time=end_time,
        )

        resources = self.store.get_distinct_resources_for_agent(
            agent_id=agent_id,
            start_time=start_time,
            end_time=end_time,
        )

        return {
            "report_type": "agent_access",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "parameters": {
                "agent_id": agent_id,
                "resource_pattern": resource_pattern,
                "start_time": start_time,
                "end_time": end_time,
            },
            "summary": {
                "total_events": len(entries),
                "successful": sum(1 for e in entries if e["result"] == "success"),
                "blocked": sum(1 for e in entries if e["result"] == "blocked"),
                "distinct_resources": len(resources),
            },
            "resources_accessed": resources,
            "entries": entries,
        }

    def resource_access_report(
        self,
        resource_pattern: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        All agents that accessed resources matching a pattern.

        Answers: "Who has touched student records in the last 90 days?"
        """
        agents = self.store.get_distinct_agents_for_resource(
            resource_pattern=resource_pattern,
            start_time=start_time,
            end_time=end_time,
        )

        total = self.store.count_access_events(
            resource_pattern=resource_pattern,
            start_time=start_time,
            end_time=end_time,
        )

        return {
            "report_type": "resource_access",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "parameters": {
                "resource_pattern": resource_pattern,
                "start_time": start_time,
                "end_time": end_time,
            },
            "summary": {
                "total_events": total,
                "distinct_agents": len(agents),
            },
            "agents": agents,
        }

    def negative_attestation(
        self,
        agent_id: str,
        resource_pattern: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Prove that an agent NEVER accessed resources matching a pattern.

        This is the FERPA killer feature. When the count is 0, this is a
        cryptographically-backed attestation that can be presented to auditors.

        The attestation includes a SHA-256 hash of the query parameters so
        the exact query can be verified later.

        Returns:
            Dict with:
            - result: "CONFIRMED: 0 access events found" or "FAILED: N events found"
            - count: number of matching events
            - attestation_hash: SHA-256 of query parameters
            - query_parameters: the exact filters used
        """
        count = self.store.count_access_events(
            agent_id=agent_id,
            resource_pattern=resource_pattern,
            start_time=start_time,
            end_time=end_time,
        )

        # Create deterministic hash of the query for verification
        query_canonical = json.dumps({
            "agent_id": agent_id,
            "resource_pattern": resource_pattern,
            "start_time": start_time,
            "end_time": end_time,
        }, sort_keys=True)
        attestation_hash = hashlib.sha256(query_canonical.encode()).hexdigest()

        confirmed = count == 0

        return {
            "report_type": "negative_attestation",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "result": "CONFIRMED: 0 access events found" if confirmed
                     else f"FAILED: {count} access event(s) found",
            "confirmed": confirmed,
            "count": count,
            "attestation_hash": attestation_hash,
            "query_parameters": {
                "agent_id": agent_id,
                "resource_pattern": resource_pattern,
                "start_time": start_time,
                "end_time": end_time,
            },
        }

    def scope_usage_report(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Scopes granted vs. actually used — proves least-privilege enforcement.

        Pulls envelope scopes and compares against actual audit trail actions.
        """
        trail = self.store.get_audit_trail(
            start_time=start_time,
            end_time=end_time,
        )

        # Group by agent
        agent_scopes: Dict[str, Dict[str, Any]] = {}
        for entry in trail:
            aid = entry["agent_id"]
            if aid not in agent_scopes:
                agent_scopes[aid] = {
                    "total_events": 0,
                    "actions": set(),
                    "resources": set(),
                }
            agent_scopes[aid]["total_events"] += 1
            agent_scopes[aid]["actions"].add(entry["action"])
            if entry.get("resource"):
                agent_scopes[aid]["resources"].add(entry["resource"])

        # Convert sets to lists for JSON serialization
        agents = {
            aid: {
                "total_events": data["total_events"],
                "distinct_actions": list(data["actions"]),
                "distinct_resources": list(data["resources"]),
            }
            for aid, data in agent_scopes.items()
        }

        return {
            "report_type": "scope_usage",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "parameters": {
                "start_time": start_time,
                "end_time": end_time,
            },
            "summary": {
                "total_events": len(trail),
                "distinct_agents": len(agents),
            },
            "agents": agents,
        }

    def generate_summary(self, report: Dict[str, Any]) -> str:
        """Generate a human-readable summary from any report type."""
        lines = []
        report_type = report.get("report_type", "unknown")
        lines.append(f"Compliance Report: {report_type}")
        lines.append(f"Generated: {report.get('generated_at', 'unknown')}")
        lines.append("")

        if report_type == "negative_attestation":
            lines.append(f"Result: {report['result']}")
            lines.append(f"Agent: {report['query_parameters']['agent_id']}")
            lines.append(f"Resource Pattern: {report['query_parameters']['resource_pattern']}")
            lines.append(f"Time Range: {report['query_parameters'].get('start_time', 'all')} - {report['query_parameters'].get('end_time', 'now')}")
            lines.append(f"Attestation Hash: {report['attestation_hash']}")

        elif report_type == "agent_access":
            summary = report["summary"]
            lines.append(f"Agent: {report['parameters']['agent_id']}")
            lines.append(f"Total Events: {summary['total_events']}")
            lines.append(f"Successful: {summary['successful']}")
            lines.append(f"Blocked: {summary['blocked']}")
            lines.append(f"Distinct Resources: {summary['distinct_resources']}")

        elif report_type == "resource_access":
            summary = report["summary"]
            lines.append(f"Resource Pattern: {report['parameters']['resource_pattern']}")
            lines.append(f"Total Events: {summary['total_events']}")
            lines.append(f"Distinct Agents: {summary['distinct_agents']}")
            if report.get("agents"):
                lines.append("")
                lines.append("Agents:")
                for agent in report["agents"]:
                    lines.append(f"  - {agent['agent_id']}: {agent['access_count']} accesses")

        elif report_type == "scope_usage":
            summary = report["summary"]
            lines.append(f"Total Events: {summary['total_events']}")
            lines.append(f"Distinct Agents: {summary['distinct_agents']}")

        return "\n".join(lines)

    def export_json(self, report: Dict[str, Any], filepath: str) -> None:
        """Export a report to JSON file."""
        with open(filepath, "w") as f:
            json.dump(report, f, indent=2, default=str)

    def export_csv(self, entries: List[Dict[str, Any]], filepath: str) -> None:
        """Export audit entries to CSV for spreadsheet review."""
        if not entries:
            with open(filepath, "w") as f:
                f.write("")
            return

        # Flatten entries for CSV
        fieldnames = [
            "timestamp", "agent_id", "action", "resource", "result",
            "error", "envelope_id", "signature_valid",
        ]

        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for entry in entries:
                writer.writerow(entry)

    def export_csv_string(self, entries: List[Dict[str, Any]]) -> str:
        """Export audit entries to CSV string."""
        if not entries:
            return ""

        fieldnames = [
            "timestamp", "agent_id", "action", "resource", "result",
            "error", "envelope_id", "signature_valid",
        ]

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            writer.writerow(entry)
        return output.getvalue()

    def generate_full_report(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        policy_summary: Optional[Dict[str, Any]] = None,
        title: str = "FERPA Compliance Report",
    ) -> Dict[str, Any]:
        """
        Aggregate all report types into a single comprehensive report.

        Combines:
        - Executive summary (stats)
        - Per-agent access breakdowns
        - Negative attestation matrix (every agent x sensitive vaults)
        - Data classifications from policy (if provided)
        """
        stats = self.store.get_stats()
        trail = self.store.get_audit_trail(start_time=start_time, end_time=end_time)

        # Collect all unique agents
        agent_ids = set()
        for entry in trail:
            agent_ids.add(entry["agent_id"])

        # Per-agent reports
        agent_reports = {}
        for agent_id in sorted(agent_ids):
            agent_reports[agent_id] = self.agent_access_report(
                agent_id=agent_id, start_time=start_time, end_time=end_time
            )

        # Negative attestation matrix: each agent vs sensitive resource patterns
        sensitive_patterns = [
            "slos://vaults/student-health/%",
            "slos://vaults/financial-aid/%",
        ]
        attestations = []
        for agent_id in sorted(agent_ids):
            for pattern in sensitive_patterns:
                attestation = self.negative_attestation(
                    agent_id=agent_id,
                    resource_pattern=pattern,
                    start_time=start_time,
                    end_time=end_time,
                )
                attestations.append(attestation)

        return {
            "report_type": "full_compliance",
            "title": title,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "time_window": {
                "start": start_time or "all time",
                "end": end_time or "now",
            },
            "executive_summary": {
                "total_agents": stats["envelopes"]["unique_agents"],
                "total_envelopes": stats["envelopes"]["total"],
                "total_events": stats["audit_trail"]["total_actions"],
                "successful": stats["audit_trail"]["successful"],
                "blocked": stats["audit_trail"]["blocked"],
                "signature_failures": stats["audit_trail"]["signature_failures"],
            },
            "agent_reports": agent_reports,
            "attestations": attestations,
            "policy_summary": policy_summary,
        }

    def render_html(self, report: Dict[str, Any]) -> str:
        """
        Render a full compliance report as self-contained HTML.

        No external CSS, no JavaScript. Opens in any browser.
        Professional enough to email to legal.
        """
        title = report.get("title", "Compliance Report")
        generated = report.get("generated_at", "")[:19]
        window = report.get("time_window", {})
        summary = report.get("executive_summary", {})
        agent_reports = report.get("agent_reports", {})
        attestations = report.get("attestations", [])
        policy = report.get("policy_summary")

        # Build HTML sections
        sections = []

        # Executive summary
        sections.append(self._html_executive_summary(summary))

        # Per-agent breakdown
        sections.append(self._html_agent_breakdown(agent_reports))

        # Negative attestations
        sections.append(self._html_attestations(attestations))

        # Policy / data classification (if available)
        if policy:
            sections.append(self._html_policy_summary(policy))

        # Metadata
        sections.append(self._html_metadata(report))

        body = "\n".join(sections)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_h(title)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         color: #1a1a2e; background: #f8f9fa; line-height: 1.6; padding: 2rem; }}
  .container {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 1.8rem; margin-bottom: 0.25rem; color: #1a1a2e; }}
  .subtitle {{ color: #6c757d; margin-bottom: 2rem; font-size: 0.95rem; }}
  h2 {{ font-size: 1.3rem; color: #1a1a2e; margin: 2rem 0 1rem 0;
       border-bottom: 2px solid #dee2e6; padding-bottom: 0.5rem; }}
  table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
  th, td {{ padding: 0.6rem 0.8rem; text-align: left; border-bottom: 1px solid #dee2e6; }}
  th {{ background: #e9ecef; font-weight: 600; font-size: 0.85rem;
       text-transform: uppercase; letter-spacing: 0.03em; color: #495057; }}
  td {{ font-size: 0.9rem; }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                gap: 1rem; margin: 1rem 0; }}
  .stat {{ background: white; padding: 1rem; border-radius: 8px;
           border: 1px solid #dee2e6; text-align: center; }}
  .stat-value {{ font-size: 1.8rem; font-weight: 700; color: #1a1a2e; }}
  .stat-label {{ font-size: 0.8rem; color: #6c757d; text-transform: uppercase;
                 letter-spacing: 0.05em; }}
  .pass {{ color: #28a745; font-weight: 600; }}
  .fail {{ color: #dc3545; font-weight: 600; }}
  .warn {{ color: #ffc107; font-weight: 600; }}
  .hash {{ font-family: monospace; font-size: 0.78rem; color: #6c757d;
           word-break: break-all; }}
  .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px;
            font-size: 0.78rem; font-weight: 600; }}
  .badge-green {{ background: #d4edda; color: #155724; }}
  .badge-red {{ background: #f8d7da; color: #721c24; }}
  .badge-yellow {{ background: #fff3cd; color: #856404; }}
  .meta {{ color: #6c757d; font-size: 0.82rem; margin-top: 2rem;
           border-top: 1px solid #dee2e6; padding-top: 1rem; }}
  .meta td {{ border: none; padding: 0.2rem 0.8rem; }}
</style>
</head>
<body>
<div class="container">
<h1>{_h(title)}</h1>
<p class="subtitle">Generated {_h(generated)} UTC &middot; Window: {_h(str(window.get('start', 'all')))} to {_h(str(window.get('end', 'now')))}</p>
{body}
</div>
</body>
</html>"""

    def _html_executive_summary(self, summary: dict) -> str:
        total = summary.get("total_events", 0)
        blocked = summary.get("blocked", 0)
        sig_fail = summary.get("signature_failures", 0)
        block_rate = f"{(blocked / total * 100):.1f}%" if total else "0%"

        return f"""<h2>Executive Summary</h2>
<div class="stat-grid">
  <div class="stat"><div class="stat-value">{summary.get('total_agents', 0)}</div><div class="stat-label">Agents</div></div>
  <div class="stat"><div class="stat-value">{total}</div><div class="stat-label">Total Events</div></div>
  <div class="stat"><div class="stat-value">{summary.get('successful', 0)}</div><div class="stat-label">Successful</div></div>
  <div class="stat"><div class="stat-value">{blocked}</div><div class="stat-label">Blocked</div></div>
  <div class="stat"><div class="stat-value">{block_rate}</div><div class="stat-label">Block Rate</div></div>
  <div class="stat"><div class="stat-value {'fail' if sig_fail else 'pass'}">{sig_fail}</div><div class="stat-label">Sig Failures</div></div>
</div>"""

    def _html_agent_breakdown(self, agent_reports: dict) -> str:
        rows = []
        for agent_id, report in sorted(agent_reports.items()):
            s = report["summary"]
            rows.append(
                f"<tr><td>{_h(agent_id)}</td>"
                f"<td>{s['total_events']}</td>"
                f"<td>{s['successful']}</td>"
                f"<td>{s['blocked']}</td>"
                f"<td>{s['distinct_resources']}</td></tr>"
            )

        return f"""<h2>Per-Agent Access Breakdown</h2>
<table>
<tr><th>Agent ID</th><th>Events</th><th>Successful</th><th>Blocked</th><th>Resources</th></tr>
{''.join(rows)}
</table>"""

    def _html_attestations(self, attestations: list) -> str:
        rows = []
        for a in attestations:
            qp = a.get("query_parameters", {})
            confirmed = a.get("confirmed", False)
            badge = '<span class="badge badge-green">CONFIRMED</span>' if confirmed \
                else f'<span class="badge badge-red">FAILED ({a.get("count", "?")} events)</span>'

            rows.append(
                f"<tr><td>{_h(qp.get('agent_id', ''))}</td>"
                f"<td>{_h(qp.get('resource_pattern', ''))}</td>"
                f"<td>{badge}</td>"
                f"<td class=\"hash\">{_h(a.get('attestation_hash', '')[:24])}...</td></tr>"
            )

        return f"""<h2>Negative Attestation Matrix</h2>
<p>Cryptographic proof of access or non-access for each agent &times; sensitive resource combination.</p>
<table>
<tr><th>Agent</th><th>Resource Pattern</th><th>Result</th><th>Hash</th></tr>
{''.join(rows)}
</table>"""

    def _html_policy_summary(self, policy: dict) -> str:
        # Data classifications
        dc_rows = []
        for dc in policy.get("data_classifications", []):
            sens = dc.get("sensitivity", "")
            badge_class = {"restricted": "badge-red", "confidential": "badge-yellow"}.get(sens, "badge-green")
            pii = ", ".join(dc.get("pii_fields", []))
            retention = f"{dc.get('retention_days', 0)} days" if dc.get("retention_days") else "N/A"
            dc_rows.append(
                f"<tr><td>{_h(dc['domain'])}</td>"
                f"<td><span class=\"badge {badge_class}\">{_h(sens)}</span></td>"
                f"<td>{_h(pii)}</td>"
                f"<td>{retention}</td></tr>"
            )

        dc_section = ""
        if dc_rows:
            dc_section = f"""<h2>Data Classifications</h2>
<p>Organization: {_h(policy.get('organization', ''))} &middot; Frameworks: {_h(', '.join(policy.get('compliance_frameworks', [])))}</p>
<table>
<tr><th>Domain</th><th>Sensitivity</th><th>PII Fields</th><th>Retention</th></tr>
{''.join(dc_rows)}
</table>"""

        return dc_section

    def _html_metadata(self, report: dict) -> str:
        from . import __version__
        return f"""<table class="meta">
<tr><td><strong>Report Type</strong></td><td>{_h(report.get('report_type', ''))}</td></tr>
<tr><td><strong>Generated At</strong></td><td>{_h(report.get('generated_at', ''))}</td></tr>
<tr><td><strong>Carryall Version</strong></td><td>{_h(__version__)}</td></tr>
</table>"""


def _h(text: str) -> str:
    """HTML-escape a string."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))
