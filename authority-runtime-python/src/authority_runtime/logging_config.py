"""
Structured JSON logging for Carryall.

Configurable via environment variables:
- CARRYALL_LOG_LEVEL: DEBUG, INFO, WARNING, ERROR (default: INFO)
- CARRYALL_LOG_FORMAT: json or text (default: json)
"""

import json
import logging
import os
import uuid
from contextvars import ContextVar

# Per-request correlation ID
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# Extra fields that can be attached to log records
_EXTRA_FIELDS = ("agent_id", "envelope_id", "action", "resource", "duration_ms", "method")


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON for machine parsing."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = request_id_var.get("")
        if rid:
            log_entry["request_id"] = rid
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        for key in _EXTRA_FIELDS:
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry)


def configure_logging():
    """Configure structured logging for the authority_runtime package."""
    level_name = os.environ.get("CARRYALL_LOG_LEVEL", "INFO").upper()
    fmt = os.environ.get("CARRYALL_LOG_FORMAT", "json")

    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        ))

    root = logging.getLogger("authority_runtime")
    root.setLevel(level)
    # Clear existing handlers to avoid duplicates on re-configure
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False


def new_request_id() -> str:
    """Generate and set a new request correlation ID."""
    rid = uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    return rid
