"""
Carryall CLI - Authority Runtime command-line interface.

Commands:
    carryall init                    Initialize carryall in a directory
    carryall keys generate           Generate new Ed25519 keypair
    carryall keys list               List stored agent keys
    carryall keys show               Show agent's public key
    carryall keys delete             Delete agent's key
    carryall credentials issue       Issue a new envelope/credential
    carryall credentials list        List stored credentials
    carryall credentials show        Show credential details
    carryall credentials revoke      Revoke a credential
    carryall shell                   Interactive policy-enforced shell
    carryall test                    Dry-run policy evaluation
    carryall backends list           List registered backends
    carryall backends inspect        Inspect a backend
    carryall audit                   Query audit log
    carryall audit --verify          Verify audit log integrity
    carryall mcp serve               Start MCP server for Clawdbot integration
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .keys import AgentKeyStore
from .envelope import create_simple_envelope
from .storage import EnvelopeStore
from .compliance import ComplianceReport
from .policy import PolicyEngine, PolicyValidationError
from .types import AuthorityEnvelope
from .backends.slos import SlosBackend, Decision

# Initialize Typer app
app = typer.Typer(
    name="carryall",
    help="Authority Runtime CLI - Cryptographic permissions for AI agents",
    add_completion=False,
)

console = Console()

# Sub-apps
keys_app = typer.Typer(help="Manage agent Ed25519 keys")
credentials_app = typer.Typer(help="Manage authority envelopes/credentials")
backends_app = typer.Typer(help="Manage backend adapters")
audit_app = typer.Typer(help="Query and verify audit logs")
compliance_app = typer.Typer(help="FERPA/compliance reporting and attestation")
policy_app = typer.Typer(help="YAML policy management")
mcp_app = typer.Typer(help="MCP server for Clawdbot integration")
db_app = typer.Typer(help="Database management")

app.add_typer(keys_app, name="keys")
app.add_typer(credentials_app, name="credentials")
app.add_typer(backends_app, name="backends")
app.add_typer(audit_app, name="audit")
app.add_typer(compliance_app, name="compliance")
app.add_typer(policy_app, name="policy")
app.add_typer(mcp_app, name="mcp")
app.add_typer(db_app, name="db")


def get_keys_dir() -> str:
    """Get keys directory from environment or default."""
    return os.environ.get("CARRYALL_KEYS_DIR", "~/.carryall/keys")


def get_db_path() -> str:
    """Get database path from environment or default."""
    return os.environ.get("CARRYALL_DB", "~/.carryall/authority.db")


def get_credentials_dir() -> str:
    """Get credentials directory from environment or default."""
    return os.environ.get("CARRYALL_CREDENTIALS_DIR", "~/.carryall/credentials")


# =============================================================================
# Root commands
# =============================================================================


@app.command()
def init(
    path: str = typer.Argument(".", help="Directory to initialize"),
):
    """Initialize carryall configuration in a directory."""
    init_path = Path(path).expanduser().resolve()

    # Create .carryall directory
    carryall_dir = init_path / ".carryall"
    carryall_dir.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    (carryall_dir / "keys").mkdir(exist_ok=True)
    (carryall_dir / "credentials").mkdir(exist_ok=True)

    # Create config file
    config_path = carryall_dir / "config.json"
    if not config_path.exists():
        config = {
            "version": "1.0.0",
            "keys_dir": str(carryall_dir / "keys"),
            "credentials_dir": str(carryall_dir / "credentials"),
            "db_path": str(carryall_dir / "authority.db"),
            "backends": {
                "slos": {
                    "enabled": False,
                    "config_path": None,
                }
            },
        }
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

    console.print(f"[green]✓[/green] Initialized carryall in {carryall_dir}")
    console.print(f"  Keys:        {carryall_dir / 'keys'}")
    console.print(f"  Credentials: {carryall_dir / 'credentials'}")
    console.print(f"  Database:    {carryall_dir / 'authority.db'}")
    console.print(f"  Config:      {config_path}")


@app.command()
def shell(
    agent: str = typer.Option(..., "--agent", "-a", help="Agent ID to use"),
    mock: bool = typer.Option(True, "--mock/--no-mock", help="Use mock SLOS backend"),
):
    """Start interactive shell with policy-enforced access.

    Example:
        carryall shell --agent finance-agent

    Commands inside the shell:
        vaults              List available vaults
        read <uri>          Read a document (policy-enforced)
        check <action> <uri>  Check access without acting
        switch <agent-id>   Switch agents
        audit --since 1h    Show recent audit trail
        help                Show all commands
    """
    from .shell import CarryallShell

    shell_instance = CarryallShell(
        agent_id=agent,
        keys_dir=get_keys_dir(),
        db_path=get_db_path(),
        credentials_dir=get_credentials_dir(),
        mock=mock,
    )
    shell_instance.run()


@app.command()
def test(
    credential: str = typer.Option(..., "--credential", "-c", help="Path to credential/envelope JSON"),
    action: str = typer.Option(..., "--action", "-a", help="Action to test (read, write, delete)"),
    resource: str = typer.Option(..., "--resource", "-r", help="Resource URI (e.g., slos://vaults/finance/doc-id)"),
    mock: bool = typer.Option(False, "--mock", help="Use mock backend (no real SLOS)"),
):
    """Dry-run policy evaluation for a credential and resource."""
    # Load credential
    cred_path = Path(credential).expanduser()
    if not cred_path.exists():
        console.print(f"[red]Error:[/red] Credential file not found: {credential}")
        raise typer.Exit(1)

    with open(cred_path) as f:
        envelope_data = json.load(f)

    envelope = AuthorityEnvelope(**envelope_data)

    # Determine backend from URI
    if resource.startswith("slos://"):
        key_store = AgentKeyStore(get_keys_dir())
        backend = SlosBackend(key_store=key_store)

        result = backend.check_access(envelope, action, resource, mock=mock)

        # Display result
        if result.decision == Decision.ALLOW:
            console.print(f"[green]✓ ALLOW[/green]: {result.reason}")
        elif result.decision == Decision.REQUIRE_APPROVAL:
            console.print(f"[yellow]⚠ REQUIRE_APPROVAL[/yellow]: {result.reason}")
        else:
            console.print(f"[red]✗ DENY[/red]: {result.reason}")

        # Show metadata
        console.print("\nMetadata:")
        for key, value in result.metadata.items():
            console.print(f"  {key}: {value}")

    else:
        console.print(f"[red]Error:[/red] Unknown resource URI scheme: {resource}")
        console.print("Supported: slos://")
        raise typer.Exit(1)


# =============================================================================
# Health check + policy dry-run
# =============================================================================


def _check_keys_dir() -> tuple[str, str]:
    """Returns ("PASS"|"WARN"|"FAIL", detail). Key files must be 0o600."""
    keys_dir = Path(get_keys_dir()).expanduser()
    if not keys_dir.exists():
        return "FAIL", f"{keys_dir} does not exist (run `carryall init`)"
    key_files = list(keys_dir.glob("*.key"))
    if not key_files:
        return "WARN", f"{keys_dir} present but contains no .key files"
    bad_perms: list[str] = []
    for kf in key_files:
        mode = kf.stat().st_mode & 0o777
        if mode != 0o600:
            bad_perms.append(f"{kf.name} mode={oct(mode)}")
    if bad_perms:
        return "FAIL", f"{len(key_files)} keys, {len(bad_perms)} with wrong perms: {', '.join(bad_perms[:3])}"
    return "PASS", f"{len(key_files)} agent keys, all 0o600"


def _check_audit_chain() -> tuple[str, str]:
    db_path = Path(get_db_path()).expanduser()
    if not db_path.exists():
        return "WARN", f"{db_path} does not exist (no audit yet)"
    try:
        store = EnvelopeStore(str(db_path))
        result = store.verify_audit_chain()
    except Exception as e:
        return "FAIL", f"chain verify raised {type(e).__name__}: {e}"
    if not result["valid"]:
        return "FAIL", (
            f"chain INVALID at entry #{result['first_invalid_id']}: {result.get('error')}"
        )
    stats = store.get_stats()
    sig_fails = stats["audit_trail"]["signature_failures"]
    detail = f"{result['entries_checked']} entries verified"
    if sig_fails:
        return "FAIL", f"{detail}, but {sig_fails} signature failure(s)"
    if result.get("gaps"):
        return "WARN", f"{detail}, {len(result['gaps'])} gap(s) (possible deletions)"
    return "PASS", detail


def _check_approvals_dir() -> tuple[str, str]:
    raw = os.environ.get("CARRYALL_APPROVALS_DIR", "~/slos/vaults/meta/approvals")
    approvals = Path(raw).expanduser()
    if not approvals.exists():
        return "WARN", f"{approvals} does not exist (will be created on first request)"
    if not os.access(approvals, os.W_OK):
        return "FAIL", f"{approvals} is not writable"
    pending = len(list(approvals.glob("*.yaml")))
    return "PASS", f"{approvals} writable, {pending} record(s)"


def _check_backend_config() -> tuple[str, str]:
    cfg = os.environ.get("CARRYALL_SLOS_CONFIG")
    if not cfg:
        return "WARN", "CARRYALL_SLOS_CONFIG not set (will fall back to MemoryBackend)"
    cfg_path = Path(cfg).expanduser()
    if not cfg_path.exists():
        return "FAIL", f"CARRYALL_SLOS_CONFIG points to missing file: {cfg_path}"
    return "PASS", str(cfg_path)


def _check_recent_activity() -> tuple[str, str]:
    db_path = Path(get_db_path()).expanduser()
    if not db_path.exists():
        return "WARN", "no audit DB; nothing to count"
    try:
        store = EnvelopeStore(str(db_path))
        stats = store.get_stats()
    except Exception as e:
        return "WARN", f"stats unavailable: {e}"
    audit = stats["audit_trail"]
    return "PASS", (
        f"{audit['total_actions']} entries "
        f"({audit['successful']} ok, {audit['blocked']} blocked)"
    )


@app.command()
def doctor():
    """Run a health check across keys, audit chain, approvals, and backend config.

    Exits 1 if any check FAILs. WARN does not fail the run.
    """
    checks = [
        ("Agent keys (dir + 0o600 perms)", _check_keys_dir),
        ("Audit hash chain", _check_audit_chain),
        ("Approvals dir", _check_approvals_dir),
        ("Backend config (CARRYALL_SLOS_CONFIG)", _check_backend_config),
        ("Audit activity", _check_recent_activity),
    ]

    table = Table(title="carryall doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    any_fail = False
    for label, fn in checks:
        try:
            status, detail = fn()
        except Exception as e:  # noqa: BLE001 — doctor must not raise
            status, detail = "FAIL", f"check raised {type(e).__name__}: {e}"
        if status == "PASS":
            style = "green"
        elif status == "WARN":
            style = "yellow"
        else:
            style = "red"
            any_fail = True
        table.add_row(label, f"[{style}]{status}[/{style}]", detail)

    console.print(table)
    if any_fail:
        raise typer.Exit(1)


_TIER_LABELS = [
    ("denied_agents", "1. Explicit deny (document)"),
    ("requires_approval", "2. Requires approval (document)"),
    ("allowed_agents", "3. Explicit allow (document)"),
    ("envelope_scope", "4. Envelope scope match"),
]


@app.command()
def explain(
    credential: str = typer.Option(..., "--credential", "-c", help="Path to envelope JSON"),
    action: str = typer.Option(..., "--action", "-a", help="Action (read, write, delete)"),
    resource: str = typer.Option(..., "--resource", "-r", help="Resource URI"),
    mock: bool = typer.Option(False, "--mock", help="Use mock backend"),
):
    """Dry-run policy evaluation and show which precedence tier fired.

    Like ``carryall test`` but renders the 5-tier OPA precedence ladder
    (deny > approval > allow > scope > default-deny) and surfaces the
    structured deny payload (reason_class, suggested_scope, retry_hint)
    that the MCP server returns to agents.
    """
    if not resource.startswith("slos://"):
        console.print(f"[red]Error:[/red] Unknown resource URI scheme: {resource}")
        raise typer.Exit(1)

    cred_path = Path(credential).expanduser()
    if not cred_path.exists():
        console.print(f"[red]Error:[/red] Credential file not found: {credential}")
        raise typer.Exit(1)

    with open(cred_path) as f:
        envelope_data = json.load(f)
    envelope = AuthorityEnvelope(**envelope_data)

    key_store = AgentKeyStore(get_keys_dir())
    backend = SlosBackend(key_store=key_store)
    result = backend.check_access(envelope, action, resource, mock=mock)
    rule = result.metadata.get("rule")

    table = Table(title=f"Precedence ladder for {action} {resource}")
    table.add_column("Tier")
    table.add_column("Fired", justify="center")
    for tier_rule, label in _TIER_LABELS:
        marker = "[bold green]✓[/bold green]" if rule == tier_rule else ""
        table.add_row(label, marker)
    final_tier = "5. Default deny" if rule is None and result.decision == Decision.DENY else "—"
    table.add_row(final_tier, "[bold green]✓[/bold green]" if rule is None and result.decision == Decision.DENY else "")
    console.print(table)

    if result.decision == Decision.ALLOW:
        console.print(f"\n[green]✓ ALLOW[/green]: {result.reason}")
    elif result.decision == Decision.REQUIRE_APPROVAL:
        console.print(f"\n[yellow]⚠ REQUIRE_APPROVAL[/yellow]: {result.reason}")
    else:
        from .enforce import classify_denial
        classified = classify_denial(result.reason, result.metadata)
        console.print(f"\n[red]✗ DENY[/red]: {result.reason}")
        if classified["reason_class"]:
            console.print(f"  reason_class:    {classified['reason_class']}")
        if classified["suggested_scope"]:
            console.print(f"  suggested_scope: {classified['suggested_scope']}")
        if classified["retry_hint"]:
            console.print(f"  retry_hint:      {classified['retry_hint']}")

    if result.metadata:
        console.print("\n[dim]Backend metadata:[/dim]")
        for key, value in result.metadata.items():
            console.print(f"  {key}: {value}")


# =============================================================================
# Keys commands
# =============================================================================


@keys_app.command("generate")
def keys_generate(
    agent_id: str = typer.Argument(..., help="Agent identifier (e.g., finance-agent)"),
    overwrite: bool = typer.Option(False, "--overwrite", "-f", help="Overwrite existing key"),
):
    """Generate new Ed25519 keypair for an agent."""
    store = AgentKeyStore(get_keys_dir())

    if store.has_key(agent_id) and not overwrite:
        console.print(f"[red]Error:[/red] Key already exists for {agent_id}")
        console.print("Use --overwrite to replace existing key")
        raise typer.Exit(1)

    public_key, secret_path = store.generate_keypair(agent_id, overwrite=overwrite)

    console.print(f"[green]✓[/green] Generated keypair for {agent_id}")
    console.print()
    console.print("Public key (add to SLOS config/agents.yaml):")
    console.print(f"  {agent_id}: {public_key}")
    console.print()
    console.print(f"Secret key saved to: {secret_path}")


@keys_app.command("list")
def keys_list():
    """List all agents with stored keys."""
    store = AgentKeyStore(get_keys_dir())
    agents = store.list_agents()

    if not agents:
        console.print("No agent keys found.")
        console.print("Generate one with: carryall keys generate <agent-id>")
        return

    table = Table(title="Agent Keys")
    table.add_column("Agent ID", style="cyan")
    table.add_column("Public Key (first 32 chars)")

    for agent_id in agents:
        try:
            public_key = store.get_public_key_base64(agent_id)
            table.add_row(agent_id, public_key[:32] + "...")
        except Exception as e:
            table.add_row(agent_id, f"[red]Error: {e}[/red]")

    console.print(table)


@keys_app.command("show")
def keys_show(
    agent_id: str = typer.Argument(..., help="Agent identifier"),
):
    """Show agent's public key."""
    store = AgentKeyStore(get_keys_dir())

    if not store.has_key(agent_id):
        console.print(f"[red]Error:[/red] No key found for {agent_id}")
        raise typer.Exit(1)

    public_key = store.get_public_key_base64(agent_id)
    console.print(f"Agent: {agent_id}")
    console.print(f"Public key (base64 for SLOS config): {public_key}")


