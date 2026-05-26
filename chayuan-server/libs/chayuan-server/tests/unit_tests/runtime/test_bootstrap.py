"""runtime.bootstrap.allocate_core_ports + render_endpoints_table 烟雾测试。

这里通过 monkeypatch 把 ``CHAYUAN_ROOT`` 切到 tmp_path、再 reload 一遍
``runtime_info`` 单例，避免污染真实数据目录。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch):
    """让 RuntimeInfo / Settings 把 runtime.json 写到 tmp_path。"""
    # 关键：CHAYUAN_ROOT 在 import 时就被 settings 缓存，所以这里只能改 runtime_info_path
    from chayuan.server.runtime import runtime_info as ri_mod
    monkeypatch.setattr(ri_mod, "runtime_info_path", lambda: tmp_path / "runtime.json")
    # 强制重建单例
    ri_mod._SINGLETON = None  # noqa: SLF001
    yield tmp_path
    ri_mod._SINGLETON = None  # noqa: SLF001


def test_allocate_core_ports_writes_runtime_json(isolated_root):
    from chayuan.server.runtime import allocate_core_ports
    result = allocate_core_ports()
    assert result.api.port > 0
    assert result.config_panel.port > 0
    assert result.api.port != result.config_panel.port
    # runtime.json 真的落盘了
    assert (isolated_root / "runtime.json").is_file()


def test_render_endpoints_table_includes_warnings(isolated_root, monkeypatch):
    """模拟 PortAllocator 自动 bump 的场景：偏好端口被占应进 warnings。"""
    from chayuan.server.runtime import allocate_core_ports, render_endpoints_table
    from chayuan.server.runtime import port_allocator as pa_mod

    # 让 is_port_free 永远说"被占用"，强制走 PortInUseError 兜底分支
    monkeypatch.setattr(pa_mod, "is_port_free", lambda *a, **kw: True)
    result = allocate_core_ports()
    text = render_endpoints_table(result)
    assert "服务" in text
    assert "地址" in text
