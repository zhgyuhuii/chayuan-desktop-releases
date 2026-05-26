"""83 题精确化:_docker_compose_ps 走 docker compose -f <yaml> ps,
只查该服务独立 yaml 内的容器,不被全局子串干扰。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _mock_subprocess_run(stdout: str, returncode: int = 0):
    """构造 subprocess.run 返回。"""
    mock = MagicMock()
    mock.stdout = stdout
    mock.stderr = ""
    mock.returncode = returncode
    return mock


def test_compose_ps_returns_empty_when_yaml_missing(tmp_path, monkeypatch):
    """yaml 不存在 → ("", "");走 fallback 到原 docker ps 逻辑。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.config_panel import compose_manager as cm

    monkeypatch.setattr(
        cm, "get_compose_file_for_service", lambda _s: None,
    )
    state, url = mod._docker_compose_ps("ollama")
    assert state == ""
    assert url == ""


def test_compose_ps_parses_ndjson_running(tmp_path, monkeypatch):
    """docker compose ps 返 ndjson,running 容器被识别。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.config_panel import compose_manager as cm

    yaml_p = tmp_path / "ollama.yaml"
    yaml_p.write_text("services:\n  ollama:\n    image: x\n", encoding="utf-8")
    monkeypatch.setattr(cm, "get_compose_file_for_service", lambda _s: yaml_p)

    fake_stdout = (
        '{"Service":"ollama","State":"running",'
        '"Publishers":[{"URL":"127.0.0.1","PublishedPort":11434,"TargetPort":11434}]}\n'
    )
    with patch("subprocess.run", return_value=_mock_subprocess_run(fake_stdout)):
        state, url = mod._docker_compose_ps("ollama")
    assert state == "running"
    assert url == "http://127.0.0.1:11434"


def test_compose_ps_parses_array_format(tmp_path, monkeypatch):
    """老版 docker compose 输出单 array → 同样能解析。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.config_panel import compose_manager as cm

    yaml_p = tmp_path / "vllm.yaml"
    yaml_p.write_text("services:\n  vllm:\n    image: x\n", encoding="utf-8")
    monkeypatch.setattr(cm, "get_compose_file_for_service", lambda _s: yaml_p)

    fake_stdout = (
        '[{"Service":"vllm","State":"running","Publishers":'
        '[{"PublishedPort":18000}]}]'
    )
    with patch("subprocess.run", return_value=_mock_subprocess_run(fake_stdout)):
        state, url = mod._docker_compose_ps("vllm")
    assert state == "running"
    assert url == "http://127.0.0.1:18000"


def test_compose_ps_exited_state(tmp_path, monkeypatch):
    """容器存在但 exited → state=exited(让 UI 显示"已安装·未启动")。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.config_panel import compose_manager as cm

    yaml_p = tmp_path / "comfyui.yaml"
    yaml_p.write_text("services:\n  comfyui:\n    image: x\n", encoding="utf-8")
    monkeypatch.setattr(cm, "get_compose_file_for_service", lambda _s: yaml_p)

    fake_stdout = (
        '{"Service":"comfyui","State":"exited","Publishers":[]}\n'
    )
    with patch("subprocess.run", return_value=_mock_subprocess_run(fake_stdout)):
        state, url = mod._docker_compose_ps("comfyui")
    assert state == "exited"
    assert url == ""


def test_compose_ps_other_service_in_yaml_ignored(tmp_path, monkeypatch):
    """yaml 里有多个 service,只匹配 service 参数指定的那个。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.config_panel import compose_manager as cm

    yaml_p = tmp_path / "multi.yaml"
    yaml_p.write_text(
        "services:\n  vllm:\n    image: x\n  redis:\n    image: y\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cm, "get_compose_file_for_service", lambda _s: yaml_p)

    fake_stdout = (
        '{"Service":"vllm","State":"running","Publishers":[{"PublishedPort":8000}]}\n'
        '{"Service":"redis","State":"running","Publishers":[{"PublishedPort":6379}]}\n'
    )
    with patch("subprocess.run", return_value=_mock_subprocess_run(fake_stdout)):
        state, url = mod._docker_compose_ps("vllm")
    assert state == "running"
    assert url == "http://127.0.0.1:8000"   # vllm 的端口,不是 redis 的


def test_compose_ps_subprocess_failure_returns_empty(tmp_path, monkeypatch):
    """subprocess 抛 FileNotFoundError(没装 docker)→ 返空。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.config_panel import compose_manager as cm

    yaml_p = tmp_path / "x.yaml"
    yaml_p.write_text("services:\n  x:\n    image: y\n", encoding="utf-8")
    monkeypatch.setattr(cm, "get_compose_file_for_service", lambda _s: yaml_p)

    with patch("subprocess.run", side_effect=FileNotFoundError):
        state, url = mod._docker_compose_ps("x")
    assert state == ""
    assert url == ""
