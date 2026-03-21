"""
Backend adapters for Authority Runtime.

Backends handle authentication and policy evaluation for different data stores.
"""

from .slos import SlosBackend, Decision, PolicyResult, DocumentMetadata
from .memory import MemoryBackend

__all__ = [
    "SlosBackend",
    "MemoryBackend",
    "Decision",
    "PolicyResult",
    "DocumentMetadata",
]
