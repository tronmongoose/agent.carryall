"""Tests that every known outbound sink routes through authority_runtime.egress."""

import ast
from pathlib import Path
from typing import Any, Dict, List

import pytest

from authority_runtime import egress
from authority_runtime.storage import EnvelopeStore

from .clawrouter_loader import REPO, load_clawrouter

PKG = REPO / "authority-runtime-python" / "src" / "authority_runtime"

SINKS = [
    (REPO / "mayor" / "clawrouter.py", "ollama_available"),
    (REPO / "mayor" / "clawrouter.py", "call_local"),
    (REPO / "lib" / "common.py", "call_ollama"),
    (REPO / "lib" / "notify.py", "send_ntfy"),
    (REPO / "context" / "context_compactor.py", "_call_ollama"),
    (REPO / "context" / "context_embeddings.py", "_get_embeddings_batch"),
    (PKG / "compiler.py", "OllamaCompiler._call_ollama_chat"),
    (PKG / "mcp_server.py", "CarryallMCPServer._notify_ntfy"),
]


def _find_function(tree: ast.Module, qualname: str) -> ast.AST:
    scope: List[ast.stmt] = tree.body
    node: Any = None
    for part in qualname.split("."):
        node = next(n for n in scope if getattr(n, "name", None) == part)
        scope = node.body
    return node


def _egress_names(tree: ast.Module) -> set:
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("egress"):
            names.update(a.asname or a.name for a in n.names)
        if isinstance(n, ast.ImportFrom) and any(a.name == "egress" for a in n.names):
            names.add("egress")
    return names


@pytest.mark.parametrize("path,qualname", SINKS, ids=[f"{p.name}:{q}" for p, q in SINKS])
def test_egress_sinks_are_wired(path, qualname):
    tree = ast.parse(path.read_text())
    assert _egress_names(tree), f"{path.name} does not import authority_runtime.egress"
    fn = _find_function(tree, qualname)
    called = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            called.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                assert f.value.id not in {"requests", "aiohttp", "session"}, qualname
    assert "urlopen" not in called and "ClientSession" not in called, qualname
    assert called & {"request", "arequest"}, f"{qualname} makes no egress call"


def _deny_rows(db: Path) -> List[Dict[str, Any]]:
    rows = EnvelopeStore(str(db)).get_audit_trail()
    return sorted((r for r in rows if r["action"].startswith("egress:")), key=lambda r: r["id"])


def _forbid_connect(monkeypatch) -> None:
    def fail(*a, **k):
        raise AssertionError("denied destination was connected to")

    monkeypatch.setattr(egress, "_open_socket", fail)


def test_clawrouter_refuses_link_local_ollama(monkeypatch, tmp_path):
    db = tmp_path / "audit.db"
    monkeypatch.setenv("CARRYALL_DB", str(db))
    monkeypatch.setenv("OLLAMA_URL", "http://169.254.169.254")
    _forbid_connect(monkeypatch)
    router = load_clawrouter(monkeypatch, tmp_path)

    with pytest.raises(egress.EgressDenied):
        router.call_local("what's my checking balance?")
    with pytest.raises(egress.EgressDenied):
        router.ollama_available()

    rows = _deny_rows(db)
    assert [r["result"] for r in rows] == ["deny", "deny"]


@pytest.mark.asyncio
async def test_compiler_refuses_metadata_base_url(monkeypatch, tmp_path):
    from authority_runtime.compiler import OllamaCompiler

    db = tmp_path / "audit.db"
    monkeypatch.setenv("CARRYALL_DB", str(db))
    _forbid_connect(monkeypatch)
    compiler = OllamaCompiler(base_url="http://169.254.169.254")
    with pytest.raises(egress.EgressDenied):
        await compiler._call_ollama_chat({"model": "m", "messages": []})
    assert len(_deny_rows(db)) == 1


@pytest.mark.asyncio
async def test_approval_ntfy_channel_refuses_metadata(monkeypatch, tmp_path):
    from authority_runtime.backends.memory import MemoryBackend
    from authority_runtime.keys import AgentKeyStore
    from authority_runtime.mcp_server import CarryallMCPServer

    db = tmp_path / "authority.db"
    server = CarryallMCPServer(
        key_store=AgentKeyStore(str(tmp_path / "keys")),
        envelope_store=EnvelopeStore(str(db)),
        backend=MemoryBackend(),
    )
    monkeypatch.setenv("NTFY_URL", "http://169.254.169.254")
    _forbid_connect(monkeypatch)
    ok = await server._notify_ntfy("req-1", "agent", "read", "slos://x", "test")
    assert ok is False
    rows = _deny_rows(db)
    assert len(rows) == 1 and rows[0]["result"] == "deny"
