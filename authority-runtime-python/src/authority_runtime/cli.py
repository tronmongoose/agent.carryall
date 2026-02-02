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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .keys import AgentKeyStore
from .envelope import create_simple_envelope, generate_key_pair, verify_signature
from .storage import EnvelopeStore
from .types import AuthorityEnvelope
from .backends.slos import SlosBackend, Decision, parse_slos_uri

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
mcp_app = typer.Typer(help="MCP server for Clawdbot integration")

app.add_typer(keys_app, name="keys")
app.add_typer(credentials_app, name="credentials")
app.add_typer(backends_app, name="backends")
app.add_typer(audit_app, name="audit")
app.add_typer(mcp_app, name="mcp")


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
        console.print(f"Generate one with: carryall keys generate <agent-id>")
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
            console.print(f"[bold]Backend:[/bold] Sovereign Life OS")
            console.print(f"[bold]Vaults:[/bold]")
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
        # Verify audit log integrity
        # Note: Currently no hash-chaining, so just check signature validity
        console.print("[yellow]Warning:[/yellow] Audit log hash-chaining not yet implemented.")
        console.print("Checking signature validity of stored envelopes...")

        stats = store.get_stats()
        console.print(f"Total envelopes: {stats['envelopes']['total']}")
        console.print(f"Total audit entries: {stats['audit_trail']['total_actions']}")
        console.print(f"Signature failures: {stats['audit_trail']['signature_failures']}")

        if stats['audit_trail']['signature_failures'] > 0:
            console.print("[red]⚠ Integrity check FAILED - signature failures detected[/red]")
        else:
            console.print("[green]✓ All recorded signatures valid[/green]")
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
    limit: int = typer.Option(10000, "--limit", "-n", help="Maximum entries to export"),
):
    """Export audit trail to JSON file for compliance archival.

    Creates a portable JSON export of the audit trail that can be
    submitted to compliance systems or archived.
    """
    import json

    db_path = Path(get_db_path()).expanduser()

    if not db_path.exists():
        console.print("No audit database found.")
        return

    store = EnvelopeStore(str(db_path))
    entries = store.get_audit_trail(agent_id=agent_id, limit=limit)
    stats = store.get_stats()

    export_data = {
        "export_timestamp": datetime.now(timezone.utc).isoformat(),
        "database_path": str(db_path),
        "statistics": stats,
        "filter_agent_id": agent_id,
        "entry_count": len(entries),
        "entries": entries,
    }

    output_path = Path(output)
    output_path.write_text(json.dumps(export_data, indent=2, default=str))

    console.print(f"[green]✓ Exported {len(entries)} audit entries to {output_path}[/green]")
    console.print(f"  Total size: {output_path.stat().st_size / 1024:.1f} KB")


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
