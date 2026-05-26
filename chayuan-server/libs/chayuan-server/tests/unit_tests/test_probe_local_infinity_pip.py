"""93-2:probe_framework 识别本地 pip Infinity。"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def _spec_for(name: str):
    from chayuan.server.config_panel.runtime_framework_panel import (
        _FRAMEWORKS_BY_NAME,
    )
    return _FRAMEWORKS_BY_NAME[name]


def _stub_local_status(*, installed: bool, running: bool,
                       binary_path=None, base_url="http://127.0.0.1:7997"):
    from chayuan.server.config_panel.local_infinity_pip import (
        LocalInfinityStatus,
    )
    return LocalInfinityStatus(
        binary_path=binary_path,
        package_importable=installed and binary_path is None,
        port_open=running,
        base_url=base_url,
    )


def test_probe_local_infinity_pip_running_returns_running():
    """本地 pip 装了 + 端口通 → state=running,绕过 docker 路径。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    spec = _spec_for("infinity")
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_url",
        return_value="",
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.get_local_infinity_status",
        return_value=_stub_local_status(
            installed=True, running=True,
            binary_path="/usr/local/bin/infinity_emb",
        ),
    ):
        h = mod.probe_framework(spec)
    assert h.state == "running"
    assert h.bin_path == "/usr/local/bin/infinity_emb"
    assert "127.0.0.1:7997" in h.url


def test_probe_local_pip_installed_but_port_closed_falls_through():
    """本地 pip 装了但端口没通(用户没起进程)→ 让后续 docker 路径继续判。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    spec = _spec_for("infinity")
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_url",
        return_value="",
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.get_local_infinity_status",
        return_value=_stub_local_status(
            installed=True, running=False,
            binary_path="/usr/local/bin/infinity_emb",
        ),
    ), patch.object(mod, "_http_ping", return_value=False), patch.object(
        mod, "_docker_container_endpoint", return_value=("missing", ""),
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip._check_binary",
        return_value="/usr/local/bin/infinity_emb",
    ):
        h = mod.probe_framework(spec)
    # 二进制可见 → state 应是 installed(让用户能点"启动")
    assert h.state == "installed"
    assert h.bin_path == "/usr/local/bin/infinity_emb"


def test_probe_other_framework_unaffected():
    """非 infinity framework 不走 local pip 路径(回归保护)。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from unittest.mock import MagicMock

    # 选一个 install_kind=docker 的非 infinity:comfyui
    spec = _spec_for("comfyui")
    pip_check = MagicMock()
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_url",
        return_value="",
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.get_local_infinity_status",
        pip_check,
    ), patch.object(mod, "_http_ping", return_value=False), patch.object(
        mod, "_docker_container_endpoint", return_value=("missing", ""),
    ):
        mod.probe_framework(spec)
    pip_check.assert_not_called()


def test_probe_swallows_local_pip_module_error():
    """local_infinity_pip 抛异常 → 不阻断,走原 docker 路径。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    spec = _spec_for("infinity")
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_url",
        return_value="",
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.get_local_infinity_status",
        side_effect=RuntimeError("module broken"),
    ), patch.object(mod, "_http_ping", return_value=False), patch.object(
        mod, "_docker_container_endpoint", return_value=("missing", ""),
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip._check_binary",
        side_effect=RuntimeError("ditto"),
    ):
        h = mod.probe_framework(spec)
    # 没崩
    assert h.state in ("missing", "installed", "running")


def test_probe_external_url_takes_priority_over_local_pip():
    """配了外置 URL 且 ping 通 → 应该比本地 pip 优先。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from unittest.mock import MagicMock

    spec = _spec_for("infinity")
    pip_check = MagicMock()
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_url",
        return_value="http://10.0.0.5:7997/health",
    ), patch.object(mod, "_http_ping", return_value=True), patch(
        "chayuan.server.config_panel.local_infinity_pip.get_local_infinity_status",
        pip_check,
    ):
        h = mod.probe_framework(spec)
    assert h.state == "running"
    assert "10.0.0.5" in h.url
    # 本地 pip 探活根本没被调
    pip_check.assert_not_called()
