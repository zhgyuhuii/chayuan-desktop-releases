"""93-1:local_infinity_pip 探活 helper 测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _check_binary
# ---------------------------------------------------------------------------

def test_check_binary_returns_path_when_available():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch("shutil.which", return_value="/usr/local/bin/infinity_emb"):
        assert mod._check_binary() == "/usr/local/bin/infinity_emb"


def test_check_binary_returns_none_when_missing():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch("shutil.which", return_value=None):
        assert mod._check_binary() is None


# ---------------------------------------------------------------------------
# _check_package_importable
# ---------------------------------------------------------------------------

def test_check_package_importable_true_when_spec_found():
    from chayuan.server.config_panel import local_infinity_pip as mod
    fake_spec = MagicMock()  # 任何非 None 即可
    with patch("importlib.util.find_spec", return_value=fake_spec):
        assert mod._check_package_importable() is True


def test_check_package_importable_false_when_not_found():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch("importlib.util.find_spec", return_value=None):
        assert mod._check_package_importable() is False


def test_check_package_importable_swallows_error():
    """find_spec 在破损环境抛异常 → 返 False,不冒泡。"""
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch("importlib.util.find_spec",
               side_effect=ValueError("broken sys.modules")):
        assert mod._check_package_importable() is False


# ---------------------------------------------------------------------------
# _check_port_open
# ---------------------------------------------------------------------------

def test_check_port_open_returns_true_on_2xx():
    """server 200/4xx 都算在跑。"""
    from chayuan.server.config_panel import local_infinity_pip as mod
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    import sys
    fake_httpx = MagicMock()
    fake_httpx.get = MagicMock(return_value=fake_resp)
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        assert mod._check_port_open("http://127.0.0.1:7997") is True


def test_check_port_open_returns_false_on_5xx():
    from chayuan.server.config_panel import local_infinity_pip as mod
    import sys
    fake_resp = MagicMock()
    fake_resp.status_code = 503
    fake_httpx = MagicMock()
    fake_httpx.get = MagicMock(return_value=fake_resp)
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        assert mod._check_port_open("http://127.0.0.1:7997") is False


def test_check_port_open_returns_false_on_connection_error():
    from chayuan.server.config_panel import local_infinity_pip as mod
    import sys
    fake_httpx = MagicMock()
    fake_httpx.get = MagicMock(side_effect=ConnectionError("refused"))
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        assert mod._check_port_open("http://x") is False


# ---------------------------------------------------------------------------
# _local_infinity_url
# ---------------------------------------------------------------------------

def test_local_infinity_url_default_port(monkeypatch):
    from chayuan.server.config_panel import local_infinity_pip as mod
    monkeypatch.delenv("CHAYUAN_LOCAL_INFINITY_PORT", raising=False)
    assert mod._local_infinity_url() == "http://127.0.0.1:7997"


def test_local_infinity_url_env_override(monkeypatch):
    from chayuan.server.config_panel import local_infinity_pip as mod
    monkeypatch.setenv("CHAYUAN_LOCAL_INFINITY_PORT", "9001")
    assert mod._local_infinity_url() == "http://127.0.0.1:9001"


def test_local_infinity_url_falls_back_on_invalid_env(monkeypatch):
    from chayuan.server.config_panel import local_infinity_pip as mod
    monkeypatch.setenv("CHAYUAN_LOCAL_INFINITY_PORT", "not-a-number")
    assert mod._local_infinity_url() == "http://127.0.0.1:7997"


# ---------------------------------------------------------------------------
# get_local_infinity_status 综合
# ---------------------------------------------------------------------------

def test_status_installed_when_binary_present():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch.object(mod, "_check_binary",
                      return_value="/usr/bin/infinity_emb"), \
         patch.object(mod, "_check_package_importable", return_value=False), \
         patch.object(mod, "_check_port_open", return_value=False):
        st = mod.get_local_infinity_status()
    assert st.installed is True
    assert st.running is False  # 端口没通
    assert st.binary_path == "/usr/bin/infinity_emb"


def test_status_installed_when_only_package():
    """二进制没装但包能 import 也算装(用户用 python -m 起)。"""
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch.object(mod, "_check_binary", return_value=None), \
         patch.object(mod, "_check_package_importable", return_value=True), \
         patch.object(mod, "_check_port_open", return_value=False):
        st = mod.get_local_infinity_status()
    assert st.installed is True


def test_status_running_when_installed_and_port_open():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch.object(mod, "_check_binary", return_value="/x/infinity_emb"), \
         patch.object(mod, "_check_package_importable", return_value=True), \
         patch.object(mod, "_check_port_open", return_value=True):
        st = mod.get_local_infinity_status()
    assert st.running is True


def test_status_not_installed_when_neither_binary_nor_package():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch.object(mod, "_check_binary", return_value=None), \
         patch.object(mod, "_check_package_importable", return_value=False):
        st = mod.get_local_infinity_status()
    assert st.installed is False
    assert st.running is False


def test_status_skips_port_probe_when_not_installed():
    """没装就不浪费 HTTP 探活。"""
    from chayuan.server.config_panel import local_infinity_pip as mod
    port_check = MagicMock(return_value=False)
    with patch.object(mod, "_check_binary", return_value=None), \
         patch.object(mod, "_check_package_importable", return_value=False), \
         patch.object(mod, "_check_port_open", port_check):
        mod.get_local_infinity_status()
    port_check.assert_not_called()


def test_status_to_dict_serializable():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch.object(mod, "_check_binary", return_value="/x"), \
         patch.object(mod, "_check_package_importable", return_value=True), \
         patch.object(mod, "_check_port_open", return_value=True):
        d = mod.get_local_infinity_status().to_dict()
    assert d["installed"] is True
    assert d["running"] is True
    assert d["base_url"].startswith("http://")


def test_get_status_with_probe_port_false_skips_http():
    from chayuan.server.config_panel import local_infinity_pip as mod
    port_check = MagicMock(return_value=True)
    with patch.object(mod, "_check_binary", return_value="/x"), \
         patch.object(mod, "_check_package_importable", return_value=True), \
         patch.object(mod, "_check_port_open", port_check):
        st = mod.get_local_infinity_status(probe_port=False)
    port_check.assert_not_called()
    assert st.port_open is False  # 没探就当未通


# ---------------------------------------------------------------------------
# 公开 helper
# ---------------------------------------------------------------------------

def test_is_local_infinity_pip_available_true():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch.object(mod, "_check_binary", return_value="/x/infinity_emb"):
        assert mod.is_local_infinity_pip_available() is True


def test_is_local_infinity_pip_available_false():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch.object(mod, "_check_binary", return_value=None), \
         patch.object(mod, "_check_package_importable", return_value=False):
        assert mod.is_local_infinity_pip_available() is False


def test_is_local_infinity_running_true():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch.object(mod, "_check_binary", return_value="/x"), \
         patch.object(mod, "_check_package_importable", return_value=True), \
         patch.object(mod, "_check_port_open", return_value=True):
        assert mod.is_local_infinity_running() is True


def test_is_local_infinity_running_false_when_not_installed():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch.object(mod, "_check_binary", return_value=None), \
         patch.object(mod, "_check_package_importable", return_value=False):
        assert mod.is_local_infinity_running() is False
