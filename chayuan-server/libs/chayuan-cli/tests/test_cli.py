from __future__ import annotations

import json

from click.testing import CliRunner

from chayuan_cli.__main__ import cli
from chayuan_registry.db import reset_for_tests


def test_help_top_level():
    r = CliRunner().invoke(cli, ["--help"])
    assert r.exit_code == 0 and "model" in r.output and "service" in r.output and "doctor" in r.output


def test_info_runs():
    r = CliRunner().invoke(cli, ["info", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert "platform" in payload and "paths" in payload


def test_model_ls_empty(monkeypatch):
    reset_for_tests("sqlite:///:memory:")
    r = CliRunner().invoke(cli, ["model", "ls", "--json"])
    assert r.exit_code == 0


def test_doctor_runs():
    r = CliRunner().invoke(cli, ["doctor", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert "checks" in payload and isinstance(payload["checks"], list)
