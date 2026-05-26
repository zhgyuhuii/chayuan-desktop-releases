from __future__ import annotations

import json

import click

from chayuan_core import get_paths, get_platform_info, load_config


@click.command(help="Print platform / paths / config info.")
@click.option("--json", "as_json", is_flag=True)
def info(as_json) -> None:
    out = {
        "platform": get_platform_info().to_dict(),
        "paths": {k: str(v) for k, v in vars(get_paths()).items()},
        "config": load_config().model_dump(),
    }
    click.echo(json.dumps(out, indent=2, ensure_ascii=False))