@keys_app.command("delete")
def keys_delete(
    agent_id: str = typer.Argument(..., help="Agent identifier"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete agent's key."""
    store = AgentKeyStore(get_keys_dir())

    if not store.has_key(agent_id):
        console.print(f"[red]Error:[/red] No key found for {agent_id}")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Delete key for {agent_id}? This cannot be undone.")
        if not confirm:
            raise typer.Exit(0)

    store.delete_key(agent_id)
    console.print(f"[green]✓[/green] Deleted key for {agent_id}")


@keys_app.command("sync")
def keys_sync(
    slos_config: str = typer.Option(None, "--slos-config", help="Path to SLOS agents.yaml"),
):
    """Check which SLOS agents have keys in carryall."""
    store = AgentKeyStore(get_keys_dir())

    # Known SLOS agents (hardcoded for now, could read from config)
    slos_agents = [
        "executive-agent",
        "finance-agent",
        "startup-agent",
        "health-agent",
        "personal-agent",
    ]

    console.print("Agent key status:")
    for agent in slos_agents:
        if store.has_key(agent):
            console.print(f"  [green]✓[/green] {agent}")
        else:
            console.print(f"  [red]✗[/red] {agent} (missing)")

    missing = [a for a in slos_agents if not store.has_key(a)]
    if missing:
        console.print()
        console.print("To generate missing keys:")
        for agent in missing:
            console.print(f"  carryall keys generate {agent}")


# =============================================================================
# Credentials commands
# =============================================================================


@credentials_app.command("issue")
def credentials_issue(
    agent_id: str = typer.Argument(..., help="Agent identifier"),
    scopes: str = typer.Option(..., "--scopes", "-s", help="Comma-separated scopes"),
    ttl: str = typer.Option("24h", "--ttl", "-t", help="Time to live (e.g., 1h, 24h, 7d)"),
    output: str = typer.Option(None, "--output", "-o", help="Output file path"),
):
    """Issue a new envelope/credential for an agent."""
    # Parse TTL
    ttl_seconds = parse_ttl(ttl)

    # Parse scopes
    scope_list = [s.strip() for s in scopes.split(",")]

    # Load or generate signing key
    key_store = AgentKeyStore(get_keys_dir())

    if not key_store.has_key(agent_id):
        console.print(f"[yellow]Warning:[/yellow] No key found for {agent_id}, generating new one...")
        public_key_b64, _ = key_store.generate_keypair(agent_id)
        console.print(f"Generated keypair. Public key: {public_key_b64}")

    # Get private key for signing
    signing_key = key_store.load_signing_key(agent_id)
    private_key_hex = signing_key.encode().hex()

    # Create envelope
    envelope = create_simple_envelope(
        agent_id=agent_id,
        scopes=scope_list,
        private_key=private_key_hex,
        ttl_seconds=ttl_seconds,
    )

    # Output
    envelope_json = envelope.model_dump_json(indent=2)

    if output:
        output_path = Path(output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(envelope_json)
        console.print(f"[green]✓[/green] Credential saved to: {output_path}")
    else:
        console.print(envelope_json)

    # Also save to credentials directory
    creds_dir = Path(get_credentials_dir()).expanduser()
    creds_dir.mkdir(parents=True, exist_ok=True)
    cred_file = creds_dir / f"{envelope.envelope_id}.json"
    with open(cred_file, "w") as f:
        f.write(envelope_json)

    console.print()
    console.print(f"Envelope ID: {envelope.envelope_id}")
    console.print(f"Agent: {agent_id}")
    console.print(f"Scopes: {scope_list}")
    console.print(f"Expires: {envelope.expires_at}")


@credentials_app.command("list")
def credentials_list():
    """List stored credentials."""
    creds_dir = Path(get_credentials_dir()).expanduser()

    if not creds_dir.exists():
        console.print("No credentials directory found.")
        return

    cred_files = list(creds_dir.glob("*.json"))

    if not cred_files:
        console.print("No credentials found.")
        return

    table = Table(title="Stored Credentials")
    table.add_column("Envelope ID", style="cyan")
    table.add_column("Agent")
    table.add_column("Scopes")
    table.add_column("Expires")
    table.add_column("Status")

    now = datetime.now(timezone.utc)

    for cred_file in sorted(cred_files):
        try:
            with open(cred_file) as f:
                data = json.load(f)

            envelope = AuthorityEnvelope(**data)
            expires_str = envelope.expires_at.replace("Z", "+00:00")
            expires = datetime.fromisoformat(expires_str)

            status = "[green]active[/green]" if expires > now else "[red]expired[/red]"
            scopes_str = ", ".join(envelope.authority.scopes[:3])
            if len(envelope.authority.scopes) > 3:
                scopes_str += f" (+{len(envelope.authority.scopes) - 3})"

            table.add_row(
                envelope.envelope_id,
                envelope.agent_id,
                scopes_str,
                envelope.expires_at[:19],
                status,
            )
        except Exception as e:
            table.add_row(cred_file.stem, "[red]Error[/red]", str(e), "", "")

    console.print(table)


@credentials_app.command("show")
def credentials_show(
    envelope_id: str = typer.Argument(..., help="Envelope ID or path to credential file"),
):
    """Show credential details."""
    # Try as file path first
    cred_path = Path(envelope_id).expanduser()
    if cred_path.exists():
        with open(cred_path) as f:
            data = json.load(f)
    else:
        # Try in credentials directory
        creds_dir = Path(get_credentials_dir()).expanduser()
        cred_file = creds_dir / f"{envelope_id}.json"
        if cred_file.exists():
            with open(cred_file) as f:
                data = json.load(f)
        else:
            console.print(f"[red]Error:[/red] Credential not found: {envelope_id}")
            raise typer.Exit(1)

    envelope = AuthorityEnvelope(**data)

    console.print(f"[bold]Envelope ID:[/bold] {envelope.envelope_id}")
    console.print(f"[bold]Agent:[/bold] {envelope.agent_id}")
    console.print(f"[bold]Provider:[/bold] {envelope.provider}")
    console.print(f"[bold]Created:[/bold] {envelope.created_at}")
    console.print(f"[bold]Expires:[/bold] {envelope.expires_at}")
    console.print(f"[bold]TTL:[/bold] {envelope.ttl_seconds} seconds")
    console.print()
    console.print("[bold]Scopes:[/bold]")
    for scope in envelope.authority.scopes:
        console.print(f"  - {scope}")
    console.print()
    console.print("[bold]Resources:[/bold]")
    for resource in envelope.authority.resources:
        console.print(f"  - {resource}")
    console.print()
    console.print(f"[bold]Signature:[/bold] {envelope.signature[:32]}...")

    # Check expiration
    now = datetime.now(timezone.utc)
    expires_str = envelope.expires_at.replace("Z", "+00:00")
    expires = datetime.fromisoformat(expires_str)

    if expires <= now:
        console.print()
        console.print("[red]⚠ This credential has expired[/red]")


@credentials_app.command("revoke")
def credentials_revoke(
    envelope_id: str = typer.Argument(..., help="Envelope ID to revoke"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Revoke a credential (delete from local storage)."""
    creds_dir = Path(get_credentials_dir()).expanduser()
    cred_file = creds_dir / f"{envelope_id}.json"

    if not cred_file.exists():
        console.print(f"[red]Error:[/red] Credential not found: {envelope_id}")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Revoke credential {envelope_id}? This cannot be undone.")
        if not confirm:
            raise typer.Exit(0)

    cred_file.unlink()
    console.print(f"[green]✓[/green] Revoked credential: {envelope_id}")


# =============================================================================
# Backends commands
# =============================================================================


@backends_app.command("list")
def backends_list():
    """List registered backends."""
    table = Table(title="Backends")
    table.add_column("Backend", style="cyan")
    table.add_column("Status")
    table.add_column("Description")

    # SLOS backend
    slos_status = "[yellow]available[/yellow]"
    table.add_row("sovereign-life-os", slos_status, "Sovereign Life OS vault backend")

    console.print(table)


@backends_app.command("inspect")
def backends_inspect(
    backend: str = typer.Argument(..., help="Backend name"),
    mock: bool = typer.Option(False, "--mock", help="Use mock data"),
):
    """Inspect a backend's available resources."""
    if backend in ["sovereign-life-os", "slos"]:
        key_store = AgentKeyStore(get_keys_dir())
        slos = SlosBackend(key_store=key_store)

        try:
            vaults = slos.list_vaults("executive-agent", mock=mock)
            console.print("[bold]Backend:[/bold] Sovereign Life OS")
            console.print("[bold]Vaults:[/bold]")
            for vault in vaults:
                console.print(f"  - {vault}")
        except FileNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            console.print("Generate keys first: carryall keys generate executive-agent")
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
    else:
        console.print(f"[red]Error:[/red] Unknown backend: {backend}")
        console.print("Available: sovereign-life-os")
        raise typer.Exit(1)


# =============================================================================
# Audit commands
# =============================================================================


@audit_app.callback(invoke_without_command=True)
def audit_query(
    ctx: typer.Context,
    agent_id: Optional[str] = typer.Option(None, "--agent", "-a", help="Filter by agent ID"),
    limit: int = typer.Option(100, "--limit", "-n", help="Maximum entries to show"),
    verify: bool = typer.Option(False, "--verify", help="Verify audit log integrity"),
):
    """Query audit log."""
    if ctx.invoked_subcommand is not None:
        return

    db_path = Path(get_db_path()).expanduser()

    if not db_path.exists():
        console.print("No audit log found.")
        return

    store = EnvelopeStore(str(db_path))

    if verify:
        # Verify audit trail hash chain integrity
        console.print("Verifying audit trail hash chain...")

        result = store.verify_audit_chain()

        console.print(f"Entries verified: {result['entries_checked']}")

        if result["gaps"]:
            console.print(f"[yellow]WARNING: {len(result['gaps'])} gap(s) detected (possible deletions)[/yellow]")
            for expected, actual in result["gaps"]:
                console.print(f"  Gap: expected id {expected}, found id {actual}")

        if result["valid"]:
            console.print(f"[green]Audit chain VALID. {result['entries_checked']} entries verified.[/green]")
        else:
            console.print(f"[red]Audit chain INVALID at entry #{result['first_invalid_id']}[/red]")
            console.print(f"  Error: {result['error']}")

        # Also check signature stats
        stats = store.get_stats()
        if stats['audit_trail']['signature_failures'] > 0:
            console.print(f"[red]⚠ {stats['audit_trail']['signature_failures']} signature failure(s) detected[/red]")

        return

    # Query audit trail
    entries = store.get_audit_trail(agent_id=agent_id, limit=limit)

    if not entries:
        console.print("No audit entries found.")
        return

    table = Table(title="Audit Trail")
    table.add_column("Timestamp", style="dim")
    table.add_column("Agent")
    table.add_column("Action")
    table.add_column("Result")
    table.add_column("Envelope ID")

    for entry in entries:
        result_style = "green" if entry["result"] == "success" else "red"
        table.add_row(
            entry["timestamp"][:19],
            entry["agent_id"],
            entry["action"],
            f"[{result_style}]{entry['result']}[/{result_style}]",
            entry["envelope_id"][:16] + "...",
        )

    console.print(table)


@audit_app.command("stats")
def audit_stats(
    output_format: str = typer.Option("table", "--format", "-f", help="Output format: table, json"),
):
    """Show audit statistics for compliance reporting.

    Displays summary metrics about agent activity, policy enforcement,
    and signature verification - useful for compliance dashboards.
    """
    db_path = Path(get_db_path()).expanduser()

    if not db_path.exists():
        console.print("No audit database found.")
        return

    store = EnvelopeStore(str(db_path))
    stats = store.get_stats()

    if output_format == "json":
        import json
        console.print(json.dumps(stats, indent=2))
        return

    # Table output
    console.print("\n[bold]Audit Statistics[/bold]")
    console.print(f"Database: {db_path}\n")

    # Envelope stats
    env_table = Table(title="Envelope Statistics")
    env_table.add_column("Metric", style="cyan")
    env_table.add_column("Value", style="green")
    env_table.add_row("Total Envelopes", str(stats["envelopes"]["total"]))
    env_table.add_row("Unique Agents", str(stats["envelopes"]["unique_agents"]))
    env_table.add_row("Unique Policies", str(stats["envelopes"]["unique_policies"]))
    console.print(env_table)

    # Audit trail stats
    audit_table = Table(title="Audit Trail Statistics")
    audit_table.add_column("Metric", style="cyan")
    audit_table.add_column("Value", style="green")
    audit_table.add_row("Total Actions", str(stats["audit_trail"]["total_actions"]))
    audit_table.add_row("Successful", str(stats["audit_trail"]["successful"]))
    audit_table.add_row("Blocked", str(stats["audit_trail"]["blocked"]))
    audit_table.add_row("Signature Failures", str(stats["audit_trail"]["signature_failures"]))

    # Calculate success rate
    total = stats["audit_trail"]["total_actions"]
    if total > 0:
        success_rate = (stats["audit_trail"]["successful"] / total) * 100
        audit_table.add_row("Success Rate", f"{success_rate:.1f}%")

    console.print(audit_table)

    # Security summary
    if stats["audit_trail"]["signature_failures"] > 0:
        console.print("\n[red]⚠ WARNING: Signature failures detected - possible tampering[/red]")
    else:
        console.print("\n[green]✓ All signatures valid - audit trail integrity maintained[/green]")


@audit_app.command("export")
def audit_export(
    output: str = typer.Option("audit_export.json", "--output", "-o", help="Output file path"),
    agent_id: Optional[str] = typer.Option(None, "--agent", "-a", help="Filter by agent ID"),
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Time window (e.g., 30d, 90d, 365d)"),
    limit: int = typer.Option(10000, "--limit", "-n", help="Maximum entries to export"),
    fmt: str = typer.Option("json", "--format", "-f", help="Output format: json or csv"),
):
    """Export audit trail to JSON or CSV file for compliance archival.

    Creates a portable export of the audit trail that can be
    submitted to compliance systems or archived.

    Examples:
        carryall audit export --since 90d --output report.json
        carryall audit export --since 365d --format csv --output annual.csv
    """
    import json as json_mod
    import csv

    db_path = Path(get_db_path()).expanduser()

    if not db_path.exists():
        console.print("No audit database found.")
        return

    store = EnvelopeStore(str(db_path))

    # Parse --since into start_time
    start_time = None
    if since:
        start_time = _parse_since(since)

    entries = store.get_audit_trail(agent_id=agent_id, start_time=start_time, limit=limit)
    stats = store.get_stats()

    output_path = Path(output)

    if fmt == "csv":
        if entries:
            with open(output_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=entries[0].keys())
                writer.writeheader()
                writer.writerows(entries)
        else:
            output_path.write_text("")
    else:
        export_data = {
            "export_timestamp": datetime.now(timezone.utc).isoformat(),
            "database_path": str(db_path),
            "statistics": stats,
            "filter_agent_id": agent_id,
            "filter_since": since,
            "entry_count": len(entries),
            "entries": entries,
        }
        output_path.write_text(json_mod.dumps(export_data, indent=2, default=str))

    console.print(f"[green]Exported {len(entries)} audit entries to {output_path}[/green]")
    console.print(f"  Total size: {output_path.stat().st_size / 1024:.1f} KB")


@audit_app.command("archive")
def audit_archive(
    older_than: str = typer.Option(..., "--older-than", help="Archive entries older than (e.g., 365d)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Archive old audit entries to a separate table.

    Moves entries older than the specified threshold to audit_trail_archive.
    Hash chain data is preserved. Gaps in the main table's ID sequence
    will be detected by 'carryall audit --verify' as warnings.

    Examples:
        carryall audit archive --older-than 365d --yes
    """
    db_path = Path(get_db_path()).expanduser()

    if not db_path.exists():
        console.print("No audit database found.")
        return

    # Parse days from the threshold
    if not older_than.endswith("d"):
        console.print("[red]Error: --older-than must be in days (e.g., 365d)[/red]")
        raise typer.Exit(code=1)

    days = int(older_than[:-1])

    if not yes:
        console.print(f"This will archive audit entries older than {days} days.")
        confirm = typer.confirm("Proceed?")
        if not confirm:
            console.print("Aborted.")
            return

    store = EnvelopeStore(str(db_path))
    result = store.archive_audit_entries(older_than_days=days)
    console.print(f"[green]Archived {result['archived_count']} entries (cutoff: {result['cutoff_date'][:10]})[/green]")


def _parse_since(since: str) -> str:
    """Parse a --since value like '30d', '90d', '1y' into an ISO timestamp."""
    from datetime import timedelta
    if since.endswith("d"):
        days = int(since[:-1])
    elif since.endswith("y"):
        days = int(since[:-1]) * 365
    else:
        days = int(since)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.isoformat()


# =============================================================================
# Compliance commands
# =============================================================================


@compliance_app.command("attest")
def compliance_attest(
    agent_id: str = typer.Option(..., "--agent", "-a", help="Agent ID to attest"),
    resource: str = typer.Option(..., "--resource", "-r", help="Resource pattern (SQL LIKE, use % wildcard)"),
    since: str = typer.Option("90d", "--since", "-s", help="Time window (e.g., 30d, 90d, 1y)"),
    output_format: str = typer.Option("table", "--format", "-f", help="Output format: table, json"),
):
    """Prove an agent NEVER accessed a resource (negative attestation).

    The FERPA killer feature: cryptographic proof that an agent did not
    access specific records during a time period.

    Examples:

        carryall compliance attest --agent financial-aid-agent --resource "slos://vaults/student-health/%"

        carryall compliance attest --agent academic-advisor --resource "slos://vaults/student-health/%" --since 90d
    """
    db_path = Path(get_db_path()).expanduser()
    if not db_path.exists():
        console.print("No audit database found.")
        raise typer.Exit(1)

    store = EnvelopeStore(str(db_path))
    report = ComplianceReport(store)

    ttl_seconds = parse_ttl(since)
    start_time = datetime.now(timezone.utc).timestamp() - ttl_seconds

    result = report.negative_attestation(
        agent_id=agent_id,
        resource_pattern=resource,
        start_time=start_time,
    )

    if output_format == "json":
        console.print(json.dumps(result, indent=2))
        return

    if result["confirmed"]:
        console.print(f"\n[green bold]CONFIRMED: {agent_id} did NOT access {resource}[/green bold]")
    else:
        console.print(f"\n[red bold]FAILED: {agent_id} accessed {resource} ({result['count']} events)[/red bold]")

    console.print(f"\n  Agent:    {result['agent_id']}")
    console.print(f"  Resource: {result['resource_pattern']}")
    console.print(f"  Window:   last {since}")
    console.print(f"  Events:   {result['count']}")
    console.print(f"  Hash:     {result['attestation_hash']}")


@compliance_app.command("agent-report")
def compliance_agent_report(
    agent_id: str = typer.Option(..., "--agent", "-a", help="Agent ID"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r", help="Resource pattern filter"),
    since: str = typer.Option("30d", "--since", "-s", help="Time window"),
    output_format: str = typer.Option("table", "--format", "-f", help="Output format: table, json, csv"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file path"),
):
    """Show all access events for an agent.

    Examples:

        carryall compliance agent-report --agent academic-advisor --since 30d

        carryall compliance agent-report --agent financial-aid-agent --resource "slos://vaults/student-health/%" --format csv -o report.csv
    """
    db_path = Path(get_db_path()).expanduser()
    if not db_path.exists():
        console.print("No audit database found.")
        raise typer.Exit(1)

    store = EnvelopeStore(str(db_path))
    report = ComplianceReport(store)

    ttl_seconds = parse_ttl(since)
    start_time = datetime.now(timezone.utc).timestamp() - ttl_seconds

    result = report.agent_access_report(
        agent_id=agent_id,
        resource_pattern=resource,
        start_time=start_time,
    )

    if output_format == "json":
        if output:
            report.export_json(result, output)
            console.print(f"[green]Exported to {output}[/green]")
        else:
            console.print(json.dumps(result, indent=2, default=str))
        return

    if output_format == "csv":
        entries = store.get_audit_trail(
            agent_id=agent_id,
            resource_pattern=resource,
        )
        if output:
            report.export_csv(entries, output)
            console.print(f"[green]Exported {len(entries)} entries to {output}[/green]")
        else:
            console.print(report.export_csv_string(entries))
        return

    # Table output
    summary = result["summary"]
    console.print(f"\n[bold]Agent Access Report: {agent_id}[/bold]")
    console.print(f"  Window: last {since}")

    table = Table()
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total Events", str(summary["total_events"]))
    table.add_row("Successful", str(summary["successful"]))
    table.add_row("Blocked", str(summary["blocked"]))
    table.add_row("Distinct Resources", str(summary["distinct_resources"]))
    console.print(table)


@compliance_app.command("resource-report")
def compliance_resource_report(
    resource: str = typer.Option(..., "--resource", "-r", help="Resource pattern (SQL LIKE)"),
    since: str = typer.Option("30d", "--since", "-s", help="Time window"),
    output_format: str = typer.Option("table", "--format", "-f", help="Output format: table, json"),
):
    """Show all agents that accessed a resource.

    Examples:

        carryall compliance resource-report --resource "slos://vaults/student-records/%"

        carryall compliance resource-report --resource "slos://vaults/student-health/%" --format json
    """
    db_path = Path(get_db_path()).expanduser()
    if not db_path.exists():
        console.print("No audit database found.")
        raise typer.Exit(1)

    store = EnvelopeStore(str(db_path))
    report = ComplianceReport(store)

    ttl_seconds = parse_ttl(since)
    start_time = datetime.now(timezone.utc).timestamp() - ttl_seconds

    result = report.resource_access_report(
        resource_pattern=resource,
        start_time=start_time,
    )

    if output_format == "json":
        console.print(json.dumps(result, indent=2, default=str))
        return

    summary = result["summary"]
    console.print(f"\n[bold]Resource Access Report: {resource}[/bold]")
    console.print(f"  Window: last {since}")

    table = Table()
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total Events", str(summary["total_events"]))
    table.add_row("Distinct Agents", str(summary["distinct_agents"]))
    console.print(table)

    if result.get("agents"):
        agent_table = Table(title="Agents")
        agent_table.add_column("Agent ID", style="cyan")
        agent_table.add_column("Access Count")
        agent_table.add_column("First Access")
        agent_table.add_column("Last Access")
        for agent in result["agents"]:
            agent_table.add_row(
                agent["agent_id"],
                str(agent["access_count"]),
                agent.get("first_access", "")[:19],
                agent.get("last_access", "")[:19],
            )
        console.print(agent_table)


@compliance_app.command("report")
def compliance_report(
    since: str = typer.Option("90d", "--since", "-s", help="Time window (e.g., 30d, 90d, 1y)"),
    output: str = typer.Option("report.html", "--output", "-o", help="Output file path"),
    output_format: str = typer.Option("html", "--format", "-f", help="Output format: html, json"),
    policy: Optional[str] = typer.Option(None, "--policy", "-p", help="Path to YAML policy file (adds data classifications)"),
    title: str = typer.Option("FERPA Compliance Report", "--title", "-t", help="Report title"),
):
    """Generate a comprehensive compliance report.

    Produces a self-contained HTML document that can be emailed to legal.
    Includes executive summary, per-agent breakdown, negative attestation
    matrix, and data classification details.

    Examples:

        carryall compliance report --since 90d --output ferpa-q1.html

        carryall compliance report --since 30d --policy policy.yaml --output report.html

        carryall compliance report --format json --output report.json
    """
    db_path = Path(get_db_path()).expanduser()
    if not db_path.exists():
        console.print("No audit database found.")
        raise typer.Exit(1)

    store = EnvelopeStore(str(db_path))
    report_gen = ComplianceReport(store)

    ttl_seconds = parse_ttl(since)
    start_time = datetime.now(timezone.utc).timestamp() - ttl_seconds

    # Load policy if provided
    policy_summary = None
    if policy:
        try:
            engine = PolicyEngine.load(policy)
            policy_summary = engine.summary()
        except Exception as e:
            console.print(f"[yellow]Warning:[/yellow] Could not load policy: {e}")

    full_report = report_gen.generate_full_report(
        start_time=start_time,
        policy_summary=policy_summary,
        title=title,
    )

    if output_format == "json":
        report_gen.export_json(full_report, output)
        console.print(f"[green]JSON report saved to {output}[/green]")
        return

    # HTML output
    html = report_gen.render_html(full_report)
    with open(output, "w") as f:
        f.write(html)

    console.print(f"[green]HTML compliance report saved to {output}[/green]")
    exec_summary = full_report["executive_summary"]
    console.print(f"  Agents: {exec_summary['total_agents']}, Events: {exec_summary['total_events']}, "
                  f"Blocked: {exec_summary['blocked']}, Attestations: {len(full_report['attestations'])}")


@compliance_app.command("summary")
def compliance_summary(
    since: str = typer.Option("30d", "--since", "-s", help="Time window"),
    output_format: str = typer.Option("table", "--format", "-f", help="Output format: table, json"),
):
    """Show compliance summary across all agents.

    Examples:

        carryall compliance summary --since 30d

        carryall compliance summary --format json
    """
    db_path = Path(get_db_path()).expanduser()
    if not db_path.exists():
        console.print("No audit database found.")
        raise typer.Exit(1)

    store = EnvelopeStore(str(db_path))
    report = ComplianceReport(store)

    result = report.scope_usage_report()

    if output_format == "json":
        console.print(json.dumps(result, indent=2, default=str))
        return

    summary = result["summary"]
    console.print("\n[bold]Compliance Summary[/bold]")

    table = Table()
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total Events", str(summary["total_events"]))
    table.add_row("Distinct Agents", str(summary["distinct_agents"]))
    table.add_row("Successful", str(summary.get("successful", "N/A")))
    table.add_row("Blocked", str(summary.get("blocked", "N/A")))
    console.print(table)

    if result.get("agents"):
        agent_table = Table(title="Agent Activity")
        agent_table.add_column("Agent ID", style="cyan")
        agent_table.add_column("Events")
        agent_table.add_column("Successful")
        agent_table.add_column("Blocked")
        for agent in result["agents"]:
            agent_table.add_row(
                agent["agent_id"],
                str(agent["total_events"]),
                str(agent.get("successful", 0)),
                str(agent.get("blocked", 0)),
            )
        console.print(agent_table)


# =============================================================================
# Policy commands
# =============================================================================


@policy_app.command("validate")
def policy_validate(
    path: str = typer.Argument(..., help="Path to YAML policy file"),
):
    """Validate a YAML policy file and show a summary.

    Examples:

        carryall policy validate examples/edtech-policy.yaml
    """
    try:
        engine = PolicyEngine.load(path)
    except PolicyValidationError as e:
        console.print(f"[red]INVALID:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error loading policy:[/red] {e}")
        raise typer.Exit(1)

    s = engine.summary()
    console.print(f"[green]Valid.[/green] {s['agent_count']} agents, "
                  f"{s['classification_count']} data classifications, "
                  f"{', '.join(s['compliance_frameworks']) or 'no'} compliance framework(s)")
    console.print(f"  Organization: {s['organization']}")
    console.print(f"  Version:      {s['version']}")


@policy_app.command("show")
def policy_show(
    path: str = typer.Argument(..., help="Path to YAML policy file"),
    output_format: str = typer.Option("table", "--format", "-f", help="Output format: table, json"),
):
    """Show policy details in a rich table.

    Examples:

        carryall policy show examples/edtech-policy.yaml

        carryall policy show examples/edtech-policy.yaml --format json
    """
    try:
        engine = PolicyEngine.load(path)
    except PolicyValidationError as e:
        console.print(f"[red]INVALID:[/red] {e}")
        raise typer.Exit(1)

    if output_format == "json":
        console.print(json.dumps(engine.summary(), indent=2))
        return

    s = engine.summary()

    # Header
    console.print(f"\n[bold]{s['organization']}[/bold] — Policy v{s['version']}")
    if s['compliance_frameworks']:
        console.print(f"  Frameworks: {', '.join(s['compliance_frameworks'])}")

    # Agents table
    agent_table = Table(title="Agent Policies")
    agent_table.add_column("Agent ID", style="cyan")
    agent_table.add_column("Scopes")
    agent_table.add_column("Constraints")
    agent_table.add_column("Denied Resources", style="red")

    for agent in s["agents"]:
        constraints_str = ", ".join(
            f"{k}={v}" for k, v in agent["constraints"].items()
        ) or "none"
        denied_str = "\n".join(agent["denied_resources"]) or "none"
        scopes_str = "\n".join(agent["scopes"])
        agent_table.add_row(agent["id"], scopes_str, constraints_str, denied_str)

    console.print(agent_table)

    # Data classifications table
    if s["data_classifications"]:
        dc_table = Table(title="Data Classifications")
        dc_table.add_column("Domain", style="cyan")
        dc_table.add_column("Sensitivity")
        dc_table.add_column("PII Fields", style="yellow")
        dc_table.add_column("Retention")

        for dc in s["data_classifications"]:
            sensitivity_style = {
                "internal": "green",
                "confidential": "yellow",
                "restricted": "red",
            }.get(dc["sensitivity"], "white")

            retention = f"{dc['retention_days']} days" if dc["retention_days"] else "not set"

            dc_table.add_row(
                dc["domain"],
                f"[{sensitivity_style}]{dc['sensitivity']}[/{sensitivity_style}]",
                ", ".join(dc["pii_fields"]) or "none",
                retention,
            )

        console.print(dc_table)


# =============================================================================
# Database commands
# =============================================================================


@db_app.command("migrate")
def db_migrate():
    """Run pending schema migrations.

    Automatically backs up the database before applying migrations.
    Safe to run repeatedly — only pending migrations are applied.
    """
    from .storage import MIGRATIONS

    db_path = Path(get_db_path()).expanduser()
    store = EnvelopeStore(str(db_path))

    version = store.get_schema_version()
    latest = MIGRATIONS[-1][0] if MIGRATIONS else 0

    if version >= latest:
        console.print(f"[green]Database is up to date (version {version}).[/green]")
    else:
        console.print(f"[green]Migrations complete. Schema version: {store.get_schema_version()}[/green]")


@db_app.command("status")
def db_status():
    """Show current schema version and migration history."""
    from .storage import MIGRATIONS

    db_path = Path(get_db_path()).expanduser()

    if not db_path.exists():
        console.print(f"No database found at {db_path}")
        return

    store = EnvelopeStore(str(db_path))
    version = store.get_schema_version()
    latest = MIGRATIONS[-1][0] if MIGRATIONS else 0

    console.print("\n[bold]Database Status[/bold]")
    console.print(f"Path: {db_path}")
    console.print(f"Schema version: {version}")
    console.print(f"Latest available: {latest}")

    if version < latest:
        console.print(f"[yellow]Pending migrations: {latest - version}[/yellow]")
    else:
        console.print("[green]All migrations applied.[/green]")

    history = store.get_migration_history()
    if history:
        table = Table(title="Migration History")
        table.add_column("Version", style="cyan")
        table.add_column("Applied At", style="dim")
        table.add_column("Description")
        for m in history:
            table.add_row(str(m["version"]), m["applied_at"][:19], m["description"])
        console.print(table)


# =============================================================================
# MCP commands
# =============================================================================


@mcp_app.command("serve")
def mcp_serve(
    transport: str = typer.Option("stdio", "--transport", "-t", help="Transport: stdio or http"),
    host: str = typer.Option("0.0.0.0", "--host", "-H", help="HTTP host to bind (http transport only)"),
    port: int = typer.Option(8765, "--port", "-p", help="HTTP port to listen on (http transport only)"),
    mock: bool = typer.Option(False, "--mock", help="Use mock backends (no real SLOS)"),
):
    """Start MCP server for Clawdbot integration.

    Supports two transport modes:

    STDIO (default): Listens on stdin/stdout for JSON-RPC 2.0 requests.
    Use this for local MCP clients like Clawdbot desktop.

    HTTP: Listens on HTTP for JSON-RPC and REST requests.
    Use this for Kubernetes sidecar deployments.

    Tools exposed:
    - carryall_check_access: Check if an envelope allows an action
    - carryall_list_vaults: List SLOS vaults
    - carryall_get_metadata: Get document metadata
    - carryall_audit_log: Query the audit log

    Examples:

        # Stdio mode (for local Clawdbot)
        carryall mcp serve

        # HTTP mode (for Kubernetes sidecar)
        carryall mcp serve --transport http --port 8765

    Clawdbot config (~/.clawdbot/clawdbot.json):

        {
            "mcpServers": {
                "carryall": {
                    "command": "carryall",
                    "args": ["mcp", "serve"]
                }
            }
        }
    """
    import asyncio
    from .mcp_server import CarryallMCPServer

    if transport not in ["stdio", "http"]:
        console.print(f"[red]Error:[/red] Unknown transport: {transport}")
        console.print("Supported: stdio, http")
        raise typer.Exit(1)

    server = CarryallMCPServer()

    try:
        if transport == "http":
            asyncio.run(server.run_http(host, port))
        else:
            asyncio.run(server.run_stdio())
    except KeyboardInterrupt:
        console.print("\nServer stopped.")


@mcp_app.command("config")
def mcp_config():
    """Show Clawdbot configuration for carryall MCP server."""
    config = {
        "mcpServers": {
            "carryall": {
                "command": "carryall",
                "args": ["mcp", "serve"],
            }
        }
    }

    console.print("[bold]Add this to ~/.clawdbot/clawdbot.json:[/bold]")
    console.print()
    console.print(json.dumps(config, indent=2))
    console.print()
    console.print("Or for Claude Desktop (~/.claude/claude_desktop_config.json):")
    console.print()
    console.print(json.dumps(config, indent=2))


# =============================================================================
# Utility functions
# =============================================================================


def parse_ttl(ttl: str) -> int:
    """Parse TTL string (e.g., '1h', '24h', '7d') to seconds."""
    ttl = ttl.strip().lower()

    if ttl.endswith("s"):
        return int(ttl[:-1])
    elif ttl.endswith("m"):
        return int(ttl[:-1]) * 60
    elif ttl.endswith("h"):
        return int(ttl[:-1]) * 3600
    elif ttl.endswith("d"):
        return int(ttl[:-1]) * 86400
    else:
        # Assume seconds
        return int(ttl)


# =============================================================================
# Main entry point
# =============================================================================


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
