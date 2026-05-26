from __future__ import annotations

import json

import click
from rich.console import Console
from rich.table import Table

from chayuan_preflight import run_all

console = Console()


@click.command(help="Run pre-flight checks (OS, AV, ports, GPU, ...).")
@click.option("--json", "as_json", is_flag=True)
@click.option("--fix", is_flag=True, help="Print suggested fix commands.")
def doctor(as_json, fix) -> None:
    report = run_all()
    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return
    table = Table(title=f"Doctor — {report.fatal_count} fatal / {report.warn_count} warn / {report.ok_count} ok")
    for col in ("severity", "name", "detail"):
        table.add_column(col)
    style = {"fatal": "red", "warn": "yellow", "ok": "green"}
    for c in report.checks:
        table.add_row(f"[{style.get(c.severity, '')}]{c.severity}[/]", c.name, c.detail)
    console.print(table)
    if fix:
        for c in report.checks:
            if c.fix:
                console.print(f"[bold]{c.name}[/]: {c.fix}")
