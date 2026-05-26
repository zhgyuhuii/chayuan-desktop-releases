"""Click-based CLI entry. `chayuan --help`."""
from __future__ import annotations

import json
import sys

import click

from chayuan_cli.commands import doctor, info, model, service


@click.group(help="Chayuan multimodal AI platform CLI.")
@click.version_option(package_name="chayuan-cli")
def cli() -> None:
    pass


cli.add_command(model.model)
cli.add_command(service.service)
cli.add_command(doctor.doctor)
cli.add_command(info.info)


@cli.command(name="version", help="Print package versions.")
def version_cmd() -> None:
    import importlib.metadata as md

    out = {}
    for n in ("chayuan-core", "chayuan-cli", "chayuan-gateway", "chayuan-registry",
              "chayuan-discovery", "chayuan-modelmgr", "chayuan-runtime",
              "chayuan-supervisor", "chayuan-preflight", "chayuan-packager"):
        try:
            out[n] = md.version(n)
        except Exception:
            out[n] = "?"
    click.echo(json.dumps(out, indent=2))


def main() -> None:
    try:
        cli(standalone_mode=True)
    except SystemExit as e:
        raise
    except Exception as e:  # noqa: BLE001
        click.echo(f"error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
