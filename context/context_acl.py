"""
SLOS Context ACL — vault-scoped access control for context operations.

Enforces read/write boundaries per agent based on policy YAML definitions.
Wraps ContextStore operations so agents can only ingest into their own vaults
and only read vaults they have explicit scope for.

Source of truth: policies/*.yaml (vault:domain:read / vault:domain:write scopes)
"""

import logging
from pathlib import Path

import yaml

log = logging.getLogger("slos-context-acl")

POLICIES_DIR = Path(__file__).parent.parent / "policies"

# ── Scope tables (derived from policies/*.yaml) ──────────────────────
# Hardcoded for speed — these rarely change and the policy YAMLs are the
# canonical reference. If a new agent is added, update here + policies/.

_READ_SCOPES: dict[str, set[str]] = {
    "executive-agent": {"startup", "finance", "health", "family", "personal", "meta"},
    "finance-agent": {"finance"},
    "startup-agent": {"startup"},
    "health-agent": {"health"},
    "personal-agent": {"personal"},
    "community-agent": {"community"},
    "email-agent": {"personal"},
}

_WRITE_SCOPES: dict[str, set[str]] = {
    "executive-agent": {"meta"},
    "finance-agent": {"finance"},
    "startup-agent": {"startup"},
    "health-agent": {"health"},
    "personal-agent": {"personal"},
    "community-agent": {"community"},
    "email-agent": {"personal"},
}


def can_read(agent_id: str, vault: str) -> bool:
    """Check if agent has read access to a vault's context."""
    allowed = _READ_SCOPES.get(agent_id)
    if allowed is None:
        log.warning("Unknown agent_id=%s denied read on vault=%s", agent_id, vault)
        return False
    return vault in allowed


def can_write(agent_id: str, vault: str) -> bool:
    """Check if agent has write access to a vault's context (ingest messages)."""
    allowed = _WRITE_SCOPES.get(agent_id)
    if allowed is None:
        log.warning("Unknown agent_id=%s denied write on vault=%s", agent_id, vault)
        return False
    return vault in allowed


def load_scopes_from_policies() -> dict[str, dict[str, set[str]]]:
    """Parse policy YAMLs and return read/write scope sets per agent.

    Useful for verifying hardcoded tables match policy files.
    Returns: {"read": {agent_id: {vaults}}, "write": {agent_id: {vaults}}}
    """
    read: dict[str, set[str]] = {}
    write: dict[str, set[str]] = {}

    for path in sorted(POLICIES_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except Exception as e:
            log.warning("Failed to parse %s: %s", path.name, e)
            continue

        agent_id = data.get("agent_id", path.stem)
        allowed = data.get("scopes", {}).get("allowed", [])

        r_vaults: set[str] = set()
        w_vaults: set[str] = set()

        for scope in allowed:
            parts = scope.split(":")
            if len(parts) >= 3 and parts[0] == "vault":
                domain, action = parts[1], parts[2]
                if action == "read":
                    r_vaults.add(domain)
                elif action == "write":
                    w_vaults.add(domain)

        read[agent_id] = r_vaults
        write[agent_id] = w_vaults

    return {"read": read, "write": write}


def verify_tables() -> list[str]:
    """Compare hardcoded scope tables against policy YAMLs. Returns list of mismatches."""
    if not POLICIES_DIR.exists():
        return ["policies/ directory not found — cannot verify"]

    live = load_scopes_from_policies()
    errors: list[str] = []

    for agent_id, vaults in live["read"].items():
        hardcoded = _READ_SCOPES.get(agent_id, set())
        if hardcoded != vaults:
            errors.append(f"READ mismatch {agent_id}: hardcoded={hardcoded}, policy={vaults}")

    for agent_id, vaults in live["write"].items():
        hardcoded = _WRITE_SCOPES.get(agent_id, set())
        if hardcoded != vaults:
            errors.append(f"WRITE mismatch {agent_id}: hardcoded={hardcoded}, policy={vaults}")

    # Check for agents in hardcoded tables but not in policies
    for agent_id in set(_READ_SCOPES) | set(_WRITE_SCOPES):
        if agent_id not in live["read"] and agent_id not in live["write"]:
            errors.append(f"Agent {agent_id} in hardcoded tables but no policy YAML found")

    return errors
