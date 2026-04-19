"""
Backend adapters for Authority Runtime.

Backends supply the source-of-truth for vaults, resources, and access
decisions. Built-in adapters: ``MemoryBackend`` (in-process) and
``SlosBackend`` (Sovereign Life OS, via MCP). Third-party adapters
implement the ``Backend`` Protocol and register themselves under the
``authority_runtime.backends`` entry-point group.
"""

from .base import Backend, Decision, DocumentMetadata, PolicyResult, load_backend
from .memory import MemoryBackend
from .slos import SlosBackend

__all__ = [
    "Backend",
    "MemoryBackend",
    "SlosBackend",
    "Decision",
    "PolicyResult",
    "DocumentMetadata",
    "load_backend",
]
