"""Tests that the dry-run CLI commands report their verdict in the exit code."""

import json

import pytest
from typer.testing import CliRunner

from authority_runtime.backends.slos import Decision
from authority_runtime.cli import app
from authority_runtime.constraints import UNCONSTRAINED
from authority_runtime.envelope import create_simple_envelope, generate_key_pair

RESOURCE = "slos://vaults/finance/q4"


@pytest.fixture
def credential(tmp_path):
    private_key, _ = generate_key_pair()
    envelope = create_simple_envelope(
        agent_id="agent-1", skill_name="reader", scopes=["vault:finance:read"],
        private_key=private_key, resources=["slos://vaults/finance/*"],
        constraints={UNCONSTRAINED: True},
    )
    path = tmp_path / "cred.json"
    path.write_text(json.dumps(json.loads(envelope.model_dump_json())))
    return path


class _Result:
    def __init__(self, decision):
        self.decision = decision
        self.reason = "stubbed"
        self.metadata = {}


@pytest.fixture
def decision(monkeypatch):
    holder = {"value": Decision.ALLOW}

    def check_access(self, envelope, action, resource, mock=False):
        return _Result(holder["value"])

    monkeypatch.setattr("authority_runtime.backends.slos.SlosBackend.check_access", check_access)
    return holder


@pytest.mark.parametrize("command", ["test", "explain"])
@pytest.mark.parametrize("verdict,code", [
    (Decision.ALLOW, 0),
    (Decision.DENY, 1),
    (Decision.REQUIRE_APPROVAL, 3),
])
def test_dry_run_commands_exit_with_the_verdict(credential, decision, command, verdict, code):
    decision["value"] = verdict
    result = CliRunner().invoke(app, [
        command, "-c", str(credential), "-a", "read", "-r", RESOURCE, "--mock"])
    assert result.exit_code == code, result.output
    assert verdict.value.upper() in result.output.upper()
