"""
router — sensitivity-aware tiered model routing primitive.

Composes a deployment-supplied SensitivityClassifier with a ModelRegistry
to make local-vs-frontier (or any tiered) routing decisions. A pluggable
UsageLogger records each decision for cost / latency / sensitivity audit.

Boundary: Carryall ships the abstractions (Router, SensitivityClassifier,
ModelRegistry, UsageLogger), tier mechanics, and origin-allowlist helpers.
Deployments supply:
  - the actual classifier implementation (regex, LLM, hybrid)
  - the model lineup with origin annotations
  - the sensitivity-level → tier mapping
  - the usage log destination

Privacy posture: the JsonlUsageLogger never writes the query body, only
its length. Bodies stay in the deployment's hands.
"""

from .classifier import (
    Sensitivity,
    SensitivityClassifier,
    NeverSensitiveClassifier,
)
from .registry import ModelRegistry, Tier
from .router import Router, RouteDecision, RouteError
from .usage_logger import (
    UsageLogger,
    NullUsageLogger,
    JsonlUsageLogger,
)

__all__ = [
    "Sensitivity",
    "SensitivityClassifier",
    "NeverSensitiveClassifier",
    "ModelRegistry",
    "Tier",
    "Router",
    "RouteDecision",
    "RouteError",
    "UsageLogger",
    "NullUsageLogger",
    "JsonlUsageLogger",
]
