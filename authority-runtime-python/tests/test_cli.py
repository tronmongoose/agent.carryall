"""
Tests for the Carryall CLI commands.

Uses typer.testing.CliRunner for isolated CLI testing.

Covers:
- carryall init
- carryall keys generate / list / show / delete
- carryall credentials issue / list / show / revoke
- carryall audit (query, stats, export)
- carryall policy validate / show
- carryall db status / migrate
- parse_ttl utility
"""

import json
import os
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from authority_runtime.cli import app, parse_ttl
from authority_runtime.storage import EnvelopeStore

runner = CliRunner()


# =============================================================================
# Utility: parse_ttl
# =============================================================================


class TestParseTTL:
    def test_seconds(self):
        assert parse_ttl("60s") == 60

    def test_minutes(self):
        assert parse_ttl("5m") == 300

    def test_hours(self):
        assert parse_ttl("1h") == 3600

    def test_24_hours(self):
        assert parse_ttl("24h") == 86400

    def test_days(self):
        assert parse_ttl("7d") == 604800

    def test_bare_number(self):
        assert parse_ttl("300") == 300

    def test_strips_whitespace(self):
        assert parse_ttl("  1h  ") == 3600


# =============================================================================
# carryall init
# =============================================================================


class TestInit:
    def test_init_creates_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(app, ["init", tmpdir])
            assert result.exit_code == 0

            carryall_dir = Path(tmpdir) / ".carryall"
            assert carryall_dir.exists()
            assert (carryall_dir / "keys").is_dir()
            assert (carryall_dir / "credentials").is_dir()
            assert (carryall_dir / "config.json").is_file()

            config = json.loads((carryall_dir / "config.json").read_text())
            assert config["version"] == "1.0.0"

    def test_init_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner.invoke(app, ["init", tmpdir])
            result = runner.invoke(app, ["init", tmpdir])
            assert result.exit_code == 0

    def test_init_current_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                result = runner.invoke(app, ["init"])
                assert result.exit_code == 0
                assert (Path(tmpdir) / ".carryall").exists()
            finally:
                os.chdir(old_cwd)


# =============================================================================
# carryall keys
# =============================================================================


