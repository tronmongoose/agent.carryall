"""`carryall rules` CLI: the enforcement point for the shipped hard rules.

Exports: rules_app.

Intended as a hook target: a shell or agent harness runs
`carryall rules check --command "$CMD"` before executing, and refuses on exit 1.
Exit codes: 0 permitted, 1 a rule fired, 2 unusable input.
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .rule_packs import RuleViolation, builtin_pack
from .rule_packs.builtin import EXEC_POINT, READ_POINT, WRITE_POINT

rules_app = typer.Typer(help="Hard rules: list them, or check one command or path")
console = Console()


@rules_app.command("list")
def list_rules() -> None:
    """Show every shipped rule, its number, and where it is enforced."""
    pack = builtin_pack()
    table = Table(title="Builtin hard rules")
    for col in ("#", "id", "enforced at", "description"):
        table.add_column(col)
    for rule in sorted(pack.rules, key=lambda r: r.number or 0):
        table.add_row(str(rule.number), rule.id, ", ".join(rule.enforcement), rule.description)
    console.print(table)


@rules_app.command("check")
def check(
    command: Optional[str] = typer.Option(None, "--command", help="Command about to run"),
    read: Optional[str] = typer.Option(None, "--read", help="Path about to be read"),
    write: Optional[str] = typer.Option(None, "--write", help="Path about to be written"),
) -> None:
    """Check one subject against the rules. Exit 1 if a rule fires."""
    subjects = [(EXEC_POINT, "command", command), (READ_POINT, "path", read),
                (WRITE_POINT, "path", write)]
    given = [(point, key, value) for point, key, value in subjects if value is not None]
    if len(given) != 1:
        console.print("[red]pass exactly one of --command, --read, --write[/red]")
        raise typer.Exit(2)
    point, key, value = given[0]
    try:
        builtin_pack().enforce_point(point, {key: value})
    except RuleViolation as v:
        console.print(f"[red]DENY[/red] rule #{v.rule_number} ({v.rule_id}) at {point}: "
                      f"{v.message}")
        console.print(f"  [dim]{v.description}[/dim]")
        raise typer.Exit(1)
    console.print(f"[green]ALLOW[/green] {point}: no rule fired")
