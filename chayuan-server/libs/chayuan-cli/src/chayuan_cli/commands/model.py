from __future__ import annotations

import json

import click
from rich.console import Console
from rich.table import Table

from chayuan_discovery import poll_once
from chayuan_modelmgr import import_model, pull
from chayuan_modelmgr.progress import ProgressEvent
from chayuan_registry import ModelRepository, init_engine, session_scope

console = Console()


@click.group(help="Manage models: download / import / list / remove / switch.")
def model() -> None:
    init_engine()


@model.command("pull", help="Download a model from a HF mirror.")
@click.argument("repo")
@click.option("--category", help="Force category (chat/embedding/...).")
@click.option("--mirror", help="Override mirror (hf-mirror/huggingface/modelscope).")
@click.option("--revision", default=None)
@click.option("--allow", "allow_patterns", multiple=True, help="Glob include filter (repeatable).")
@click.option("--ignore", "ignore_patterns", multiple=True)
def pull_cmd(repo: str, category, mirror, revision, allow_patterns, ignore_patterns) -> None:
    last_pct = -1.0

    def _on(ev: ProgressEvent) -> None:
        nonlocal last_pct
        pct = (ev.bytes_done / ev.bytes_total * 100) if ev.bytes_total else 0
        if abs(pct - last_pct) >= 1.0 or ev.state in ("done", "error"):
            last_pct = pct
            console.print(f"[{ev.state}] {ev.filename or repo} {pct:5.1f}% — {ev.message}",
                          highlight=False)

    res = pull(
        repo, category=category, mirror=mirror, revision=revision,
        allow_patterns=list(allow_patterns) or None,
        ignore_patterns=list(ignore_patterns) or None,
        progress_cb=_on,
    )
    poll_once()
    console.print(f"[bold green]done[/] → {res.dest} ({res.bytes_total} bytes)")


@model.command("import", help="Import a local model directory.")
@click.argument("src", type=click.Path(exists=True, file_okay=False))
@click.option("--repo", help="Override repo id (defaults to source dir name).")
@click.option("--category", help="Override category.")
@click.option("--move", is_flag=True, help="Move instead of copy.")
@click.option("--hardlink", is_flag=True, help="Hard-link files (saves disk).")
def import_cmd(src, repo, category, move, hardlink) -> None:
    dest, meta = import_model(src, repo=repo, category=category, move=move, hardlink=hardlink)
    poll_once()
    console.print(f"[bold green]imported[/] → {dest}")
    if meta:
        console.print(json.dumps(meta.to_payload(), indent=2, ensure_ascii=False))


@model.command("ls", help="List registered models.")
@click.option("--category", default=None)
@click.option("--json", "as_json", is_flag=True)
def ls_cmd(category, as_json) -> None:
    poll_once()
    with session_scope() as s:
        models = ModelRepository(s).list(category=category)
        rows = [m.to_public() for m in models]
    if as_json:
        click.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    table = Table(title="Models")
    for col in ("id", "category", "runtime", "format", "status", "enabled", "default", "size_mb"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r["id"], r["category"], r["runtime"], r["format"], r["status"],
            "yes" if r["enabled"] else "no",
            "yes" if r["is_default"] else "",
            f"{r['size_bytes'] / 1024 / 1024:.1f}",
        )
    console.print(table)


@model.command("rm", help="Hard-remove a model from the registry.")
@click.argument("model_id")
def rm_cmd(model_id) -> None:
    with session_scope() as s:
        if ModelRepository(s).hard_remove(model_id):
            console.print(f"[red]removed[/] {model_id}")
        else:
            click.get_current_context().exit(1)


@model.command("switch", help="Set default model for a category.")
@click.argument("category")
@click.argument("model_id")
def switch_cmd(category, model_id) -> None:
    with session_scope() as s:
        if not ModelRepository(s).set_default(category, model_id):
            click.echo("model/category mismatch", err=True)
            click.get_current_context().exit(1)
    console.print(f"[bold]{category}[/] default → {model_id}")


@model.command("enable", help="Enable a model.")
@click.argument("model_id")
def enable_cmd(model_id) -> None:
    with session_scope() as s:
        ModelRepository(s).set_enabled(model_id, True)


@model.command("disable", help="Disable a model.")
@click.argument("model_id")
def disable_cmd(model_id) -> None:
    with session_scope() as s:
        ModelRepository(s).set_enabled(model_id, False)


@model.command("scan", help="Trigger an immediate filesystem scan.")
def scan_cmd() -> None:
    summary = poll_once()
    console.print(json.dumps(summary, indent=2))