class TestKeys:
    def test_keys_generate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_KEYS_DIR": tmpdir}
            result = runner.invoke(app, ["keys", "generate", "test-agent"], env=env)
            assert result.exit_code == 0
            assert "Generated keypair" in result.output

    def test_keys_generate_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_KEYS_DIR": tmpdir}
            runner.invoke(app, ["keys", "generate", "test-agent"], env=env)
            result = runner.invoke(app, ["keys", "generate", "test-agent"], env=env)
            assert result.exit_code == 1
            assert "already exists" in result.output

    def test_keys_generate_with_overwrite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_KEYS_DIR": tmpdir}
            runner.invoke(app, ["keys", "generate", "test-agent"], env=env)
            result = runner.invoke(app, ["keys", "generate", "test-agent", "--overwrite"], env=env)
            assert result.exit_code == 0

    def test_keys_list_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_KEYS_DIR": tmpdir}
            result = runner.invoke(app, ["keys", "list"], env=env)
            assert result.exit_code == 0
            assert "No agent keys found" in result.output

    def test_keys_list_with_agents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_KEYS_DIR": tmpdir}
            runner.invoke(app, ["keys", "generate", "agent-a"], env=env)
            runner.invoke(app, ["keys", "generate", "agent-b"], env=env)
            result = runner.invoke(app, ["keys", "list"], env=env)
            assert result.exit_code == 0
            assert "agent-a" in result.output
            assert "agent-b" in result.output

    def test_keys_show(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_KEYS_DIR": tmpdir}
            runner.invoke(app, ["keys", "generate", "test-agent"], env=env)
            result = runner.invoke(app, ["keys", "show", "test-agent"], env=env)
            assert result.exit_code == 0
            assert "Public key" in result.output

    def test_keys_show_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_KEYS_DIR": tmpdir}
            result = runner.invoke(app, ["keys", "show", "nonexistent"], env=env)
            assert result.exit_code == 1

    def test_keys_delete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_KEYS_DIR": tmpdir}
            runner.invoke(app, ["keys", "generate", "test-agent"], env=env)
            result = runner.invoke(app, ["keys", "delete", "test-agent", "--force"], env=env)
            assert result.exit_code == 0
            assert "Deleted" in result.output

    def test_keys_delete_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_KEYS_DIR": tmpdir}
            result = runner.invoke(app, ["keys", "delete", "nonexistent", "--force"], env=env)
            assert result.exit_code == 1


# =============================================================================
# carryall credentials
# =============================================================================


class TestCredentials:
    def test_credentials_issue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            keys_dir = os.path.join(tmpdir, "keys")
            creds_dir = os.path.join(tmpdir, "creds")
            os.makedirs(keys_dir)
            os.makedirs(creds_dir)
            env = {
                "CARRYALL_KEYS_DIR": keys_dir,
                "CARRYALL_CREDENTIALS_DIR": creds_dir,
            }
            result = runner.invoke(
                app,
                ["credentials", "issue", "test-agent", "--scopes", "vault:finance:read", "--ttl", "1h"],
                env=env,
            )
            assert result.exit_code == 0
            assert "Envelope ID" in result.output

            # Credential should be saved to creds_dir
            cred_files = list(Path(creds_dir).glob("*.json"))
            assert len(cred_files) == 1

    def test_credentials_list_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_CREDENTIALS_DIR": tmpdir}
            result = runner.invoke(app, ["credentials", "list"], env=env)
            assert result.exit_code == 0
            assert "No credentials found" in result.output

    def test_credentials_issue_and_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            keys_dir = os.path.join(tmpdir, "keys")
            creds_dir = os.path.join(tmpdir, "creds")
            os.makedirs(keys_dir)
            os.makedirs(creds_dir)
            env = {
                "CARRYALL_KEYS_DIR": keys_dir,
                "CARRYALL_CREDENTIALS_DIR": creds_dir,
            }
            runner.invoke(
                app,
                ["credentials", "issue", "agent-x", "--scopes", "vault:hr:read", "--ttl", "24h"],
                env=env,
            )
            result = runner.invoke(app, ["credentials", "list"], env=env)
            assert result.exit_code == 0
            assert "agent-x" in result.output

    def test_credentials_show(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            keys_dir = os.path.join(tmpdir, "keys")
            creds_dir = os.path.join(tmpdir, "creds")
            os.makedirs(keys_dir)
            os.makedirs(creds_dir)
            env = {
                "CARRYALL_KEYS_DIR": keys_dir,
                "CARRYALL_CREDENTIALS_DIR": creds_dir,
            }
            runner.invoke(
                app,
                ["credentials", "issue", "agent-y", "--scopes", "vault:finance:read"],
                env=env,
            )
            # Find the credential file
            cred_file = list(Path(creds_dir).glob("*.json"))[0]
            envelope_id = cred_file.stem

            result = runner.invoke(app, ["credentials", "show", envelope_id], env=env)
            assert result.exit_code == 0
            assert "agent-y" in result.output

    def test_credentials_revoke(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            keys_dir = os.path.join(tmpdir, "keys")
            creds_dir = os.path.join(tmpdir, "creds")
            os.makedirs(keys_dir)
            os.makedirs(creds_dir)
            env = {
                "CARRYALL_KEYS_DIR": keys_dir,
                "CARRYALL_CREDENTIALS_DIR": creds_dir,
            }
            runner.invoke(
                app,
                ["credentials", "issue", "agent-z", "--scopes", "vault:hr:read"],
                env=env,
            )
            cred_file = list(Path(creds_dir).glob("*.json"))[0]
            envelope_id = cred_file.stem

            result = runner.invoke(app, ["credentials", "revoke", envelope_id, "--force"], env=env)
            assert result.exit_code == 0
            assert "Revoked" in result.output
            assert not cred_file.exists()


# =============================================================================
# carryall audit
# =============================================================================


class TestAudit:
    def test_audit_no_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_DB": os.path.join(tmpdir, "nonexistent.db")}
            result = runner.invoke(app, ["audit"], env=env)
            assert result.exit_code == 0
            assert "No audit log found" in result.output

    def test_audit_empty_database(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # Create the database with schema
            EnvelopeStore(db_path)
            env = {"CARRYALL_DB": db_path}
            result = runner.invoke(app, ["audit"], env=env)
            assert result.exit_code == 0
            assert "No audit entries found" in result.output
        finally:
            os.unlink(db_path)

    def test_audit_stats(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            EnvelopeStore(db_path)
            env = {"CARRYALL_DB": db_path}
            result = runner.invoke(app, ["audit", "stats"], env=env)
            assert result.exit_code == 0
            assert "Audit Statistics" in result.output
        finally:
            os.unlink(db_path)

    def test_audit_stats_json(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            EnvelopeStore(db_path)
            env = {"CARRYALL_DB": db_path}
            result = runner.invoke(app, ["audit", "stats", "--format", "json"], env=env)
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "envelopes" in data
            assert "audit_trail" in data
        finally:
            os.unlink(db_path)

    def test_audit_export(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            EnvelopeStore(db_path)
            env = {"CARRYALL_DB": db_path}
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as out:
                output_path = out.name
            result = runner.invoke(
                app,
                ["audit", "export", "--output", output_path],
                env=env,
            )
            assert result.exit_code == 0
            data = json.loads(Path(output_path).read_text())
            assert "entries" in data
            os.unlink(output_path)
        finally:
            os.unlink(db_path)

    def test_audit_verify(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            EnvelopeStore(db_path)
            env = {"CARRYALL_DB": db_path}
            result = runner.invoke(app, ["audit", "--verify"], env=env)
            assert result.exit_code == 0
        finally:
            os.unlink(db_path)


# =============================================================================
# carryall policy
# =============================================================================


class TestPolicy:
    @pytest.fixture
    def policy_file(self):
        """Create a minimal valid policy file."""
        content = {
            "organization": "Test Org",
            "version": "1.0.0",
            "agents": {
                "test-agent": {
                    "scopes": ["vault:finance:read"],
                    "constraints": {},
                }
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml
            yaml.dump(content, f)
            path = f.name
        yield path
        os.unlink(path)

    def test_policy_validate_valid(self, policy_file):
        result = runner.invoke(app, ["policy", "validate", policy_file])
        assert result.exit_code == 0
        assert "Valid" in result.output

    def test_policy_validate_missing_file(self):
        result = runner.invoke(app, ["policy", "validate", "/nonexistent/file.yaml"])
        assert result.exit_code == 1

    def test_policy_show(self, policy_file):
        result = runner.invoke(app, ["policy", "show", policy_file])
        assert result.exit_code == 0
        assert "Test Org" in result.output

    def test_policy_show_json(self, policy_file):
        result = runner.invoke(app, ["policy", "show", policy_file, "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["organization"] == "Test Org"


# =============================================================================
# carryall db
# =============================================================================


class TestDB:
    def test_db_status_no_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"CARRYALL_DB": os.path.join(tmpdir, "nonexistent.db")}
            result = runner.invoke(app, ["db", "status"], env=env)
            assert result.exit_code == 0
            assert "No database found" in result.output

    def test_db_status_existing(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            EnvelopeStore(db_path)
            env = {"CARRYALL_DB": db_path}
            result = runner.invoke(app, ["db", "status"], env=env)
            assert result.exit_code == 0
            assert "Schema version" in result.output
        finally:
            os.unlink(db_path)

    def test_db_migrate(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            EnvelopeStore(db_path)
            env = {"CARRYALL_DB": db_path}
            result = runner.invoke(app, ["db", "migrate"], env=env)
            assert result.exit_code == 0
            assert "up to date" in result.output
        finally:
            os.unlink(db_path)


# =============================================================================
# carryall backends
# =============================================================================


class TestBackends:
    def test_backends_list(self):
        result = runner.invoke(app, ["backends", "list"])
        assert result.exit_code == 0
        assert "sovereign-life-os" in result.output

    def test_backends_inspect_unknown(self):
        result = runner.invoke(app, ["backends", "inspect", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown backend" in result.output


# =============================================================================
# carryall mcp config
# =============================================================================


class TestMCPConfig:
    def test_mcp_config(self):
        result = runner.invoke(app, ["mcp", "config"])
        assert result.exit_code == 0
        assert "mcpServers" in result.output
        assert "carryall" in result.output
