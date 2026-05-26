"""``chayuan model status`` 子命令测试 — 用 CliRunner 跑 click 命令。"""
from __future__ import annotations

import json
from unittest import mock

import pytest
from click.testing import CliRunner

from chayuan.cli_service import model_status_cmd
from chayuan.server.model_registry.bootstrap import (
    BootstrapReport,
    CapabilityStatus,
)
from chayuan.server.model_registry.local_index import LocalModelEntry
from chayuan.server.model_registry.process_args import Resolution


def _entry(model_id: str, capability: str, fmt: str = "gguf",
           path: str = "") -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id,
        path=path or f"/tmp/fake/{model_id}",
        relpath=model_id,
        capability=capability,
        format=fmt,
        family=capability.replace("-", "_"),
        size_bytes=2_500_000_000,
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ─────────────────────────── 文本输出 ───────────────────────────


def test_status_prints_ok_when_ready(runner):
    report = BootstrapReport(
        ready=True,
        missing=[],
        statuses=[
            CapabilityStatus(capability="chat", satisfied=True,
                             candidates=[_entry("qwen3-4b", "chat")]),
            CapabilityStatus(capability="text-embedding", satisfied=True,
                             candidates=[_entry("bge-m3", "text-embedding",
                                                fmt="hf_transformers")]),
            CapabilityStatus(capability="rerank", satisfied=True,
                             candidates=[_entry("bge-rerank", "rerank")]),
        ],
    )
    snap = {
        "llamacpp": Resolution(process="llamacpp", args=["--model", "/m/q.gguf"],
                               resolved_models={"chat": "qwen3-4b"}),
        "infinity": Resolution(process="infinity", args=["--model-id", "/m/x"]),
        "ollama":   Resolution(process="ollama",
                               env={"OLLAMA_MODELS": "/m/_ollama"}),
    }
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.check_bootstrap",
        return_value=report,
    ), mock.patch(
        "chayuan.server.model_registry.process_args.resolve_all",
        return_value=snap,
    ):
        r = runner.invoke(model_status_cmd, [])
    assert r.exit_code == 0
    assert "模型库自检通过" in r.output
    assert "chat" in r.output
    assert "llamacpp" in r.output
    assert "--model" in r.output


def test_status_prints_missing_and_hints_when_not_ready(runner):
    report = BootstrapReport(
        ready=False,
        missing=["text-embedding", "rerank"],
        statuses=[
            CapabilityStatus(capability="chat", satisfied=True,
                             candidates=[_entry("qwen3-4b", "chat")]),
            CapabilityStatus(capability="text-embedding", satisfied=False),
            CapabilityStatus(capability="rerank", satisfied=False),
        ],
    )
    snap = {
        "llamacpp": Resolution(process="llamacpp", args=["--model", "/m/q.gguf"]),
        "infinity": Resolution(process="infinity",
                               missing=["embedding", "rerank"]),
        "ollama":   Resolution(process="ollama",
                               env={"OLLAMA_MODELS": "/m/_ollama"}),
    }
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.check_bootstrap",
        return_value=report,
    ), mock.patch(
        "chayuan.server.model_registry.process_args.resolve_all",
        return_value=snap,
    ):
        r = runner.invoke(model_status_cmd, [])
    assert r.exit_code == 0
    assert "缺" in r.output
    assert "text-embedding" in r.output and "rerank" in r.output
    # install_hints 推荐 lite
    assert "lite" in r.output
    # missing 在 infinity 处显示
    assert "missing=" in r.output


# ─────────────────────────── JSON 输出 ───────────────────────────


def test_status_json_output(runner):
    report = BootstrapReport(
        ready=True,
        missing=[],
        statuses=[
            CapabilityStatus(capability="chat", satisfied=True,
                             candidates=[_entry("qwen3-4b", "chat")]),
        ],
    )
    snap = {
        "llamacpp": Resolution(process="llamacpp", args=["--model", "/m/q.gguf"]),
    }
    with mock.patch(
        "chayuan.server.model_registry.bootstrap.check_bootstrap",
        return_value=report,
    ), mock.patch(
        "chayuan.server.model_registry.process_args.resolve_all",
        return_value=snap,
    ):
        r = runner.invoke(model_status_cmd, ["--json"])
    assert r.exit_code == 0
    parsed = json.loads(r.output)
    assert parsed["bootstrap"]["ready"] is True
    assert parsed["install_hints"] == []
    assert "llamacpp" in parsed["process_args"]
    assert parsed["process_args"]["llamacpp"]["args"] == ["--model", "/m/q.gguf"]


def test_status_no_scan_flag_threads_through(runner):
    """--no-scan 参数应该让 check_bootstrap 收到 do_scan=False。"""
    called: dict = {}

    def _capture_check(*, required=None, do_scan=True):
        called["do_scan"] = do_scan
        return BootstrapReport(ready=True, missing=[], statuses=[])

    with mock.patch(
        "chayuan.server.model_registry.bootstrap.check_bootstrap",
        side_effect=_capture_check,
    ), mock.patch(
        "chayuan.server.model_registry.process_args.resolve_all",
        return_value={},
    ):
        r = runner.invoke(model_status_cmd, ["--no-scan"])
    assert r.exit_code == 0
    assert called["do_scan"] is False
