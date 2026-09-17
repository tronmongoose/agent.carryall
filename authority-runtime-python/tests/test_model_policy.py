"""Tests for authority_runtime.models: exact-ID allowlist, resolution chain, audit."""

import json

import pytest

from authority_runtime.models import (
    ALLOWLIST_VERSION,
    ModelPolicy,
    ModelPolicyError,
    banned_slug,
    resolve_audited,
)
from authority_runtime.storage import EnvelopeStore


@pytest.fixture
def policy() -> ModelPolicy:
    return ModelPolicy.builtin()


@pytest.fixture
def store(tmp_path) -> EnvelopeStore:
    return EnvelopeStore(str(tmp_path / "audit.db"))


def model_rows(store):
    rows = [r for r in store.get_audit_trail() if r["action"].startswith("model:")]
    return sorted(rows, key=lambda r: r["id"])


def test_allowlist_resolves_exact_ids(policy):
    r = policy.resolve("anthropic", "claude-opus-5")
    assert (r.provider, r.model_id, r.origin, r.locality) == (
        "anthropic", "claude-opus-5", "Anthropic", "remote")
    assert r.policy_version == ALLOWLIST_VERSION
    assert policy.resolve("ollama", "gemma4:26b").origin == "Google"


@pytest.mark.parametrize("provider,model", [
    ("anthropic", "claude-opus-5-20990101"),
    ("anthropic", "CLAUDE-OPUS-5"),
    ("anthropic", " claude-opus-5"),
    ("anthropic", "claude-3-haiku-20240307"),
    ("anthropic", "claude-sonnet-4"),
    ("ollama", "gemma4:27b"),
    ("ollama", "claude-opus-5"),
    ("anthropic", "gemma4:26b"),
    ("anthropic", ""),
])
def test_allowlist_rejects_unknown_model(policy, provider, model):
    with pytest.raises(ModelPolicyError) as exc:
        policy.resolve(provider, model)
    assert exc.value.reason in {"not_allowlisted", "invalid_model_id"}


@pytest.mark.parametrize("provider,model", [
    ("openai", "gpt-4o"),
    ("openai", "gpt-4o-mini"),
    ("azure-openai", "gpt-4o"),
    ("anthropic", "gpt-4o"),
    ("ollama", "gpt-oss:20b"),
    ("OpenAI", "gpt-4o"),
])
def test_allowlist_rejects_openai(policy, provider, model):
    with pytest.raises(ModelPolicyError) as exc:
        policy.resolve(provider, model)
    assert exc.value.reason in {"forbidden_provider", "unknown_provider", "not_allowlisted"}


@pytest.mark.parametrize("model", [
    "qwen2.5:72b", "qwen3-coder", "deepseek-r1:70b", "yi:34b", "glm4:9b", "chatglm3",
    "internlm2", "minimax-m1", "kimi-k2", "moonshot-v1", "hunyuan-large", "doubao-pro",
    "ernie-4", "baichuan2", "hf.co/Qwen/Qwen2.5-7B", "library/DeepSeek-V3",
])
def test_allowlist_rejects_banned_vendor_prefixes(policy, model):
    assert banned_slug(model) is not None
    with pytest.raises(ModelPolicyError) as exc:
        policy.resolve("ollama", model)
    assert exc.value.reason == "banned_vendor"


@pytest.mark.parametrize("model", [
    "gemma4:26b", "llama3.3:70b", "phi4", "hermes3", "mistral-small3.2", "claude-opus-5",
    "granite3.3", "mixtral:8x7b",
])
def test_banned_layer_has_no_false_positive_on_allowed_families(model):
    assert banned_slug(model) is None


def test_banned_layer_runs_even_if_allowlisted(tmp_path, monkeypatch):
    additions = tmp_path / "models.json"
    additions.write_text(json.dumps({"models": [
        {"provider": "ollama", "model_id": "qwen2.5:7b", "origin": "Mistral"}]}))
    with pytest.raises(ModelPolicyError) as exc:
        ModelPolicy.load(additions)
    assert exc.value.reason == "banned_vendor"


def test_unknown_provider_raises(policy):
    for provider in ("gemini", "bedrock", "", "Anthropic"):
        with pytest.raises(ModelPolicyError):
            policy.resolve(provider, "claude-opus-5")


def test_resolve_choice_precedence_never_skips_invalid(policy):
    r = policy.resolve_choice("ollama", requested=None, configured=None, default="gemma4:26b")
    assert r.source == "default"
    r = policy.resolve_choice("anthropic", requested="claude-haiku-4-5",
                              configured="claude-opus-5", default="claude-sonnet-5")
    assert (r.model_id, r.source) == ("claude-haiku-4-5", "requested")
    with pytest.raises(ModelPolicyError):
        policy.resolve_choice("ollama", requested=None, configured="qwen2.5:72b",
                              default="gemma4:26b")
    with pytest.raises(ModelPolicyError):
        policy.resolve_choice("ollama", requested=None, configured="", default="gemma4:26b")


def test_local_additions_extend_ollama_only(tmp_path):
    additions = tmp_path / "models.json"
    additions.write_text(json.dumps({"models": [
        {"provider": "ollama", "model_id": "mistral-small3.2:24b", "origin": "Mistral"}]}))
    p = ModelPolicy.load(additions)
    assert p.resolve("ollama", "mistral-small3.2:24b").origin == "Mistral"
    assert p.version.startswith(ALLOWLIST_VERSION + "+local:")

    for bad, reason in [
        ({"provider": "anthropic", "model_id": "claude-x", "origin": "Anthropic"},
         "additions_remote_provider"),
        ({"provider": "ollama", "model_id": "falcon:7b", "origin": "TII"}, "origin_not_allowed"),
        ({"provider": "ollama", "model_id": "m"}, "additions_malformed"),
    ]:
        additions.write_text(json.dumps({"models": [bad]}))
        with pytest.raises(ModelPolicyError) as exc:
            ModelPolicy.load(additions)
        assert exc.value.reason == reason


def test_missing_additions_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("CARRYALL_MODEL_ALLOWLIST", str(tmp_path / "absent.json"))
    with pytest.raises(ModelPolicyError) as exc:
        ModelPolicy.load()
    assert exc.value.reason == "additions_unreadable"
    monkeypatch.delenv("CARRYALL_MODEL_ALLOWLIST")
    assert ModelPolicy.load().version == ALLOWLIST_VERSION


def test_resolution_is_audited(policy, store):
    resolve_audited(policy, "ollama", configured="gemma4:26b", default="gemma4:26b",
                    purpose="unit", store=store)
    with pytest.raises(ModelPolicyError):
        resolve_audited(policy, "ollama", configured="qwen2.5:72b", default="gemma4:26b",
                        purpose="unit", store=store)
    allow, deny = model_rows(store)
    assert allow["result"] == "allow" and allow["resource"] == "model://ollama/gemma4:26b"
    assert allow["metadata"]["policy_version"] == ALLOWLIST_VERSION
    assert allow["metadata"]["source"] == "configured"
    assert deny["result"] == "deny" and deny["metadata"]["reason"] == "banned_vendor"
    assert store.verify_audit_chain()["valid"]


def test_audit_failure_blocks_resolution(policy, store, monkeypatch):
    def broken(entry):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "save_audit_entry", broken)
    with pytest.raises(ModelPolicyError) as exc:
        resolve_audited(policy, "ollama", configured=None, default="gemma4:26b",
                        purpose="unit", store=store)
    assert exc.value.reason == "audit_unavailable"
