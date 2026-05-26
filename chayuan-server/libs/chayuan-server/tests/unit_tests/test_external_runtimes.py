"""92-1:external_runtimes.yaml 读写 + probe_external 测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# get_external_runtime / get_external_url
# ---------------------------------------------------------------------------

def _stub_yaml(doc: dict):
    fake = MagicMock()
    fake.doc = doc
    return fake


def test_get_external_runtime_returns_none_when_yaml_missing():
    from chayuan.server.config_panel import external_runtimes as mod
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               side_effect=FileNotFoundError("missing")):
        assert mod.get_external_runtime("infinity") is None


def test_get_external_runtime_returns_dict_when_present():
    from chayuan.server.config_panel import external_runtimes as mod
    doc = {
        "runtimes": {
            "infinity": {
                "url": "http://10.0.0.5:7997",
                "health_path": "/health",
                "enabled": True,
            }
        }
    }
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml(doc)):
        item = mod.get_external_runtime("infinity")
    assert item == {"url": "http://10.0.0.5:7997",
                    "health_path": "/health", "enabled": True}


def test_get_external_runtime_returns_none_when_disabled():
    from chayuan.server.config_panel import external_runtimes as mod
    doc = {
        "runtimes": {"infinity": {"url": "http://x", "enabled": False}}
    }
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml(doc)):
        assert mod.get_external_runtime("infinity") is None


def test_get_external_runtime_returns_none_when_url_empty():
    from chayuan.server.config_panel import external_runtimes as mod
    doc = {"runtimes": {"infinity": {"url": "", "enabled": True}}}
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml(doc)):
        assert mod.get_external_runtime("infinity") is None


def test_get_external_url_concatenates_health_path():
    from chayuan.server.config_panel import external_runtimes as mod
    doc = {
        "runtimes": {
            "comfyui": {
                "url": "http://10.0.0.5:18188",
                "health_path": "/system_stats",
                "enabled": True,
            }
        }
    }
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml(doc)):
        url = mod.get_external_url("comfyui")
    assert url == "http://10.0.0.5:18188/system_stats"


def test_get_external_url_uses_default_health_path_when_missing():
    """yaml 里没 health_path → 用调用方传的 default。"""
    from chayuan.server.config_panel import external_runtimes as mod
    doc = {"runtimes": {"infinity": {"url": "http://x:7997", "enabled": True}}}
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml(doc)):
        url = mod.get_external_url("infinity", default_health_path="/health")
    assert url == "http://x:7997/health"


def test_get_external_url_handles_relative_health_path():
    """health_path 不以 / 开头时自动补一个。"""
    from chayuan.server.config_panel import external_runtimes as mod
    doc = {
        "runtimes": {
            "x": {"url": "http://x", "health_path": "ping", "enabled": True}
        }
    }
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml(doc)):
        url = mod.get_external_url("x")
    assert url == "http://x/ping"


def test_get_external_url_returns_empty_when_no_config():
    from chayuan.server.config_panel import external_runtimes as mod
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml({})):
        assert mod.get_external_url("ghost", "/health") == ""


# ---------------------------------------------------------------------------
# set_external_url 写入
# ---------------------------------------------------------------------------

def test_set_external_url_auto_prepends_http_scheme():
    """94-1:不带 schema 的 URL 自动补 http://(老校验逻辑被替换)。"""
    from chayuan.server.config_panel import external_runtimes as mod
    captured = {}
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml({})), \
         patch("chayuan.server.config_panel.yaml_store.save_updates",
               lambda f, u: captured.update(u) or ("/p", "/b", [])):
        ok, _ = mod.set_external_url("infinity", "10.0.0.5:7997")
    assert ok is True
    assert captured["runtimes"]["infinity"]["url"] == "http://10.0.0.5:7997"


def test_normalize_url_already_has_http():
    from chayuan.server.config_panel.external_runtimes import normalize_url
    assert normalize_url("http://x:7997") == "http://x:7997"
    assert normalize_url("https://example.com") == "https://example.com"


def test_normalize_url_adds_http_when_missing():
    from chayuan.server.config_panel.external_runtimes import normalize_url
    assert normalize_url("127.0.0.1:7997") == "http://127.0.0.1:7997"
    assert normalize_url("localhost:18188") == "http://localhost:18188"
    assert normalize_url("my-server.local") == "http://my-server.local"


def test_normalize_url_strips_whitespace():
    from chayuan.server.config_panel.external_runtimes import normalize_url
    assert normalize_url("  http://x  ") == "http://x"
    assert normalize_url("\t127.0.0.1:7997\n") == "http://127.0.0.1:7997"


def test_normalize_url_empty_returns_empty():
    from chayuan.server.config_panel.external_runtimes import normalize_url
    assert normalize_url("") == ""
    assert normalize_url(None) == ""
    assert normalize_url("   ") == ""


def test_normalize_url_handles_partial_scheme():
    """``http:127.0.0.1`` 这种(漏 ``//``)的奇怪写法。"""
    from chayuan.server.config_panel.external_runtimes import normalize_url
    # 用户写错的 ``://`` 模式,补正成 http://
    assert normalize_url("ws://x:7997") == "http://x:7997"


