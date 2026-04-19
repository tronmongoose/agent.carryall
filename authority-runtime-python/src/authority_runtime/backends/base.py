"""
Backend Protocol for Authority Runtime.

Defines the contract any backend must satisfy to be usable as a source of
truth for vaults, resources, and policy decisions. Both the built-in
MemoryBackend and SlosBackend satisfy this Protocol, and third parties
(e.g. a ConductorOne Baton adapter) can register their own backend by
implementing these methods and publishing an entry point under the
``authority_runtime.backends`` group.

A backend is a plain class with the seven methods below. Use
``@runtime_checkable`` so ``isinstance(obj, Backend)`` verifies the method
set at runtime.
"""

from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable


class Decision(Enum):
    """Policy evaluation result."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class PolicyResult:
    """Result of policy evaluation."""

    decision: Decision
    reason: str
    metadata: dict

    def __str__(self) -> str:
        return f"{self.decision.value.upper()}: {self.reason}"


@dataclass
class DocumentMetadata:
    """Document metadata returned by a backend's ``get_metadata``."""

    uri: str
    id: str
    domain: list[str]
    sensitivity: str
    allowed_agents: list[str]
    denied_agents: list[str]
    requires_approval: list[str]


@runtime_checkable
class Backend(Protocol):
    """
    Pluggable data-source contract for Authority Runtime.

    Implement these seven methods to plug a new identity/resource source
    into Carryall. Existing implementations (``MemoryBackend``,
    ``SlosBackend``) are the reference.
    """

    def list_vaults(self, agent_id: str = "executive-agent", mock: bool = False) -> list[str]: ...

    def list_resources(self, vault: str, agent_id: str, mock: bool = False) -> list[dict]: ...

    def get_metadata(self, uri: str, agent_id: str, mock: bool = False) -> DocumentMetadata: ...

    def check_access(
        self,
        envelope: Any,
        action: str,
        uri: str,
        mock: bool = False,
    ) -> PolicyResult: ...

    def read_document(
        self,
        document_id: str,
        purpose: str,
        agent_id: str,
        mock: bool = False,
    ) -> dict: ...

    def write_document(
        self,
        domain: str,
        content: str,
        metadata: dict,
        agent_id: str,
        document_id: Optional[str] = None,
        mock: bool = False,
    ) -> dict: ...

    def query_documents(
        self,
        domain: str,
        query: str,
        agent_id: str,
        include_content: bool = False,
        limit: int = 10,
        mock: bool = False,
    ) -> dict: ...


_BUILTIN_BACKENDS = {
    "memory": "authority_runtime.backends.memory:MemoryBackend",
    "slos": "authority_runtime.backends.slos:SlosBackend",
}


def _resolve_backend_class(name: str) -> type:
    """
    Resolve a backend class by name.

    Resolution order:
      1. Built-in names ("memory", "slos").
      2. Entry points registered under the ``authority_runtime.backends`` group
         (how third parties publish adapters, e.g. ``baton = carryall_baton.backend:BatonBackend``).
      3. Dotted path of the form ``module.path:ClassName``.
    """
    if name in _BUILTIN_BACKENDS:
        return _resolve_backend_class(_BUILTIN_BACKENDS[name])

    try:
        from importlib.metadata import entry_points

        eps = entry_points(group="authority_runtime.backends")
        for ep in eps:
            if ep.name == name:
                return ep.load()
    except Exception:
        pass

    if ":" in name:
        module_path, class_name = name.split(":", 1)
        module = importlib.import_module(module_path)
        return getattr(module, class_name)

    raise ValueError(
        f"Unknown backend '{name}'. Expected one of {list(_BUILTIN_BACKENDS)}, "
        "a registered entry point, or a 'module.path:ClassName' dotted path."
    )


def load_backend(config_path: Optional[str] = None) -> Backend:
    """
    Instantiate a backend from a JSON config file.

    The config path is resolved in this order:
      1. Explicit ``config_path`` argument.
      2. ``CARRYALL_SLOS_CONFIG`` environment variable.
      3. Fall back to an in-memory backend with empty initial data.

    Config schema::

        {
          "backend": "memory" | "slos" | "baton" | "pkg.mod:Class",
          "init": { ...kwargs passed to the backend constructor... }
        }

    For backward compatibility, if the config file exists but does not set
    ``backend``, we treat it as a ``SlosBackend`` config (the prior behavior
    of ``CARRYALL_SLOS_CONFIG``).
    """
    path = config_path or os.environ.get("CARRYALL_SLOS_CONFIG")

    if not path:
        from .memory import MemoryBackend

        return MemoryBackend()

    config_file = Path(path).expanduser()
    if not config_file.exists():
        raise FileNotFoundError(f"Backend config not found: {config_file}")

    with open(config_file) as f:
        config = json.load(f)

    backend_name = config.get("backend")
    if backend_name is None:
        from .slos import SlosBackend

        return SlosBackend(config_path=str(config_file))

    backend_cls = _resolve_backend_class(backend_name)
    init_kwargs = config.get("init", {})
    return backend_cls(**init_kwargs)


__all__ = [
    "Backend",
    "Decision",
    "PolicyResult",
    "DocumentMetadata",
    "load_backend",
]
