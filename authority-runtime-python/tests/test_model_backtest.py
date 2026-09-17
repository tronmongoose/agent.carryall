"""Tests for authority_runtime.models.backtest: replay past model decisions against a policy."""

import json

import pytest
from typer.testing import CliRunner

from authority_runtime.models import ModelPolicy, ModelPolicyError, resolve_audited
from authority_runtime.models.backtest import (
    ReplayRecord,
    records_from_audit,
    records_from_usage_jsonl,
    replay,
)
from authority_runtime.storage import EnvelopeStore


def test_replay_counts_and_reasons():
    records = [
        ReplayRecord("ollama", "gemma4:26b", recorded="allow", ref="1"),
        ReplayRecord("ollama", "qwen2.5:72b", recorded="allow", ref="2"),
        ReplayRecord("anthropic", "gpt-4o", recorded=None, ref="3"),
        ReplayRecord("anthropic", "claude-opus-5", recorded="deny", ref="4"),
    ]
    report = replay(records, ModelPolicy.builtin())
    assert (report.total, report.allowed, report.denied) == (4, 2, 2)
    assert report.by_reason == {"banned_vendor": 1, "not_allowlisted": 1}
    assert {f["ref"]: f["now"] for f in report.flips} == {"2": "deny", "4": "allow"}
    assert report.to_dict()["policy_version"] == ModelPolicy.builtin().version


def test_replay_is_pure_and_deterministic():
    records = [ReplayRecord("ollama", "gemma4:26b", recorded=None, ref=str(i)) for i in range(3)]
    p = ModelPolicy.builtin()
    assert replay(records, p).to_dict() == replay(records, p).to_dict()


def test_records_from_clawrouter_usage_log():
    lines = [
        json.dumps({"route": "local", "model": "gemma4:26b", "timestamp": "t1"}),
        json.dumps({"route": "frontier", "model": "claude-sonnet-4-20250514"}),
        json.dumps({"route": "refused", "model": "none"}),
        json.dumps({"tier": "local", "model": "qwen2.5:72b"}),
        "not json",
    ]
    records, skipped = records_from_usage_jsonl(
        lines, {"local": "ollama", "frontier": "anthropic"})
    assert [(r.provider, r.model_id) for r in records] == [
        ("ollama", "gemma4:26b"), ("anthropic", "claude-sonnet-4-20250514"),
        ("ollama", "qwen2.5:72b")]
    assert skipped == 2


def test_records_from_audit_trail_round_trip(tmp_path):
    store = EnvelopeStore(str(tmp_path / "a.db"))
    p = ModelPolicy.builtin()
    resolve_audited(p, "ollama", configured=None, default="gemma4:26b", purpose="x", store=store)
    with pytest.raises(ModelPolicyError):
        resolve_audited(p, "openai", configured=None, default="gpt-4o", purpose="x", store=store)
    records = list(records_from_audit(store.get_audit_trail(limit=100000)))
    assert sorted((r.provider, r.model_id, r.recorded) for r in records) == [
        ("ollama", "gemma4:26b", "allow"), ("openai", "gpt-4o", "deny")]
    assert replay(records, p).flips == []


def test_cli_backtest_and_check(tmp_path):
    from authority_runtime.cli import app

    log = tmp_path / "usage.jsonl"
    log.write_text("\n".join(json.dumps(x) for x in [
        {"route": "local", "model": "gemma4:26b"},
        {"route": "local", "model": "qwen2.5:72b"},
    ]))
    runner = CliRunner()
    result = runner.invoke(app, ["models", "backtest", "--usage", str(log), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["denied"] == 1

    result = runner.invoke(app, ["models", "backtest", "--usage", str(log), "--fail-on-deny"])
    assert result.exit_code == 1

    assert runner.invoke(app, ["models", "check", "anthropic", "claude-opus-5"]).exit_code == 0
    assert runner.invoke(app, ["models", "check", "openai", "gpt-4o"]).exit_code == 1
