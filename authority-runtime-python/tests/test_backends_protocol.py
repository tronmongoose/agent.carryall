"""Tests for the Backend Protocol and load_backend resolver."""

import json
from pathlib import Path

import pytest

from authority_runtime.backends import (
    Backend,
    MemoryBackend,
    SlosBackend,
    Decision,
    PolicyResult,
    DocumentMetadata,
    load_backend,
)
from authority_runtime.backends.base import _resolve_backend_class


def test_memory_backend_is_a_backend():
    assert isinstance(MemoryBackend(), Backend)


def test_slos_backend_is_a_backend():
    # SlosBackend can be instantiated without config; it just won't be functional.
    assert isinstance(SlosBackend(), Backend)


def test_broken_backend_rejected():
    class Broken:
        def list_vaults(self, agent_id="executive-agent", mock=False):
            return []
        # Missing the other six required methods.

    assert not isinstance(Broken(), Backend)


def test_resolve_builtin_names():
    assert _resolve_backend_class("memory") is MemoryBackend
    assert _resolve_backend_class("slos") is SlosBackend


def test_resolve_dotted_path():
    cls = _resolve_backend_class("authority_runtime.backends.memory:MemoryBackend")
    assert cls is MemoryBackend


def test_resolve_unknown_name_raises():
    with pytest.raises(ValueError, match="Unknown backend"):
        _resolve_backend_class("does-not-exist")


def test_load_backend_default_is_memory(tmp_path, monkeypatch):
    monkeypatch.delenv("CARRYALL_SLOS_CONFIG", raising=False)
    backend = load_backend()
    assert isinstance(backend, MemoryBackend)


def test_load_backend_from_explicit_config(tmp_path):
    config = tmp_path / "backend.json"
    config.write_text(json.dumps({
        "backend": "memory",
        "init": {
            "initial_data": {
                "demo": {
                    "hello": {"content": "world", "sensitivity": "internal"},
                }
            }
        },
    }))

    backend = load_backend(str(config))
    assert isinstance(backend, MemoryBackend)
    assert backend.list_vaults() == ["demo"]


def test_load_backend_legacy_slos_config(tmp_path):
    # Config without a "backend" key falls back to SlosBackend for backward compat.
    config = tmp_path / "legacy.json"
    config.write_text(json.dumps({"mcp_command": "slos-mcp"}))

    backend = load_backend(str(config))
    assert isinstance(backend, SlosBackend)


def test_load_backend_missing_config_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_backend(str(tmp_path / "nope.json"))


def test_decision_and_policy_result_are_exported_from_base():
    # Down-stream adapters import from base without touching slos.py.
    from authority_runtime.backends.base import Decision as BD, PolicyResult as BPR

    assert BD is Decision
    assert BPR is PolicyResult


def test_document_metadata_shape():
    meta = DocumentMetadata(
        uri="slos://vaults/x/y",
        id="y",
        domain=["x"],
        sensitivity="internal",
        allowed_agents=[],
        denied_agents=[],
        requires_approval=[],
    )
    assert meta.domain == ["x"]
