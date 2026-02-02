"""
Backend adapters for Authority Runtime.

Backends handle authentication and policy evaluation for different data stores.
"""

from .slos import SlosBackend, Decision, PolicyResult, DocumentMetadata

__all__ = [
    "SlosBackend",
    "Decision",
    "PolicyResult",
    "DocumentMetadata",
]
