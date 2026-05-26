from __future__ import annotations

import json
import time

import click
from rich.console import Console
from rich.table import Table

from chayuan_supervisor import SupervisorManager, get_runtime_info

console = Console()


def _print_endpoints(mgr: SupervisorManager) -> None:
    eps = mgr.endpoints()
    if not eps:
        console.print("[dim]no endpoints exposed[/]")
        return
    table = Table(title="Service endpoints", show_lines=False)
    for col in ("service", "kind", "address", "user", "password", "url"):
        table.add_column(col, overflow="fold")
    for name, ep in sorted(eps.items()):
        host = ep.get("host", "")
        port = ep.get("port", "")
        addr = f"{host}:{port}" if port else host
        table.add_row(
            name,
            str(ep.get("scheme") or ep.get("kind") or ""),
            addr,
            str(ep.get("user", "") or ""),
            str(ep.get("password", "") or ""),
            str(ep.get("url", "") or ""),
        )
    console.print(table)
    console.print("[dim]These endpoints are persisted to "
                  "<CHAYUAN_HOME>/data/runtime.json (chmod 600).[/]")


@click.group(help="Manage subprocesses (postgres / redis / ollama / ...).")
def service() -> None:
    pass


@service.command("start")
@click.option("--dry-run", is_flag=True)
@click.option("--only", multiple=True)
@click.option("--foreground", is_flag=True)
@click.option("--quiet", is_flag=True, help="suppress endpoint table after start")
def start_cmd(dry_run, only, foreground, quiet) -> None:
    mgr = SupervisorManager()
    mgr.up(dry_run=dry_run, only=list(only) or None)
    if not quiet:
        _print_endpoints(mgr)
    if foreground and not dry_run:
        console.print("[bold]Press Ctrl-C to stop all services.[/]")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            mgr.down()


@service.command("stop")
def stop_cmd() -> None:
    mgr = SupervisorManager()
    mgr.plan()
    mgr.down()


@service.command("restart")
@click.argument("name", required=False)
def restart_cmd(name) -> None:
    mgr = SupervisorManager()
    mgr.plan()
    mgr.up(only=[name] if name else None)


@service.command("status")
@click.option("--json", "as_json", is_flag=True)
def status_cmd(as_json) -> None:
    mgr = SupervisorManager()
    mgr.plan()
    st = mgr.status()
    if as_json:
        click.echo(json.dumps(st, indent=2))
        return
    table = Table(title="Services")
    for col in ("name", "state", "pid", "uptime_sec", "attempt"):
        table.add_column(col)
    for s in st:
        table.add_row(s["name"], s["state"], str(s.get("pid") or "-"),
                      str(int(s["uptime_sec"])), str(s["attempt"]))
    console.print(table)


@service.command("logs")
@click.argument("name")
@click.option("--tail", type=int, default=200)
def logs_cmd(name, tail) -> None:
    mgr = SupervisorManager()
    mgr.plan()
    for line in mgr.logs(name, tail=tail):
        click.echo(line)


@service.command("plan")
def plan_cmd() -> None:
    mgr = SupervisorManager()
    procs = mgr.plan()
    out = {
        "graph": mgr.graph(),
        "ports": mgr.ports(),
        "endpoints": mgr.endpoints(),
        "processes": [{"name": p.name, "binary": p.binary, "args": p.args} for p in procs],
    }
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))


@service.command("info", help="Print final addresses, ports, users and passwords.")
@click.option("--json", "as_json", is_flag=True)
@click.option("--reveal-passwords/--mask-passwords", default=True,
              help="Toggle masking for screen sharing.")
def info_cmd(as_json, reveal_passwords) -> None:
    info = get_runtime_info()
    eps = info.all_endpoints()
    if not eps:
        # Service hasn't been started yet — at least show what *would* happen.
        mgr = SupervisorManager()
        mgr.plan()
        eps = mgr.endpoints()
    if not reveal_passwords:
        masked: dict[str, dict] = {}
        for k, v in eps.items():
            pwd = v.get("password")
            if pwd:
                copy = dict(v)
                copy["url"] = (v.get("url") or "").replace(pwd, "****")
                copy["password"] = "****"
                masked[k] = copy
            else:
                masked[k] = v
        eps = masked
    if as_json:
        click.echo(json.dumps(eps, indent=2, ensure_ascii=False))
        return
    table = Table(title="Service endpoints (from runtime.json)")
    for col in ("service", "kind", "address", "user", "password", "url"):
        table.add_column(col, overflow="fold")
    for name, ep in sorted(eps.items()):
        host = ep.get("host", "")
        port = ep.get("port", "")
        addr = f"{host}:{port}" if port else host
        table.add_row(name, str(ep.get("scheme") or ep.get("kind") or ""),
                      addr, str(ep.get("user", "")),
                      str(ep.get("password", "")),
                      str(ep.get("url", "")))
    console.print(table)