def test_set_external_url_writes_runtimes_block():
    from chayuan.server.config_panel import external_runtimes as mod
    captured = {}

    def _save(name, updates):
        captured.update(updates)
        return ("/p", "/b", [])

    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml({})), \
         patch("chayuan.server.config_panel.yaml_store.save_updates", _save):
        ok, _ = mod.set_external_url(
            "infinity", "http://10.0.0.5:7997",
            health_path="/health", enabled=True,
        )
    assert ok is True
    assert "runtimes" in captured
    assert captured["runtimes"]["infinity"]["url"] == "http://10.0.0.5:7997"
    assert captured["runtimes"]["infinity"]["health_path"] == "/health"
    assert captured["runtimes"]["infinity"]["enabled"] is True


def test_set_external_url_preserves_other_runtimes():
    """改一个不影响另一个。"""
    from chayuan.server.config_panel import external_runtimes as mod
    captured = {}

    def _save(name, updates):
        captured.update(updates)
        return ("/p", "/b", [])

    existing = {
        "runtimes": {
            "comfyui": {"url": "http://x", "enabled": True},
        }
    }
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml(existing)), \
         patch("chayuan.server.config_panel.yaml_store.save_updates", _save):
        mod.set_external_url("infinity", "http://y:7997")
    assert "comfyui" in captured["runtimes"]
    assert "infinity" in captured["runtimes"]


def test_set_external_url_allows_empty_for_disabled():
    """url 可空(用作 enabled=false 占位)。"""
    from chayuan.server.config_panel import external_runtimes as mod
    captured = {}
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml({})), \
         patch("chayuan.server.config_panel.yaml_store.save_updates",
               lambda f, u: captured.update(u) or ("/p", "/b", [])):
        ok, _ = mod.set_external_url("vllm", "", enabled=False)
    assert ok is True


def test_set_external_url_rejects_empty_name():
    from chayuan.server.config_panel import external_runtimes as mod
    ok, msg = mod.set_external_url("", "http://x")
    assert ok is False
    assert "name" in msg


# ---------------------------------------------------------------------------
# delete / list
# ---------------------------------------------------------------------------

def test_delete_external_runtime_idempotent():
    from chayuan.server.config_panel import external_runtimes as mod
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml({})), \
         patch("chayuan.server.config_panel.yaml_store.save_updates",
               return_value=("/p", "/b", [])):
        ok, _ = mod.delete_external_runtime("ghost")
    assert ok is True


def test_delete_external_runtime_removes_existing():
    from chayuan.server.config_panel import external_runtimes as mod
    captured = {}

    def _save(name, updates):
        captured.update(updates)
        return ("/p", "/b", [])

    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml(
                   {"runtimes": {"infinity": {"url": "http://x"}}}
               )), \
         patch("chayuan.server.config_panel.yaml_store.save_updates", _save):
        mod.delete_external_runtime("infinity")
    assert "infinity" not in captured["runtimes"]


def test_list_external_runtimes_returns_flattened():
    from chayuan.server.config_panel import external_runtimes as mod
    doc = {
        "runtimes": {
            "infinity": {"url": "http://x", "enabled": True},
            "comfyui":  {"url": "http://y", "enabled": False},
        }
    }
    with patch("chayuan.server.config_panel.yaml_store.load_yaml",
               return_value=_stub_yaml(doc)):
        items = mod.list_external_runtimes()
    names = {i["name"]: i for i in items}
    assert names["infinity"]["url"] == "http://x"
    assert names["comfyui"]["enabled"] is False


# ---------------------------------------------------------------------------
# probe_external
# ---------------------------------------------------------------------------

def test_probe_external_empty_url_returns_false():
    from chayuan.server.config_panel.external_runtimes import probe_external
    ok, _ = probe_external("")
    assert ok is False


def test_probe_external_success_returns_true():
    from chayuan.server.config_panel import external_runtimes as mod
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.get = MagicMock(return_value=fake_resp)

    import sys
    fake_httpx = MagicMock()
    fake_httpx.get = MagicMock(return_value=fake_resp)
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        ok, detail = mod.probe_external("http://x:7997/health")
    assert ok is True
    assert detail == "200"


def test_probe_external_5xx_returns_false():
    from chayuan.server.config_panel import external_runtimes as mod
    fake_resp = MagicMock()
    fake_resp.status_code = 502
    import sys
    fake_httpx = MagicMock()
    fake_httpx.get = MagicMock(return_value=fake_resp)
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        ok, detail = mod.probe_external("http://x")
    assert ok is False
    assert "502" in detail


def test_probe_external_connection_error_returns_false():
    from chayuan.server.config_panel import external_runtimes as mod
    import sys
    fake_httpx = MagicMock()
    fake_httpx.get = MagicMock(side_effect=ConnectionError("refused"))
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        ok, detail = mod.probe_external("http://x")
    assert ok is False
    assert "ConnectionError" in detail or "refused" in detail
