"""`chayuan-pack build --target=... --release=...`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from chayuan_packager.bundle import bundle
from chayuan_packager.filter import filter_manifest
from chayuan_packager.scan import scan
from chayuan_packager.verify import verify_manifest


@click.group()
def cli() -> None:
    pass


@cli.command("scan")
@click.option("--workspace", default=".", help="workspace root")
def scan_cmd(workspace) -> None:
    m = scan(workspace)
    click.echo(m.to_json())


@cli.command("plan")
@click.option("--workspace", default=".")
@click.option("--release", default="standard", type=click.Choice(["lite", "standard", "pro"]))
def plan_cmd(workspace, release) -> None:
    m = filter_manifest(scan(workspace), release)
    click.echo(m.to_json())


@cli.command("build")
@click.option("--workspace", default=".")
@click.option("--target", default=sys.platform.replace("darwin", "mac").replace("win32", "win"),
              type=click.Choice(["win", "mac", "linux"]))
@click.option("--release", default="standard", type=click.Choice(["lite", "standard", "pro"]))
@click.option("--version", default="0.1.0")
@click.option("--out", default="dist", help="output directory")
@click.option("--require-license", is_flag=True)
@click.option("--dry-run", is_flag=True)
def build_cmd(workspace, target, release, version, out, require_license, dry_run) -> None:
    workspace = Path(workspace).resolve()
    raw = scan(workspace)
    filtered = filter_manifest(raw, release)
    ok, problems = verify_manifest(filtered, require_license=require_license)
    if not ok:
        click.echo(json.dumps({"error": "verify failed", "problems": problems}, indent=2), err=True)
        sys.exit(2)
    res = bundle(filtered, target=target, release=release, version=version,
                 out_dir=Path(out).resolve(), dry_run=dry_run)
    click.echo(json.dumps({
        "archive": str(res.archive),
        "manifest": str(res.manifest_path),
        "size_bytes": res.size_bytes,
        "components": len(filtered.components),
        "dry_run": dry_run,
    }, indent=2))


def main() -> None:
    cli()


if __name__ == "__main__":  # pragma: no cover
    main()
