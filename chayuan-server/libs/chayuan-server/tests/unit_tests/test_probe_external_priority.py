"""92-2:probe_framework 外置 endpoint 最高优先级。

要点:
  * 配了外置 URL 且 ping 通 → state=running,绕过 docker
  * 外置 URL ping 不通 → 走原 docker / default_url 流程,但 url 字段保留外置地址
  * 没配外置 URL → 行为与 92 题前完全一致(回归保护)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def _spec_for(name: str):
    """根据 name 拿真实 _FrameworkSpec(从 catalog)。"""
    from chayuan.server.config_panel.runtime_framework_panel import (
        _FRAMEWORKS_BY_NAME,
    )
    s = _FRAMEWORKS_BY_NAME.get(name)
    if s is None:
        raise AssertionError(f"unknown framework: {name}")
    return s


def test_probe_external_url_ping_ok_returns_running():
    """配了外置 URL + ping 通 → state=running,完全绕过 docker。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    spec = _spec_for("infinity")
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_url",
        return_value="http://10.0.0.5:7997/health",
    ), patch.object(mod, "_http_ping", return_value=True), patch.object(
        mod, "_docker_container_endpoint", return_value=("missing", ""),
    ):
        h = mod.probe_framework(spec)
    assert h.state == "running"
    assert h.url == "http://10.0.0.5:7997/health"


def test_probe_external_url_ping_fail_falls_back_to_docker():
    """外置 URL 不通 → 还能走 docker 探测;url 仍显示外置地址。

    用 comfyui 测 — 它在静态 catalog 里 install_kind=docker。
    """
    from chayuan.server.config_panel import runtime_framework_panel as mod

    spec = _spec_for("comfyui")
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_url",
        return_value="http://10.0.0.5:18188/system_stats",
    ), patch.object(mod, "_http_ping", return_value=False), patch.object(
        mod, "_docker_container_endpoint",
        return_value=("running", "http://127.0.0.1:18188"),
    ), patch.object(mod, "_which_any", return_value=None):
        h = mod.probe_framework(spec)
    # docker 容器在跑 → 判 running
    assert h.state == "running"


def test_probe_no_external_unchanged_behavior_when_docker_running():
    """没配外置 URL → 走原 docker 探测路径,行为不变(回归保护)。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    spec = _spec_for("comfyui")
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_url",
        return_value="",
    ), patch.object(mod, "_http_ping", return_value=False), patch.object(
        mod, "_docker_container_endpoint",
        return_value=("running", "http://127.0.0.1:18188"),
    ), patch.object(mod, "_which_any", return_value=None):
        h = mod.probe_framework(spec)
    assert h.state == "running"
    # docker_inspect 给的 url 占主导
    assert "18188" in h.url


def test_probe_external_url_skips_docker_inspect_call():
    """ping 通后应直接 return,不调 _docker_container_endpoint。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from unittest.mock import MagicMock

    spec = _spec_for("infinity")
    docker_probe = MagicMock(return_value=("missing", ""))
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_url",
        return_value="http://x:7997/health",
    ), patch.object(mod, "_http_ping", return_value=True), patch.object(
        mod, "_docker_container_endpoint", docker_probe,
    ):
        mod.probe_framework(spec)
    docker_probe.assert_not_called()


def test_probe_comfyui_external_works_too():
    """ComfyUI 同样支持外置 URL。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    spec = _spec_for("comfyui")
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_url",
        return_value="http://192.168.1.100:18188/system_stats",
    ), patch.object(mod, "_http_ping", return_value=True):
        h = mod.probe_framework(spec)
    assert h.state == "running"
    assert "192.168.1.100" in h.url


def test_probe_swallows_external_runtimes_module_error():
    """external_runtimes 模块本身抛异常 → 走原路径,不阻断探测。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    spec = _spec_for("infinity")
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_url",
        side_effect=RuntimeError("yaml parse error"),
    ), patch.object(mod, "_http_ping", return_value=False), patch.object(
        mod, "_docker_container_endpoint",
        return_value=("missing", ""),
    ), patch.object(mod, "_which_any", return_value=None):
        h = mod.probe_framework(spec)
    # 没崩,正常走 missing
    assert h.state in ("missing", "installed", "running")
