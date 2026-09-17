"""Tests that model policy is enforced at every dispatch point: clawrouter, MCP, compilers, router."""

from pathlib import Path
from typing import List

import pytest

from authority_runtime import egress
from authority_runtime.enforce import PermissionDenied
from authority_runtime.models import ModelPolicyError
from authority_runtime.storage import EnvelopeStore

from .clawrouter_loader import REPO, load_clawrouter


def model_rows(db: Path):
    rows = EnvelopeStore(str(db)).get_audit_trail(limit=100000)
    return sorted((r for r in rows if r["action"].startswith("model:")), key=lambda r: r["id"])


def _never(label: str):
    def fail(*a, **k):
        raise AssertionError(f"{label} must not be reached")

    return fail


@pytest.fixture
def router_env(monkeypatch, tmp_path):
    db = tmp_path / "audit.db"
    monkeypatch.setenv("CARRYALL_DB", str(db))
    monkeypatch.delenv("CARRYALL_MODEL_ALLOWLIST", raising=False)
    for var in ("CLAWROUTER_LOCAL_MODEL", "CLAWROUTER_FRONTIER_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return db


def test_clawrouter_rejects_banned_local_model(monkeypatch, tmp_path, router_env):
    monkeypatch.setenv("CLAWROUTER_LOCAL_MODEL", "qwen2.5:72b")
    monkeypatch.setattr(egress, "request", _never("Ollama egress"))
    calls: List[str] = []
    router = load_clawrouter(monkeypatch, tmp_path, calls)

    with pytest.raises(ModelPolicyError) as exc:
        router.call_local("what's my checking balance?")
    assert exc.value.reason == "banned_vendor"
    assert calls == [], "finance data was fetched for a refused model"
    rows = model_rows(router_env)
    assert [(r["result"], r["metadata"]["model_id"]) for r in rows] == [("deny", "qwen2.5:72b")]


def test_clawrouter_rejects_openai_frontier_model(monkeypatch, tmp_path, router_env):
    monkeypatch.setenv("CLAWROUTER_FRONTIER_MODEL", "gpt-4o")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    router = load_clawrouter(monkeypatch, tmp_path)
    monkeypatch.setattr(router, "urlopen", _never("frontier HTTP"))

    with pytest.raises(ModelPolicyError):
        router.call_frontier("why should I invest in bonds vs equities?")
    assert [r["result"] for r in model_rows(router_env)] == ["deny"]


def test_frontier_escalation_is_audited(monkeypatch, tmp_path, router_env):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    router = load_clawrouter(monkeypatch, tmp_path)
    monkeypatch.setattr(router, "ollama_available", lambda: False)
    frontier_calls: List[str] = []

    def fake_frontier(query):
        frontier_calls.append(query)
        return {"answer": "Paris", "route": "frontier", "model": "x", "tokens": 0,
                "cost_usd": 0.0, "latency_ms": 0}

    monkeypatch.setattr(router, "call_frontier", fake_frontier)
    result = router.route_query("what is the capital of France?")
    assert result["route"] == "frontier" and frontier_calls
    rows = [r for r in model_rows(router_env) if r["action"] == "model:clawrouter_escalation"]
    assert len(rows) == 1 and rows[0]["result"] == "allow"
    assert rows[0]["metadata"]["model_id"] == "claude-sonnet-4-20250514"
    assert any("claude-sonnet-4-20250514" in r for r in result["classification"]["reasons"])


def test_frontier_escalation_refuses_disallowed_model(monkeypatch, tmp_path, router_env):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CLAWROUTER_FRONTIER_MODEL", "gpt-4o")
    router = load_clawrouter(monkeypatch, tmp_path)
    monkeypatch.setattr(router, "ollama_available", lambda: False)
    monkeypatch.setattr(router, "call_frontier", _never("frontier call"))

    result = router.route_query("what is the capital of France?")
    assert result["route"] == "refused"
    rows = [r for r in model_rows(router_env) if r["action"] == "model:clawrouter_escalation"]
    assert [r["result"] for r in rows] == ["deny"]


@pytest.fixture
def mcp_server(tmp_path, monkeypatch):
    from authority_runtime.backends.memory import MemoryBackend
    from authority_runtime.keys import AgentKeyStore
    from authority_runtime.mcp_server import CarryallMCPServer

    monkeypatch.delenv("CARRYALL_MODEL_ALLOWLIST", raising=False)
    return CarryallMCPServer(
        key_store=AgentKeyStore(str(tmp_path / "keys")),
        envelope_store=EnvelopeStore(str(tmp_path / "authority.db")),
        backend=MemoryBackend(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "gemini", "OLLAMA", "", None, 7])
async def test_provider_dispatch_has_no_catchall(mcp_server, monkeypatch, provider):
    from authority_runtime import compiler, mcp_server as mod

    assert not hasattr(mod, "OpenAICompiler")
    assert "OPENAI" not in (REPO / "authority-runtime-python/src/authority_runtime/mcp_server.py"
                            ).read_text().upper()
    monkeypatch.setattr(compiler, "OpenAI", _never("OpenAI client"))
    monkeypatch.setattr(compiler, "Anthropic", _never("Anthropic client"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    with pytest.raises(PermissionDenied) as exc:
        await mcp_server._tool_compile_policy({
            "agent_id": "a", "intent": "read notes", "available_scopes": ["vault:notes:read"],
            "available_resources": [], "llm_provider": provider,
        })
    assert exc.value.reason_class == "MODEL_POLICY"
    rows = [r for r in mcp_server.envelope_store.get_audit_trail()
            if r["action"] == "model:compile_policy"]
    assert [r["result"] for r in rows] == ["deny"]


@pytest.mark.asyncio
async def test_compile_policy_rejects_banned_ollama_model(mcp_server, monkeypatch):
    monkeypatch.setenv("OLLAMA_COMPILER_MODEL", "deepseek-r1:70b")
    monkeypatch.setattr(egress, "request", _never("Ollama egress"))
    with pytest.raises(PermissionDenied) as exc:
        await mcp_server._tool_compile_policy({
            "agent_id": "a", "intent": "read notes", "available_scopes": ["vault:notes:read"],
            "available_resources": [], "llm_provider": "ollama",
        })
    assert exc.value.reason_class == "MODEL_POLICY"


def test_compilers_enforce_allowlist_at_construction(monkeypatch):
    from authority_runtime import compiler

    monkeypatch.setattr(compiler, "OpenAI", _never("OpenAI client"))
    with pytest.raises(ModelPolicyError):
        compiler.OpenAICompiler(api_key="sk-test")
    with pytest.raises(ModelPolicyError):
        compiler.AnthropicCompiler(model="claude-3-haiku-20240307", api_key="k")
    with pytest.raises(ModelPolicyError):
        compiler.OllamaCompiler(model="qwen2.5:72b")
    assert compiler.OllamaCompiler().default_model == "gemma4:26b"


def test_router_enforces_allowlist_via_registry():
    from authority_runtime.router import ModelRegistry, NeverSensitiveClassifier, Router, RouteError

    def build(model: str, origin: str, provider="ollama") -> ModelRegistry:
        reg = ModelRegistry()
        reg.add_tier("t", model=model, origin=origin, provider=provider)
        reg.map_sensitivity("public", "t")
        return reg

    Router(classifier=NeverSensitiveClassifier(), registry=build("gemma4:26b", "Google"))
    for reg in (build("qwen2.5:72b", "Alibaba"), build("gemma4:26b", "Meta"),
                build("claude-opus-5", "Anthropic", provider="")):
        with pytest.raises((ModelPolicyError, ValueError)):
            Router(classifier=NeverSensitiveClassifier(), registry=reg)

    reg = build("gemma4:26b", "Google")
    router = Router(classifier=NeverSensitiveClassifier(), registry=reg)
    reg.add_tier("late", model="deepseek-r1", origin="Google", provider="ollama")
    with pytest.raises(RouteError):
        router.route("q", force_tier="late")
