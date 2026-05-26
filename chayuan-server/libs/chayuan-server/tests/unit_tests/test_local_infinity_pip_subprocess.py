"""93-4:start_local_infinity_subprocess + 路由。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# start_local_infinity_subprocess
# ---------------------------------------------------------------------------

def test_start_rejects_empty_model_id():
    from chayuan.server.config_panel import local_infinity_pip as mod
    ret = mod.start_local_infinity_subprocess("")
    assert ret["ok"] is False
    assert "model_id" in ret["msg"]


def test_start_rejects_when_neither_binary_nor_package():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch.object(mod, "_check_binary", return_value=None), \
         patch.object(mod, "_check_package_importable", return_value=False):
        ret = mod.start_local_infinity_subprocess("jinaai/jina-clip-v1")
    assert ret["ok"] is False
    assert "infinity_emb" in ret["msg"]


def test_start_idempotent_when_port_already_open():
    """端口已通 → 不重复起 Popen。"""
    from chayuan.server.config_panel import local_infinity_pip as mod
    popen = MagicMock()
    with patch.object(mod, "_check_binary", return_value="/u/infinity_emb"), \
         patch.object(mod, "_check_port_open", return_value=True), \
         patch("subprocess.Popen", popen):
        ret = mod.start_local_infinity_subprocess("jinaai/jina-clip-v1")
    assert ret["ok"] is True
    assert ret["pid"] is None
    assert "已通" in ret["msg"] or "重复" in ret["msg"]
    popen.assert_not_called()


def test_start_uses_binary_when_available(monkeypatch):
    from chayuan.server.config_panel import local_infinity_pip as mod
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    monkeypatch.delenv("CHAYUAN_LOCAL_INFINITY_PORT", raising=False)
    with patch.object(mod, "_check_binary",
                      return_value="/usr/local/bin/infinity_emb"), \
         patch.object(mod, "_check_port_open", return_value=False), \
         patch("subprocess.Popen", return_value=fake_proc) as popen:
        ret = mod.start_local_infinity_subprocess("jinaai/jina-clip-v1")
    assert ret["ok"] is True
    assert ret["pid"] == 12345
    cmd = popen.call_args[0][0]
    assert cmd[0] == "/usr/local/bin/infinity_emb"
    assert "--model-id" in cmd
    assert "jinaai/jina-clip-v1" in cmd
    assert "--port" in cmd


def test_start_uses_python_dash_m_when_only_package_available():
    """无二进制但包能 import → python -m infinity_emb。"""
    from chayuan.server.config_panel import local_infinity_pip as mod
    fake_proc = MagicMock(); fake_proc.pid = 99
    with patch.object(mod, "_check_binary", return_value=None), \
         patch.object(mod, "_check_package_importable", return_value=True), \
         patch.object(mod, "_check_port_open", return_value=False), \
         patch("subprocess.Popen", return_value=fake_proc) as popen:
        mod.start_local_infinity_subprocess("x/y")
    cmd = popen.call_args[0][0]
    assert "-m" in cmd
    assert "infinity_emb" in cmd


def test_start_respects_custom_port():
    from chayuan.server.config_panel import local_infinity_pip as mod
    fake_proc = MagicMock(); fake_proc.pid = 1
    with patch.object(mod, "_check_binary", return_value="/x/infinity_emb"), \
         patch.object(mod, "_check_port_open", return_value=False), \
         patch("subprocess.Popen", return_value=fake_proc) as popen:
        ret = mod.start_local_infinity_subprocess("a/b", port=9001)
    cmd = popen.call_args[0][0]
    assert "9001" in cmd
    assert ret["url"] == "http://127.0.0.1:9001"


def test_start_returns_error_on_popen_failure():
    from chayuan.server.config_panel import local_infinity_pip as mod
    with patch.object(mod, "_check_binary", return_value="/x/infinity_emb"), \
         patch.object(mod, "_check_port_open", return_value=False), \
         patch("subprocess.Popen",
               side_effect=PermissionError("denied")):
        ret = mod.start_local_infinity_subprocess("a/b")
    assert ret["ok"] is False
    assert "PermissionError" in ret["msg"]


# ---------------------------------------------------------------------------
# 路由层
# ---------------------------------------------------------------------------

def test_status_endpoint_returns_dict():
    from chayuan.server.api_server import image_routes as mod

    fake_status = MagicMock()
    fake_status.to_dict = MagicMock(return_value={
        "binary_path": "/x", "package_importable": True,
        "port_open": True, "base_url": "http://127.0.0.1:7997",
        "installed": True, "running": True,
    })
    with patch(
        "chayuan.server.config_panel.local_infinity_pip.get_local_infinity_status",
        return_value=fake_status,
    ):
        ret = mod.local_infinity_pip_status_endpoint(user={"id": 1})
    assert ret["code"] == 0
    assert ret["data"]["installed"] is True


def test_start_endpoint_rejects_non_admin():
    from chayuan.server.api_server import image_routes as mod
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        mod.local_infinity_pip_start_endpoint(
            payload={"model_id": "a/b"},
            user={"id": 2, "role": "user"},
        )
    assert exc.value.status_code == 403


def test_start_endpoint_requires_model_id():
    from chayuan.server.api_server import image_routes as mod
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        mod.local_infinity_pip_start_endpoint(
            payload={}, user={"id": 1, "role": "admin"},
        )
    assert exc.value.status_code == 400


def test_start_endpoint_admin_full_path():
    from chayuan.server.api_server import image_routes as mod

    with patch(
        "chayuan.server.config_panel.local_infinity_pip.start_local_infinity_subprocess",
        return_value={
            "ok": True, "pid": 1234,
            "msg": "已发起启动", "url": "http://127.0.0.1:7997",
        },
    ):
        ret = mod.local_infinity_pip_start_endpoint(
            payload={"model_id": "jinaai/jina-clip-v1"},
            user={"id": 1, "role": "admin"},
        )
    assert ret["code"] == 0
    assert ret["data"]["pid"] == 1234


def test_start_endpoint_allows_guest():
    from chayuan.server.api_server import image_routes as mod
    with patch(
        "chayuan.server.config_panel.local_infinity_pip.start_local_infinity_subprocess",
        return_value={"ok": True, "pid": 1, "msg": "ok",
                      "url": "http://x:7997"},
    ):
        ret = mod.local_infinity_pip_start_endpoint(
            payload={"model_id": "x/y"},
            user={"id": -1, "is_guest": True, "role": "user"},
        )
    assert ret["code"] == 0


def test_start_endpoint_propagates_failure_as_code1():
    from chayuan.server.api_server import image_routes as mod
    with patch(
        "chayuan.server.config_panel.local_infinity_pip.start_local_infinity_subprocess",
        return_value={"ok": False, "pid": None,
                      "msg": "binary missing", "url": ""},
    ):
        ret = mod.local_infinity_pip_start_endpoint(
            payload={"model_id": "x/y"},
            user={"id": 1, "role": "admin"},
        )
    assert ret["code"] == 1
    assert "binary missing" in ret["msg"]
