"""`carryall models` CLI: inspect the allowlist, check one model, backtest history.

Exports: models_app.

Exit codes: 0 allowed / no denials, 1 refused / denials with --fail-on-deny,
2 unusable input (bad allowlist file, unreadable log, bad route mapping).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import typer
from rich.console import Console
from rich.table import Table

from .models import ModelPolicy, ModelPolicyError
from .models.backtest import BacktestReport, records_from_audit, records_from_usage_jsonl, replay
from .storage import EnvelopeStore

models_app = typer.Typer(help="Model allowlist: list, check, backtest")
console = Console()

ALLOWLIST_OPT = typer.Option(None, "--allowlist", help="Local additions JSON (overrides env)")


def _policy(allowlist: Optional[Path]) -> ModelPolicy:
    """Load the policy or exit 2 with the refusal reason."""
    try:
        return ModelPolicy.load(allowlist)
    except ModelPolicyError as e:
        console.print(f"[red]allowlist unusable:[/red] {e}")
        raise typer.Exit(2)


@models_app.command("list")
def list_models(allowlist: Optional[Path] = ALLOWLIST_OPT) -> None:
    """Show every allowlisted (provider, model ID) and the policy version."""
    policy = _policy(allowlist)
    table = Table(title=f"Model allowlist {policy.version}")
    for col in ("provider", "model_id", "origin", "status"):
        table.add_column(col)
    for e in policy.list_entries():
        table.add_row(e.provider, e.model_id, e.origin, e.status)
    console.print(table)


@models_app.command("check")
def check_model(provider: str, model_id: str, allowlist: Optional[Path] = ALLOWLIST_OPT) -> None:
    """Resolve one model exactly as runtime call sites do. Exit 1 if refused."""
    policy = _policy(allowlist)
    try:
        r = policy.resolve(provider, model_id)
    except ModelPolicyError as e:
        console.print(f"[red]DENY[/red] {provider}/{model_id}: {e.reason} ({e.detail})")
        raise typer.Exit(1)
    console.print(f"[green]ALLOW[/green] {r.provider}/{r.model_id} origin={r.origin} "
                  f"locality={r.locality} status={r.status} policy={r.policy_version}")


def _route_map(pairs: List[str]) -> Dict[str, str]:
    """Parse repeated route=provider options; defaults match clawrouter."""
    mapping = {"local": "ollama", "frontier": "anthropic"}
    for pair in pairs:
        route, sep, provider = pair.partition("=")
        if not sep or not route or not provider:
            console.print(f"[red]bad --route {pair!r}; expected route=provider[/red]")
            raise typer.Exit(2)
        mapping[route] = provider
    return mapping


@models_app.command("backtest")
def backtest(
    usage: Optional[Path] = typer.Option(None, "--usage", help="Usage JSONL (clawrouter/router)"),
    audit_db: Optional[Path] = typer.Option(None, "--audit-db", help="Audit SQLite database"),
    route: List[str] = typer.Option([], "--route", help="route=provider mapping for --usage"),
    allowlist: Optional[Path] = ALLOWLIST_OPT,
    as_json: bool = typer.Option(False, "--json", help="Emit the report as JSON"),
    fail_on_deny: bool = typer.Option(False, "--fail-on-deny", help="Exit 1 if anything denies"),
) -> None:
    """Replay past model choices against the current (or given) allowlist. Read-only."""
    if (usage is None) == (audit_db is None):
        console.print("[red]pass exactly one of --usage or --audit-db[/red]")
        raise typer.Exit(2)
    policy = _policy(allowlist)
    skipped = 0
    try:
        if usage is not None:
            records, skipped = records_from_usage_jsonl(
                usage.read_text(encoding="utf-8").splitlines(), _route_map(route))
        else:
            assert audit_db is not None
            if not audit_db.exists():
                raise OSError(f"{audit_db} does not exist")
            rows = EnvelopeStore(os.fspath(audit_db)).get_audit_trail(limit=10_000_000)
            records = list(records_from_audit(rows))
    except OSError as e:
        console.print(f"[red]cannot read input:[/red] {e}")
        raise typer.Exit(2)
    report = replay(records, policy)
    _emit(report, skipped, as_json)
    if fail_on_deny and report.denied:
        raise typer.Exit(1)


def _emit(report: BacktestReport, skipped: int, as_json: bool) -> None:
    """Print the report as JSON or a short human summary."""
    data = report.to_dict()
    data["skipped"] = skipped
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    console.print(f"policy {report.policy_version}: {report.total} replayed, "
                  f"{report.allowed} allow, {report.denied} deny, {skipped} skipped")
    for reason, n in data["by_reason"].items():
        console.print(f"  deny {reason}: {n}")
    for flip in report.flips[:20]:
        console.print(f"  flip {flip['ref']} {flip['model']}: {flip['was']} -> {flip['now']}")
