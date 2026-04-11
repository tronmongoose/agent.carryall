"""
Carryall Interactive Shell - Policy-enforced access to SLOS data.

Usage:
    carryall shell --agent finance-agent

Commands:
    vaults                          List available vaults
    list <vault>                    List documents in a vault
    read <uri>                      Read a document (policy-enforced)
    metadata <uri>                  Get document metadata
    check <action> <uri>            Check access without reading
    switch <agent-id>               Switch to a different agent
    scopes                          Show current agent's scopes
    audit [--since <duration>]      Show recent audit trail
    whoami                          Show current agent and credential
    help                            Show this help
    exit / quit                     Exit the shell
"""

import json
import readline
import shlex
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from .keys import AgentKeyStore
from .storage import EnvelopeStore
from .envelope import create_simple_envelope
from .types import AuthorityEnvelope
from .backends.slos import SlosBackend, Decision


console = Console()


class CarryallShell:
    """Interactive shell with policy enforcement."""

    def __init__(
        self,
        agent_id: str,
        keys_dir: str,
        db_path: str,
        credentials_dir: str,
        mock: bool = True,
    ):
        self.key_store = AgentKeyStore(keys_dir)
        self.db_path = db_path
        self.credentials_dir = Path(credentials_dir).expanduser()
        self.credentials_dir.mkdir(parents=True, exist_ok=True)
        self.mock = mock
        self.slos = SlosBackend(key_store=self.key_store)

        # Initialize store
        db = Path(db_path).expanduser()
        db.parent.mkdir(parents=True, exist_ok=True)
        self.store = EnvelopeStore(str(db))

        # Load agent
        self.agent_id: str = ""
        self.envelope: Optional[AuthorityEnvelope] = None
        self.public_key_hex: str = ""
        self._switch_agent(agent_id)

    def _switch_agent(self, agent_id: str):
        """Switch to a different agent, loading or creating credentials."""
        if not self.key_store.has_key(agent_id):
            console.print(f"[red]No key found for {agent_id}[/red]")
            console.print(f"Run: carryall keys generate {agent_id}")
            return False

        self.agent_id = agent_id
        # Get hex-encoded public key (for signature verification)
        signing_key = self.key_store.load_signing_key(agent_id)
        self.public_key_hex = signing_key.verify_key.encode().hex()

        # Look for an active credential
        self.envelope = self._find_active_credential(agent_id)

        if not self.envelope:
            # Auto-issue a session credential
            signing_key = self.key_store.load_signing_key(agent_id)
            private_key_hex = signing_key.encode().hex()

            # Default scopes based on agent name
            scopes = self._default_scopes(agent_id)

            self.envelope = create_simple_envelope(
                agent_id=agent_id,
                scopes=scopes,
                private_key=private_key_hex,
                ttl_seconds=3600,  # 1 hour session
            )

            # Save it
            cred_file = self.credentials_dir / f"{self.envelope.envelope_id}.json"
            with open(cred_file, "w") as f:
                f.write(self.envelope.model_dump_json(indent=2))

            console.print(f"[dim]Issued session credential: {self.envelope.envelope_id} (1h TTL)[/dim]")

        console.print(f"[green]Active agent:[/green] {agent_id}")
        console.print(f"[dim]Scopes: {', '.join(self.envelope.authority.scopes)}[/dim]")
        return True

    def _default_scopes(self, agent_id: str) -> list[str]:
        """Generate default scopes based on agent naming convention."""
        # Extract domain from agent name (e.g., finance-agent -> finance)
        domain = agent_id.replace("-agent", "").replace("_agent", "")

        return [
            f"vault:{domain}:read",
            f"vault:{domain}:write",
            "vault:shared:read",
        ]

    def _find_active_credential(self, agent_id: str) -> Optional[AuthorityEnvelope]:
        """Find an active (non-expired) credential for this agent."""
        now = datetime.now(timezone.utc)

        for cred_file in self.credentials_dir.glob("*.json"):
            try:
                with open(cred_file) as f:
                    data = json.load(f)
                envelope = AuthorityEnvelope(**data)

                if envelope.agent_id != agent_id:
                    continue

                expires_str = envelope.expires_at.replace("Z", "+00:00")
                expires = datetime.fromisoformat(expires_str)

                if expires > now:
                    return envelope
            except Exception:
                continue

        return None

    def _log_action(self, action: str, result: str, **metadata):
        """Log an action to the audit trail."""
        from .enforce import create_audit_entry

        entry = create_audit_entry(
            action=action,
            envelope=self.envelope,
            public_key=self.public_key_hex,
            result=result,
            **metadata,
        )
        self.store.save_audit_entry(entry)

    def run(self):
        """Run the interactive shell."""
        console.print()
        console.print(Panel(
            "[bold]Carryall Shell[/bold]\n"
            "Policy-enforced access to your data.\n"
            "Type [bold]help[/bold] for commands, [bold]exit[/bold] to quit.",
            border_style="blue",
        ))
        console.print()

        # Setup readline for history
        try:
            readline.read_history_file(str(Path("~/.carryall/shell_history").expanduser()))
        except FileNotFoundError:
            pass

        while True:
            try:
                prompt = f"[bold cyan]{self.agent_id}[/bold cyan]> "
                line = console.input(prompt).strip()

                if not line:
                    continue

                # Save history
                readline.add_history(line)

                # Parse command
                try:
                    parts = shlex.split(line)
                except ValueError:
                    parts = line.split()

                cmd = parts[0].lower()
                args = parts[1:]

                if cmd in ("exit", "quit", "q"):
                    break
                elif cmd == "help":
                    self._cmd_help()
                elif cmd == "whoami":
                    self._cmd_whoami()
                elif cmd == "scopes":
                    self._cmd_scopes()
                elif cmd == "vaults":
                    self._cmd_vaults()
                elif cmd == "list":
                    self._cmd_list(args)
                elif cmd == "read":
                    self._cmd_read(args)
                elif cmd == "metadata":
                    self._cmd_metadata(args)
                elif cmd == "check":
                    self._cmd_check(args)
                elif cmd == "switch":
                    self._cmd_switch(args)
                elif cmd == "audit":
                    self._cmd_audit(args)
                elif cmd.startswith("/"):
                    # Shorthand commands
                    if cmd == "/a" or cmd == "/audit":
                        self._cmd_audit(args)
                    elif cmd == "/s" or cmd == "/switch":
                        self._cmd_switch(args)
                    elif cmd == "/v" or cmd == "/vaults":
                        self._cmd_vaults()
                    else:
                        console.print(f"[red]Unknown command: {cmd}[/red]")
                else:
                    console.print(f"[red]Unknown command: {cmd}[/red]")
                    console.print("Type [bold]help[/bold] for available commands.")

            except KeyboardInterrupt:
                console.print()
                continue
            except EOFError:
                break

        # Save history on exit
        history_file = Path("~/.carryall/shell_history").expanduser()
        history_file.parent.mkdir(parents=True, exist_ok=True)
        readline.write_history_file(str(history_file))

        console.print("[dim]Session ended.[/dim]")

    def _cmd_help(self):
        """Show help."""
        table = Table(title="Commands", show_header=True, header_style="bold")
        table.add_column("Command", style="cyan")
        table.add_column("Description")

        commands = [
            ("vaults", "List available vaults"),
            ("list <vault>", "List documents in a vault"),
            ("read <uri>", "Read a document (policy-enforced)"),
            ("metadata <uri>", "Get document metadata"),
            ("check <action> <uri>", "Check access without acting"),
            ("switch <agent-id>", "Switch to a different agent"),
            ("scopes", "Show current agent's scopes"),
            ("whoami", "Show current agent and credential"),
            ("audit [--since 1h]", "Show recent audit trail"),
            ("help", "Show this help"),
            ("exit", "Exit the shell"),
        ]

        for cmd, desc in commands:
            table.add_row(cmd, desc)

        console.print(table)

    def _cmd_whoami(self):
        """Show current agent info."""
        console.print(f"[bold]Agent:[/bold] {self.agent_id}")
        console.print(f"[bold]Envelope:[/bold] {self.envelope.envelope_id}")
        console.print(f"[bold]Expires:[/bold] {self.envelope.expires_at}")
        console.print("[bold]Scopes:[/bold]")
        for scope in self.envelope.authority.scopes:
            console.print(f"  - {scope}")

    def _cmd_scopes(self):
        """Show current scopes."""
        for scope in self.envelope.authority.scopes:
            console.print(f"  {scope}")

    def _cmd_vaults(self):
        """List available vaults."""
        try:
            vaults = self.slos.list_vaults(self.agent_id, mock=self.mock)
            console.print("[bold]Vaults:[/bold]")
            for vault in vaults:
                console.print(f"  {vault}")
            self._log_action("list_vaults", "success")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            self._log_action("list_vaults", "error", error=str(e))

    def _cmd_list(self, args: list[str]):
        """List documents in a vault."""
        if not args:
            console.print("[red]Usage: list <vault>[/red]")
            return

        vault = args[0]
        try:
            docs = self.slos.list_resources(vault, self.agent_id, mock=self.mock)
            if docs:
                table = Table(title=f"Documents in {vault}")
                table.add_column("ID", style="cyan")
                table.add_column("Title")

                for doc in docs:
                    table.add_row(doc.get("id", ""), doc.get("title", ""))

                console.print(table)
            else:
                console.print(f"[dim]No documents found in {vault}[/dim]")

            self._log_action("list_vault", "success", vault=vault)
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            self._log_action("list_vault", "error", vault=vault, error=str(e))

    def _cmd_read(self, args: list[str]):
        """Read a document with policy enforcement."""
        if not args:
            console.print("[red]Usage: read <slos://vaults/vault/doc-id>[/red]")
            return

        uri = args[0]

        # Check access first
        result = self.slos.check_access(self.envelope, "read", uri, mock=self.mock)

        if result.decision == Decision.DENY:
            console.print(f"[red]DENY[/red] {result.reason}")
            self._log_action("read", "blocked", resource=uri, reason=result.reason)
            return

        if result.decision == Decision.REQUIRE_APPROVAL:
            console.print(f"[yellow]REQUIRE_APPROVAL[/yellow] {result.reason}")
            self._log_action("read", "pending_approval", resource=uri, reason=result.reason)
            return

        # Access allowed
        console.print(f"[green]ALLOW[/green] {result.reason}")
        console.print()

        if self.mock:
            # Show mock content
            from .backends.slos import parse_slos_uri
            vault, doc_id = parse_slos_uri(uri)
            console.print(f"[dim]--- Mock content for {vault}/{doc_id} ---[/dim]")
            console.print(f"Document: {doc_id}")
            console.print(f"Vault: {vault}")
            console.print("[dim]--- (Connect real SLOS for actual content) ---[/dim]")
        else:
            console.print("[dim]Real SLOS read not yet connected[/dim]")

        self._log_action("read", "success", resource=uri)

    def _cmd_metadata(self, args: list[str]):
        """Get document metadata."""
        if not args:
            console.print("[red]Usage: metadata <slos://vaults/vault/doc-id>[/red]")
            return

        uri = args[0]

        # Check access
        result = self.slos.check_access(self.envelope, "read", uri, mock=self.mock)

        if result.decision == Decision.DENY:
            console.print(f"[red]DENY[/red] {result.reason}")
            self._log_action("get_metadata", "blocked", resource=uri, reason=result.reason)
            return

        try:
            metadata = self.slos.get_metadata(uri, self.agent_id, mock=self.mock)
            console.print(f"[bold]URI:[/bold] {metadata.uri}")
            console.print(f"[bold]ID:[/bold] {metadata.id}")
            console.print(f"[bold]Domain:[/bold] {', '.join(metadata.domain)}")
            console.print(f"[bold]Sensitivity:[/bold] {metadata.sensitivity}")
            console.print(f"[bold]Allowed Agents:[/bold] {', '.join(metadata.allowed_agents) or 'any'}")
            console.print(f"[bold]Denied Agents:[/bold] {', '.join(metadata.denied_agents) or 'none'}")
            console.print(f"[bold]Requires Approval:[/bold] {', '.join(metadata.requires_approval) or 'none'}")
            self._log_action("get_metadata", "success", resource=uri)
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            self._log_action("get_metadata", "error", resource=uri, error=str(e))

    def _cmd_check(self, args: list[str]):
        """Check access without acting."""
        if len(args) < 2:
            console.print("[red]Usage: check <action> <slos://vaults/vault/doc-id>[/red]")
            return

        action, uri = args[0], args[1]
        result = self.slos.check_access(self.envelope, action, uri, mock=self.mock)

        if result.decision == Decision.ALLOW:
            console.print(f"[green]ALLOW[/green] {result.reason}")
        elif result.decision == Decision.REQUIRE_APPROVAL:
            console.print(f"[yellow]REQUIRE_APPROVAL[/yellow] {result.reason}")
        else:
            console.print(f"[red]DENY[/red] {result.reason}")

        # Show metadata
        for key, value in result.metadata.items():
            console.print(f"  [dim]{key}: {value}[/dim]")

        self._log_action(f"check_access:{action}", result.decision.value, resource=uri)

    def _cmd_switch(self, args: list[str]):
        """Switch to a different agent."""
        if not args:
            console.print("[red]Usage: switch <agent-id>[/red]")

            # Show available agents
            agents = self.key_store.list_agents()
            if agents:
                console.print("\nAvailable agents:")
                for agent in agents:
                    marker = " [green]<-- current[/green]" if agent == self.agent_id else ""
                    console.print(f"  {agent}{marker}")
            return

        self._switch_agent(args[0])

    def _cmd_audit(self, args: list[str]):
        """Show recent audit trail."""
        # Parse --since flag
        limit = 20
        since = None

        i = 0
        while i < len(args):
            if args[i] == "--since" and i + 1 < len(args):
                since = args[i + 1]
                i += 2
            elif args[i] == "-n" and i + 1 < len(args):
                limit = int(args[i + 1])
                i += 2
            else:
                i += 1

        # Parse since duration
        start_time = None
        if since:
            now = datetime.now(timezone.utc)
            since = since.strip().lower()
            if since.endswith("m"):
                start_time = (now - timedelta(minutes=int(since[:-1]))).isoformat()
            elif since.endswith("h"):
                start_time = (now - timedelta(hours=int(since[:-1]))).isoformat()
            elif since.endswith("d"):
                start_time = (now - timedelta(days=int(since[:-1]))).isoformat()

        entries = self.store.get_audit_trail(
            agent_id=None,  # Show all agents
            start_time=start_time,
            limit=limit,
        )

        if not entries:
            console.print("[dim]No audit entries found.[/dim]")
            return

        table = Table(title="Audit Trail")
        table.add_column("Time", style="dim")
        table.add_column("Agent", style="cyan")
        table.add_column("Action")
        table.add_column("Result")

        for entry in entries:
            result = entry["result"]
            if result == "success":
                style = "green"
            elif result == "blocked":
                style = "red"
            else:
                style = "yellow"

            table.add_row(
                entry["timestamp"][11:19],  # Just the time portion
                entry["agent_id"],
                entry["action"],
                f"[{style}]{result}[/{style}]",
            )

        console.print(table)
