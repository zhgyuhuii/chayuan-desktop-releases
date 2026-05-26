"""96-2:外置 endpoint 保存 → 读回 端到端测试。

用户原话:"现在配置后发现没保存链接和端口"
本测试用真实文件(tmp_path)验证 set + load 闭环。
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_chayuan_root(tmp_path, monkeypatch):
    """把 CHAYUAN_ROOT 切到 tmp_path,让 yaml_store 写到这里。"""
    monkeypatch.setattr(
        "chayuan.settings.CHAYUAN_ROOT", str(tmp_path),
    )
    return tmp_path


def test_set_and_get_external_url_first_time(fake_chayuan_root):
    """首次配置(yaml 不存在)→ 写入成功 + 读回拿到原值。"""
    from chayuan.server.config_panel import external_runtimes as mod

    ok, msg = mod.set_external_url(
        "infinity", "http://127.0.0.1:7997",
        health_path="/health", enabled=True,
    )
    assert ok is True

    # 文件应当真的落盘
    yaml_p = fake_chayuan_root / "external_runtimes.yaml"
    assert yaml_p.exists()
    content = yaml_p.read_text(encoding="utf-8")
    assert "infinity" in content
    assert "127.0.0.1:7997" in content

    # 读回
    item = mod.get_external_runtime("infinity")
    assert item is not None
    assert item["url"] == "http://127.0.0.1:7997"
    assert item["health_path"] == "/health"
    assert item["enabled"] is True


def test_set_external_url_no_schema_persists_normalized(fake_chayuan_root):
    """填 ``127.0.0.1:7997``(无 schema)→ 落盘是 ``http://127.0.0.1:7997``。"""
    from chayuan.server.config_panel import external_runtimes as mod

    ok, _ = mod.set_external_url("infinity", "127.0.0.1:7997")
    assert ok is True
    item = mod.get_external_runtime("infinity")
    assert item["url"] == "http://127.0.0.1:7997"


def test_set_two_runtimes_both_persisted(fake_chayuan_root):
    """先后配 infinity / comfyui → 两条都在 yaml 里,互不覆盖。"""
    from chayuan.server.config_panel import external_runtimes as mod

    mod.set_external_url("infinity", "http://10.0.0.5:7997")
    mod.set_external_url("comfyui", "http://10.0.0.6:18188")

    items = {it["name"]: it for it in mod.list_external_runtimes()}
    assert "infinity" in items
    assert "comfyui" in items
    assert items["infinity"]["url"] == "http://10.0.0.5:7997"
    assert items["comfyui"]["url"] == "http://10.0.0.6:18188"


def test_update_existing_runtime_overwrites(fake_chayuan_root):
    """改了 URL 再保存 → 旧 URL 被覆盖。"""
    from chayuan.server.config_panel import external_runtimes as mod

    mod.set_external_url("infinity", "http://old:7997")
    mod.set_external_url("infinity", "http://new:7997")

    item = mod.get_external_runtime("infinity")
    assert item["url"] == "http://new:7997"


def test_set_then_disable_keeps_url_but_returns_none_from_get(fake_chayuan_root):
    """enabled=False → ``get_external_runtime`` 返 None(让上层走 docker 回退);
    但 yaml 里 url 仍保留(用户改 enabled=True 后能恢复)。
    """
    from chayuan.server.config_panel import external_runtimes as mod

    mod.set_external_url("infinity", "http://x:7997", enabled=True)
    mod.set_external_url("infinity", "http://x:7997", enabled=False)

    assert mod.get_external_runtime("infinity") is None  # 禁用时不返回
    # 但底层 list 仍能看到它
    items = {it["name"]: it for it in mod.list_external_runtimes()}
    assert items["infinity"]["url"] == "http://x:7997"
    assert items["infinity"]["enabled"] is False


def test_delete_and_then_get_returns_none(fake_chayuan_root):
    from chayuan.server.config_panel import external_runtimes as mod

    mod.set_external_url("infinity", "http://x:7997")
    assert mod.get_external_runtime("infinity") is not None

    ok, _ = mod.delete_external_runtime("infinity")
    assert ok is True
    assert mod.get_external_runtime("infinity") is None


def test_get_external_url_concatenates_health_path_correctly(fake_chayuan_root):
    """端到端:set + get_external_url 应该返完整探活 URL。"""
    from chayuan.server.config_panel import external_runtimes as mod

    mod.set_external_url("infinity", "http://x:7997", health_path="/health")
    full = mod.get_external_url("infinity")
    assert full == "http://x:7997/health"


def test_set_external_url_on_locked_yaml_returns_friendly_error(
    fake_chayuan_root, monkeypatch,
):
    """yaml_store.save_updates 抛异常 → 返 ok=False 不冒泡。"""
    from chayuan.server.config_panel import external_runtimes as mod

    def _broken_save(name, updates):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(
        "chayuan.server.config_panel.yaml_store.save_updates",
        _broken_save,
    )
    ok, msg = mod.set_external_url("infinity", "http://x:7997")
    assert ok is False
    assert "PermissionError" in msg or "read-only" in msg
