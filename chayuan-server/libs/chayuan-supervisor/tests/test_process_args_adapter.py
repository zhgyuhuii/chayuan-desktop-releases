"""``chayuan_supervisor.process_args_adapter`` 行为测试 + 与 manager 的集成。

测试覆盖:
* resolve_extra_for 在 chayuan-server 不在场 / 解析失败 / 未知进程时返回空;
* resolve_extra_for 命中时返回正确的 (args, env);
* SupervisorManager.plan() 把解析到的 args 追加到 ProcessSpec.args 之后,
  env 用 setdefault 不覆盖 yaml 原值。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml

from chayuan_supervisor import SupervisorManager, load_spec
from chayuan_supervisor.credentials import reset_for_tests
from chayuan_supervisor.process_args_adapter import resolve_extra_for


# ───────────────────────── adapter 单元 ─────────────────────────


def test_adapter_returns_empty_when_chayuan_server_not_importable():
    """import 失败时静默返回空,不抛。"""
    # 模拟 chayuan.server.model_registry.process_args import 失败
    with mock.patch.dict(sys.modules, {"chayuan.server.model_registry.process_args": None}):
        # mock.patch.dict 把 None 当作"已 import 但坏掉"的语义,实际触发 ImportError
        args, env = resolve_extra_for("llamacpp")
    assert args == []
    assert env == {}


def test_adapter_returns_empty_when_resolve_all_raises():
    """resolve_all 抛异常时不应阻塞 supervisor —— 静默返回空。"""
    fake = mock.MagicMock(side_effect=RuntimeError("boom"))
    fake_module = mock.MagicMock()
    fake_module.resolve_all = fake
    with mock.patch.dict(sys.modules, {
        "chayuan.server.model_registry.process_args": fake_module,
    }):
        args, env = resolve_extra_for("llamacpp")
    assert args == []
    assert env == {}


def test_adapter_returns_empty_for_unknown_process_name():
    """resolve_all 没返回该进程时 → 空 args/env。"""
    class _FakeRes:
        args = ["--model", "/m/q.gguf"]
        env: dict = {}
        missing: list = []
        resolved_models: dict = {}
        reason = "ok"

    fake_module = mock.MagicMock()
    fake_module.resolve_all = lambda: {"llamacpp": _FakeRes()}
    with mock.patch.dict(sys.modules, {
        "chayuan.server.model_registry.process_args": fake_module,
    }):
        args, env = resolve_extra_for("unknown-process")
    assert args == []
    assert env == {}


def test_adapter_passes_through_known_process_args():
    class _FakeRes:
        args = ["--model", "/m/q.gguf", "--ctx-size", "4096"]
        env = {"FOO": "bar"}
        missing: list = []
        resolved_models = {"chat": "qwen3-4b"}
        reason = "ok"

    fake_module = mock.MagicMock()
    fake_module.resolve_all = lambda: {"llamacpp": _FakeRes()}
    with mock.patch.dict(sys.modules, {
        "chayuan.server.model_registry.process_args": fake_module,
    }):
        args, env = resolve_extra_for("llamacpp")
    assert args == ["--model", "/m/q.gguf", "--ctx-size", "4096"]
    assert env == {"FOO": "bar"}


# ───────────────────────── manager 集成 ─────────────────────────


def test_manager_appends_resolved_args_to_spec(tmp_path: Path):
    """SupervisorManager.plan() 应把 resolve_extra_for 给的 args 追加在 spec.args 之后。"""
    reset_for_tests(tmp_path / "rt.json")
    spec = {
        "processes": [
            {
                "name": "llamacpp",
                "binary": "/bin/true",
                "args": ["--host", "127.0.0.1", "--port", "${LLAMACPP_PORT}"],
                "port": "LLAMACPP_PORT",
                "preferred_port": 38086,
                "credentials": {"no_auth": True},
            }
        ]
    }
    f = tmp_path / "supervisor.yaml"
    f.write_text(yaml.safe_dump(spec), encoding="utf-8")
    specs = load_spec(f)

    with mock.patch(
        "chayuan_supervisor.manager.resolve_extra_for",
        return_value=(["--model", "/m/q.gguf", "--ctx-size", "4096"], {"GGML_BACKEND": "cpu"}),
    ):
        mgr = SupervisorManager(specs=specs)
        procs = mgr.plan()

    p = procs[0]
    # 原有 args 保留,新 args 追加在尾部
    assert "--host" in p.args
    assert "--port" in p.args
    assert "--model" in p.args
    assert "/m/q.gguf" in p.args
    # 顺序:--model 必须在 yaml 原 args 之后
    assert p.args.index("--model") > p.args.index("--host")
    # env 追加
    assert p.env.get("GGML_BACKEND") == "cpu"


def test_manager_yaml_env_wins_on_conflict(tmp_path: Path):
    """yaml 原 env 与解析 env 冲突时,yaml 优先(env.setdefault 语义)。"""
    reset_for_tests(tmp_path / "rt.json")
    spec = {
        "processes": [
            {
                "name": "llamacpp",
                "binary": "/bin/true",
                "args": [],
                "env": {"GGML_BACKEND": "metal"},  # yaml 已经指定
                "credentials": {"no_auth": True},
            }
        ]
    }
    f = tmp_path / "supervisor.yaml"
    f.write_text(yaml.safe_dump(spec), encoding="utf-8")
    specs = load_spec(f)

    with mock.patch(
        "chayuan_supervisor.manager.resolve_extra_for",
        return_value=([], {"GGML_BACKEND": "cpu"}),  # 解析说 cpu
    ):
        mgr = SupervisorManager(specs=specs)
        procs = mgr.plan()

    # yaml 写的 metal 应该胜出
    assert procs[0].env["GGML_BACKEND"] == "metal"


def test_manager_falls_through_when_adapter_returns_empty(tmp_path: Path):
    """adapter 返回 ([], {}) 时,manager 行为应与未启用此功能时完全一致。"""
    reset_for_tests(tmp_path / "rt.json")
    spec = {
        "processes": [
            {
                "name": "llamacpp",
                "binary": "/bin/true",
                "args": ["--host", "127.0.0.1"],
                "credentials": {"no_auth": True},
            }
        ]
    }
    f = tmp_path / "supervisor.yaml"
    f.write_text(yaml.safe_dump(spec), encoding="utf-8")
    specs = load_spec(f)

    with mock.patch(
        "chayuan_supervisor.manager.resolve_extra_for",
        return_value=([], {}),
    ):
        mgr = SupervisorManager(specs=specs)
        procs = mgr.plan()

    p = procs[0]
    # 只剩 yaml 原 args
    assert p.args == ["--host", "127.0.0.1"]


def test_manager_only_appends_for_listed_processes(tmp_path: Path):
    """非推理引擎进程不受影响(adapter 对未列出名字返回空)。"""
    reset_for_tests(tmp_path / "rt.json")
    spec = {
        "processes": [
            {
                "name": "postgres",
                "binary": "/bin/true",
                "args": ["-D", "data/pg"],
                "credentials": {"no_auth": True},
            },
            {
                "name": "llamacpp",
                "binary": "/bin/true",
                "args": ["--host", "127.0.0.1"],
                "credentials": {"no_auth": True},
            },
        ]
    }
    f = tmp_path / "supervisor.yaml"
    f.write_text(yaml.safe_dump(spec), encoding="utf-8")
    specs = load_spec(f)

    def _stub(process_name: str):
        if process_name == "llamacpp":
            return (["--model", "/m/q.gguf"], {})
        return ([], {})

    with mock.patch(
        "chayuan_supervisor.manager.resolve_extra_for",
        side_effect=_stub,
    ):
        mgr = SupervisorManager(specs=specs)
        procs = mgr.plan()

    by_name = {p.name: p for p in procs}
    # postgres 不动
    assert by_name["postgres"].args == ["-D", "data/pg"]
    # llamacpp 追加
    assert "--model" in by_name["llamacpp"].args
