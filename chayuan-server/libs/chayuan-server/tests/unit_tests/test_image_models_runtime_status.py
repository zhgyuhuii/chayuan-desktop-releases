"""89-6:GET /image_models/runtime_status 路由测试。

覆盖:
  * _scan_infinity_loaded 解析 yaml + ps state
  * _scan_hf_cache_present 解析 cache 目录命名
  * runtime_status 5s LRU 缓存命中
  * resolve_default 失败 → 降级返空字段
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _scan_infinity_loaded
# ---------------------------------------------------------------------------

def test_scan_infinity_loaded_parses_env_list(tmp_path):
    """environment 是 list 形式 → 提取 MODEL_ID。"""
    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text(
        "services:\n"
        "  infinity:\n"
        "    image: x\n"
        "    environment:\n"
        "      - MODEL_ID=jinaai/jina-clip-v1\n"
        "      - PORT=7997\n",
        encoding="utf-8",
    )
    from chayuan.server.api_server import image_routes as mod
    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ), patch(
        "chayuan.server.config_panel.runtime_framework_panel._docker_compose_ps",
        return_value=("running", "http://127.0.0.1:37997"),
    ):
        out = mod._scan_infinity_loaded()
    assert out == [{"model_id": "jinaai/jina-clip-v1"}]


def test_scan_infinity_loaded_parses_env_dict(tmp_path):
    """environment 是 dict 形式同样能解析。"""
    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text(
        "services:\n"
        "  infinity:\n"
        "    image: x\n"
        "    environment:\n"
        "      MODEL_ID: jinaai/jina-clip-v2\n",
        encoding="utf-8",
    )
    from chayuan.server.api_server import image_routes as mod
    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ), patch(
        "chayuan.server.config_panel.runtime_framework_panel._docker_compose_ps",
        return_value=("running", "http://127.0.0.1:37997"),
    ):
        out = mod._scan_infinity_loaded()
    assert out == [{"model_id": "jinaai/jina-clip-v2"}]


def test_scan_infinity_loaded_returns_empty_when_not_running(tmp_path):
    """容器 exited → 不算 loaded。"""
    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text(
        "services:\n  infinity:\n    image: x\n"
        "    environment:\n      - MODEL_ID=x/y\n",
        encoding="utf-8",
    )
    from chayuan.server.api_server import image_routes as mod
    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ), patch(
        "chayuan.server.config_panel.runtime_framework_panel._docker_compose_ps",
        return_value=("exited", ""),
    ):
        out = mod._scan_infinity_loaded()
    assert out == []


def test_scan_infinity_loaded_no_yaml_returns_empty():
    from chayuan.server.api_server import image_routes as mod
    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=None,
    ):
        out = mod._scan_infinity_loaded()
    assert out == []


def test_scan_infinity_loaded_no_model_id_returns_empty(tmp_path):
    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text("services:\n  infinity:\n    image: x\n", encoding="utf-8")
    from chayuan.server.api_server import image_routes as mod
    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ):
        out = mod._scan_infinity_loaded()
    assert out == []


# ---------------------------------------------------------------------------
# _scan_hf_cache_present
# ---------------------------------------------------------------------------

def test_scan_hf_cache_present_lists_models(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / "models--jinaai--jina-clip-v1").mkdir()
    (hub / "models--BAAI--bge-m3").mkdir()
    (hub / "datasets--something").mkdir()  # 应被忽略
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    from chayuan.server.api_server import image_routes as mod
    out = mod._scan_hf_cache_present()
    assert "jinaai/jina-clip-v1" in out
    assert "BAAI/bge-m3" in out
    assert all(not name.startswith("datasets") for name in out)


def test_scan_hf_cache_present_returns_empty_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "ghost"))
    from chayuan.server.api_server import image_routes as mod
    assert mod._scan_hf_cache_present() == []


# ---------------------------------------------------------------------------
# runtime_status route(直接 call 视图函数)
# ---------------------------------------------------------------------------

def test_runtime_status_returns_full_payload(tmp_path, monkeypatch):
    from chayuan.server.api_server import image_routes as mod

    mod._invalidate_runtime_status_cache()

    fake_cli = MagicMock()
    fake_cli.kind = "infinity"
    fake_cli.healthcheck = MagicMock(return_value=True)

    with patch.object(mod, "_scan_infinity_loaded",
                      return_value=[{"model_id": "j/c"}]), \
         patch.object(mod, "_scan_hf_cache_present",
                      return_value=["j/c", "BAAI/bge-m3"]), \
         patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("j/c", "infinity-local")), \
         patch("chayuan.server.image_source.embedder.get_client",
               return_value=fake_cli):
        ret = mod.image_models_runtime_status(user={"id": 1})

    assert ret["code"] == 0
    d = ret["data"]
    assert d["default_model_id"] == "j/c"
    assert d["default_platform"] == "infinity-local"
    assert d["client_in_use"] == "infinity"
    assert d["client_healthy"] is True
    assert d["infinity_loaded"] == [{"model_id": "j/c"}]
    assert "j/c" in d["hf_cache_present"]


def test_runtime_status_caches_within_ttl():
    """5s 内重复请求走缓存,不再调 _scan_*。"""
    from chayuan.server.api_server import image_routes as mod
    mod._invalidate_runtime_status_cache()

    scan_inf = MagicMock(return_value=[])
    scan_hf = MagicMock(return_value=[])

    with patch.object(mod, "_scan_infinity_loaded", scan_inf), \
         patch.object(mod, "_scan_hf_cache_present", scan_hf), \
         patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("x", None)), \
         patch("chayuan.server.image_source.embedder.get_client",
               side_effect=RuntimeError("no client")):
        mod.image_models_runtime_status(user={"id": 1})
        mod.image_models_runtime_status(user={"id": 1})
        mod.image_models_runtime_status(user={"id": 1})

    # 第一次走真路径,后续两次缓存命中
    assert scan_inf.call_count == 1
    assert scan_hf.call_count == 1
    mod._invalidate_runtime_status_cache()


def test_runtime_status_get_client_failure_returns_unhealthy():
    """get_client 抛 → client_in_use="" + healthy=False,但接口仍 200。"""
    from chayuan.server.api_server import image_routes as mod
    mod._invalidate_runtime_status_cache()

    with patch.object(mod, "_scan_infinity_loaded", return_value=[]), \
         patch.object(mod, "_scan_hf_cache_present", return_value=[]), \
         patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("ghost/x", "infinity-local")), \
         patch("chayuan.server.image_source.embedder.get_client",
               side_effect=RuntimeError("everything is broken")):
        ret = mod.image_models_runtime_status(user={"id": 1})
    assert ret["code"] == 0
    assert ret["data"]["client_in_use"] == ""
    assert ret["data"]["client_healthy"] is False
    mod._invalidate_runtime_status_cache()


def test_invalidate_runtime_status_cache_forces_re_scan():
    """主动调 _invalidate 后下一次再扫。"""
    from chayuan.server.api_server import image_routes as mod
    mod._invalidate_runtime_status_cache()

    scan_inf = MagicMock(return_value=[])
    scan_hf = MagicMock(return_value=[])

    with patch.object(mod, "_scan_infinity_loaded", scan_inf), \
         patch.object(mod, "_scan_hf_cache_present", scan_hf), \
         patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("x", None)), \
         patch("chayuan.server.image_source.embedder.get_client",
               side_effect=RuntimeError("no client")):
        mod.image_models_runtime_status(user={"id": 1})
        mod._invalidate_runtime_status_cache()
        mod.image_models_runtime_status(user={"id": 1})

    assert scan_inf.call_count == 2
    mod._invalidate_runtime_status_cache()
