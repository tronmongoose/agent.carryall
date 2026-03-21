"""Immutable Finding model for ARGUS security scanner."""

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FindingType(str, Enum):
    DATA_IN_WRONG_DOMAIN = "DATA_IN_WRONG_DOMAIN"
    CROSS_DOMAIN_LEAK = "CROSS_DOMAIN_LEAK"
    FORBIDDEN_PATTERN = "FORBIDDEN_PATTERN"
    UNAUTHORIZED_AGENT = "UNAUTHORIZED_AGENT"
    AUDIT_TAMPERING_ATTEMPT = "AUDIT_TAMPERING_ATTEMPT"
    AGENT_INVOKED_AUDITOR = "AGENT_INVOKED_AUDITOR"


def _redact(value: str) -> str:
    """Redact a matched value: first 6 chars + ... + last 4 chars."""
    if len(value) <= 10:
        return value[:3] + "..." + value[-2:]
    return value[:6] + "..." + value[-4:]


def _hash_value(value: str) -> str:
    """SHA256 hash of the raw matched bytes."""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


@dataclass(frozen=True)
class Finding:
    finding_id: str
    finding_type: FindingType
    severity: Severity
    timestamp_utc: float
    source_path: str
    source_domain: str
    detected_in_domain: str
    pattern_type: str
    matched_excerpt: str
    raw_hash: str
    agent_context: Optional[str]
    description: str

    @classmethod
    def create(
        cls,
        finding_type: FindingType,
        severity: Severity,
        source_path: str,
        source_domain: str,
        detected_in_domain: str,
        pattern_type: str,
        matched_value: str,
        agent_context: Optional[str] = None,
        description: str = "",
    ) -> "Finding":
        """Create a Finding with automatic redaction, hashing, and ID generation."""
        ts = time.time()
        redacted = _redact(matched_value)
        raw_hash = _hash_value(matched_value)

        # Deterministic ID for deduplication: hash of key fields
        id_content = f"{finding_type.value}:{source_path}:{pattern_type}:{raw_hash}"
        finding_id = hashlib.sha256(id_content.encode()).hexdigest()

        return cls(
            finding_id=finding_id,
            finding_type=finding_type,
            severity=severity,
            timestamp_utc=ts,
            source_path=source_path,
            source_domain=source_domain,
            detected_in_domain=detected_in_domain,
            pattern_type=pattern_type,
            matched_excerpt=redacted,
            raw_hash=raw_hash,
            agent_context=agent_context,
            description=description,
        )

    def to_log_line(self) -> str:
        """Serialize to a single JSON line for append-only audit log."""
        d = asdict(self)
        d["finding_type"] = self.finding_type.value
        d["severity"] = self.severity.value
        return json.dumps(d, separators=(",", ":"))
