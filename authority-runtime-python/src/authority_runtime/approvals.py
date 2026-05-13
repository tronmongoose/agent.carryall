"""
Carryall Approval Queue — human-in-the-loop authorization for cross-domain access.

Manages approval requests as YAML files in the SLOS meta vault approvals directory,
matching the existing format at ~/slos/vaults/meta/approvals/.

When an agent's access requires approval (per OPA policy or document-level metadata),
the request is queued here. The agent receives a "requires_approval" response with a
request ID. The request can be approved/denied via Telegram or the Authority Dashboard.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Union

import yaml

logger = logging.getLogger(__name__)


class ApprovalQueue:
    """
    File-backed approval queue using YAML records.

    Each approval request is stored as a YAML file named {request_id}.yaml
    in the approvals directory (typically ~/slos/vaults/meta/approvals/).
    """

    def __init__(self, approvals_dir: Union[str, Path]):
        self.approvals_dir = Path(approvals_dir).expanduser()
        self.approvals_dir.mkdir(parents=True, exist_ok=True)

    def _request_path(self, request_id: str) -> Path:
        return self.approvals_dir / f"{request_id}.yaml"

    def create_request(
        self,
        agent_id: str,
        action: str,
        resource_uri: str,
        purpose: str,
        target_domain: Optional[str] = None,
        fields_needed: Optional[list[str]] = None,
        ttl_seconds: int = 86400,
        auto_deny_seconds: Optional[int] = None,
    ) -> str:
        """
        Create a pending approval request.

        Args:
            agent_id: Agent requesting access
            action: Action type (read, write, etc.)
            resource_uri: SLOS URI of the resource
            purpose: Why the agent needs access (audit trail)
            target_domain: Target vault domain
            fields_needed: Specific fields requested (optional)
            ttl_seconds: Time to live before auto-expiry (default 24h)
            auto_deny_seconds: If set, transitions to "denied" after this many
                seconds with no human decision. Must be < ttl_seconds.
                Default None preserves legacy behaviour (TTL -> "expired").

        Returns:
            Request ID (UUIDv7-style)
        """
        now = datetime.now(timezone.utc)
        request_id = str(uuid.uuid4())

        # Extract domain from URI if not provided
        if not target_domain and resource_uri.startswith("slos://vaults/"):
            parts = resource_uri.replace("slos://vaults/", "").split("/", 1)
            target_domain = parts[0] if parts else "unknown"

        record = {
            "id": request_id,
            "agent_id": agent_id,
            "action": action,
            "resource_uri": resource_uri,
            "target_domain": target_domain or "unknown",
            "purpose": purpose,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "status": "pending",
        }

        if auto_deny_seconds is not None and auto_deny_seconds < ttl_seconds:
            record["auto_deny_at"] = (now + timedelta(seconds=auto_deny_seconds)).isoformat()

        if fields_needed:
            record["fields_needed"] = fields_needed

        path = self._request_path(request_id)
        with open(path, "w") as f:
            yaml.dump(record, f, default_flow_style=False, sort_keys=False)

        logger.info(
            f"Approval request {request_id} created: {agent_id} wants to {action} "
            f"{resource_uri} — {purpose}"
        )
        return request_id

    def decide(
        self,
        request_id: str,
        decision: str,
        decided_by: str,
        reason: str = "",
    ) -> bool:
        """
        Approve or deny a pending request.

        Args:
            request_id: The approval request ID
            decision: "approved" or "denied"
            decided_by: Who made the decision (e.g., "erik", "telegram")
            reason: Optional reason for the decision

        Returns:
            True if decision was recorded, False if request not found or already decided
        """
        path = self._request_path(request_id)
        if not path.exists():
            logger.warning(f"Approval request {request_id} not found")
            return False

        with open(path) as f:
            record = yaml.safe_load(f)

        if record.get("status") != "pending":
            logger.warning(
                f"Approval request {request_id} already decided: {record.get('status')}"
            )
            return False

        # Check expiry
        expires_at = datetime.fromisoformat(record["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            record["status"] = "expired"
            with open(path, "w") as f:
                yaml.dump(record, f, default_flow_style=False, sort_keys=False)
            logger.warning(f"Approval request {request_id} has expired")
            return False

        now = datetime.now(timezone.utc)
        record["status"] = decision
        record["decided_at"] = now.isoformat()
        record["decided_by"] = decided_by
        record["decision_reason"] = reason

        with open(path, "w") as f:
            yaml.dump(record, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Approval request {request_id} {decision} by {decided_by}: {reason}")
        return True

    def _maybe_auto_transition(self, record: dict, path: Path) -> dict:
        """Auto-transition a pending record to "denied" or "expired" if due.

        "denied" takes precedence over "expired" — auto_deny_at is always
        earlier than expires_at when both are set, and a timeout-deny is a
        stronger statement than a TTL expiry.
        """
        if record.get("status") != "pending":
            return record
        now = datetime.now(timezone.utc)
        auto_deny_at = record.get("auto_deny_at")
        if auto_deny_at and now > datetime.fromisoformat(auto_deny_at):
            record["status"] = "denied"
            record["decided_at"] = now.isoformat()
            record["decided_by"] = "auto-deny"
            record["decision_reason"] = "auto-denied: no human decision before auto_deny_at"
            with open(path, "w") as f:
                yaml.dump(record, f, default_flow_style=False, sort_keys=False)
            return record
        expires_at = datetime.fromisoformat(record["expires_at"])
        if now > expires_at:
            record["status"] = "expired"
            with open(path, "w") as f:
                yaml.dump(record, f, default_flow_style=False, sort_keys=False)
        return record

    def check(self, request_id: str) -> Optional[dict]:
        """
        Check the status of an approval request.

        Returns:
            Record dict if found, None otherwise. Auto-transitions stale
            requests to "denied" (if auto_deny_at passed) or "expired".
        """
        path = self._request_path(request_id)
        if not path.exists():
            return None

        with open(path) as f:
            record = yaml.safe_load(f)

        return self._maybe_auto_transition(record, path)

    def is_approved(self, request_id: str) -> bool:
        """Check if a specific request has been approved."""
        record = self.check(request_id)
        return record is not None and record.get("status") == "approved"

    def is_denied(self, request_id: str) -> bool:
        """Check if a specific request has been denied (by human or auto-deny)."""
        record = self.check(request_id)
        return record is not None and record.get("status") == "denied"

    def find_approved(
        self,
        agent_id: str,
        action: str,
        resource_uri: str,
    ) -> Optional[dict]:
        """
        Find an existing approved request that covers this access.

        Checks for a non-expired approved request matching the agent, action, and resource.
        This allows agents to retry after approval without needing the original request ID.

        Returns:
            Approved record dict if found, None otherwise.
        """
        now = datetime.now(timezone.utc)
        for path in self.approvals_dir.glob("*.yaml"):
            try:
                with open(path) as f:
                    record = yaml.safe_load(f)
                if (
                    record.get("status") == "approved"
                    and record.get("agent_id") == agent_id
                    and record.get("action") == action
                    and record.get("resource_uri") == resource_uri
                ):
                    expires_at = datetime.fromisoformat(record["expires_at"])
                    if now <= expires_at:
                        return record
            except Exception:
                continue
        return None

    def list_pending(self) -> list[dict]:
        """List all pending (non-expired, non-auto-denied) approval requests."""
        pending = []
        for path in sorted(self.approvals_dir.glob("*.yaml")):
            try:
                with open(path) as f:
                    record = yaml.safe_load(f)
                record = self._maybe_auto_transition(record, path)
                if record.get("status") == "pending":
                    pending.append(record)
            except Exception:
                continue
        return pending

    def list_history(self, limit: int = 50) -> list[dict]:
        """List recent approval decisions (approved, denied, expired)."""
        history = []
        for path in sorted(self.approvals_dir.glob("*.yaml"), reverse=True):
            if len(history) >= limit:
                break
            try:
                with open(path) as f:
                    record = yaml.safe_load(f)
                if record.get("status") != "pending":
                    history.append(record)
            except Exception:
                continue
        return history

    def expire_stale(self) -> dict[str, int]:
        """Run auto-transitions across all pending requests.

        Returns a count of newly-transitioned records keyed by terminal
        status: ``{"denied": N, "expired": M}``.
        """
        counts = {"denied": 0, "expired": 0}
        for path in self.approvals_dir.glob("*.yaml"):
            try:
                with open(path) as f:
                    record = yaml.safe_load(f)
                if record.get("status") != "pending":
                    continue
                transitioned = self._maybe_auto_transition(record, path)
                new_status = transitioned.get("status")
                if new_status in counts:
                    counts[new_status] += 1
            except Exception:
                continue
        total = counts["denied"] + counts["expired"]
        if total:
            logger.info(
                f"Auto-transitioned {total} stale approval requests "
                f"(denied={counts['denied']}, expired={counts['expired']})"
            )
        return counts
