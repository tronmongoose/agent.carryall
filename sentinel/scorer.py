"""
Sentinel — adversarial scoring engine for Carryall audit events.

Scores policy decisions and security findings from SLOS audit trails
and Argus scanner output. Produces BLOCK/FLAG/PASS recommendations.

This module contains the scoring logic only. The SSE streaming and
HTTP endpoints live in the deployment's dashboard server.
"""

import json
import os
from datetime import datetime


SENTINEL_SEVERITY_WEIGHT = {"CRITICAL": 100, "HIGH": 75, "MEDIUM": 40, "LOW": 10}


def score_sentinel_event(entry: dict) -> dict:
    """Score a policy decision or finding. Returns {score, recommendation, factors}."""
    factors = []
    score = 0

    decision = entry.get("decision", entry.get("result", "")).lower()
    action = entry.get("action", "")
    vault = entry.get("vault", "")
    agent = entry.get("agent_id", "")
    severity = entry.get("severity", "")

    # Denial = high score
    if decision in ("deny", "denied", "blocked"):
        score += 60
        factors.append("policy_denial")

    # Write actions are higher risk
    if "write" in action.lower():
        score += 15
        factors.append("write_operation")

    # Sensitive vaults
    if vault in ("health", "finance"):
        score += 10
        factors.append(f"sensitive_vault:{vault}")

    # ARGUS severity
    if severity:
        score += SENTINEL_SEVERITY_WEIGHT.get(severity, 0)
        factors.append(f"argus_severity:{severity}")

    # Cross-domain access
    if entry.get("finding_type") in ("CROSS_DOMAIN_LEAK", "DATA_IN_WRONG_DOMAIN"):
        score += 30
        factors.append("cross_domain_violation")

    # Envelope signature failure
    if entry.get("signature_valid") == 0:
        score += 50
        factors.append("invalid_signature")

    # Trifecta contamination: finance operation with untrusted content in pipeline
    purpose = entry.get("purpose", "")
    if vault in ("finance",) and "UNTRUSTED" in purpose:
        score += 80
        factors.append("trifecta_contamination")

    # ACL denial — context store blocked a cross-vault read/write
    if "ACL DENIED" in purpose:
        score += 40
        factors.append("context_acl_denied")

    score = min(100, score)

    if score >= 70:
        recommendation = "BLOCK"
    elif score >= 30:
        recommendation = "FLAG"
    else:
        recommendation = "PASS"

    return {
        "score": score,
        "recommendation": recommendation,
        "factors": factors,
        "confidence": min(1.0, 0.5 + len(factors) * 0.15),
    }


def score_events(events: list, limit: int = 50, severity_min: str = None) -> list:
    """Score a list of audit events and return sorted by score descending.

    Args:
        events: Raw audit/finding dicts to score.
        limit: Max events to return.
        severity_min: Filter by minimum recommendation level (BLOCK, FLAG, PASS).
    """
    scored = []
    for entry in events:
        sentinel = score_sentinel_event(entry)
        scored.append({
            "id": entry.get("id", entry.get("finding_id", "")),
            "timestamp": entry.get("timestamp", ""),
            "agent_id": entry.get("agent_id", entry.get("agent_context", "")),
            "action": entry.get("action", entry.get("finding_type", "")),
            "vault": entry.get("vault", entry.get("detected_in_domain", "")),
            "decision": entry.get("decision", entry.get("severity", "")),
            "purpose": entry.get("purpose", entry.get("description", "")),
            "sentinel_score": sentinel["score"],
            "recommendation": sentinel["recommendation"],
            "factors": sentinel["factors"],
            "confidence": sentinel["confidence"],
        })

    scored.sort(key=lambda e: (-e["sentinel_score"], e.get("timestamp", "")))

    if severity_min:
        min_score = {"BLOCK": 70, "FLAG": 30, "PASS": 0}.get(severity_min.upper(), 0)
        scored = [e for e in scored if e["sentinel_score"] >= min_score]

    return scored[:limit]


def compute_stats(events: list) -> dict:
    """Sentinel summary stats from a list of already-scored events."""
    total = len(events)
    blocks = sum(1 for e in events if e.get("recommendation") == "BLOCK")
    flags = sum(1 for e in events if e.get("recommendation") == "FLAG")
    passes = sum(1 for e in events if e.get("recommendation") == "PASS")

    counts: dict = {}
    for e in events:
        for f in e.get("factors", []):
            counts[f] = counts.get(f, 0) + 1
    top_factors = sorted(
        [{"factor": k, "count": v} for k, v in counts.items()],
        key=lambda x: -x["count"],
    )[:10]

    return {
        "total": total,
        "blocks": blocks,
        "flags": flags,
        "passes": passes,
        "block_rate": round(blocks / total * 100, 1) if total else 0,
        "top_factors": top_factors,
    }
