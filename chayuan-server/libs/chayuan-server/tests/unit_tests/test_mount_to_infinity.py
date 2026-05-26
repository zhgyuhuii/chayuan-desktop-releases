"""89-9:POST /image_models/mount_to_infinity 测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _write_infinity_model_id:替换 / 追加 / 异常
# ---------------------------------------------------------------------------

def test_write_model_id_replaces_existing_in_list(tmp_path):
    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text(
        "services:\n"
        "  infinity:\n"
        "    image: x\n"
        "    environment:\n"
        "      - MODEL_ID=old/model\n"
        "      - PORT=7997\n",
        encoding="utf-8",
    )
    from chayuan.server.api_server import image_routes as mod

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ):
        ret = mod._write_infinity_model_id("jinaai/jina-clip-v1")
    assert ret == yaml_p
    text = yaml_p.read_text(encoding="utf-8")
    assert "MODEL_ID=jinaai/jina-clip-v1" in text
    assert "MODEL_ID=old/model" not in text
    assert "PORT=7997" in text  # 其它环境变量保留


def test_write_model_id_appends_when_missing_in_list(tmp_path):
    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text(
        "services:\n"
        "  infinity:\n"
        "    image: x\n"
        "    environment:\n"
        "      - PORT=7997\n",
        encoding="utf-8",
    )
    from chayuan.server.api_server import image_routes as mod

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ):
        mod._write_infinity_model_id("X/Y")
    text = yaml_p.read_text(encoding="utf-8")
    assert "MODEL_ID=X/Y" in text
    assert "PORT=7997" in text


def test_write_model_id_replaces_in_dict_form(tmp_path):
    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text(
        "services:\n"
        "  infinity:\n"
        "    image: x\n"
        "    environment:\n"
        "      MODEL_ID: old/m\n"
        "      PORT: 7997\n",
        encoding="utf-8",
    )
    from chayuan.server.api_server import image_routes as mod

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ):
        mod._write_infinity_model_id("new/m")
    import yaml as _yaml
    doc = _yaml.safe_load(yaml_p.read_text(encoding="utf-8"))
    env = doc["services"]["infinity"]["environment"]
    assert env["MODEL_ID"] == "new/m"
    assert env["PORT"] == 7997


def test_write_model_id_creates_environment_when_absent(tmp_path):
    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text(
        "services:\n  infinity:\n    image: x\n",
        encoding="utf-8",
    )
    from chayuan.server.api_server import image_routes as mod

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ):
        mod._write_infinity_model_id("a/b")
    text = yaml_p.read_text(encoding="utf-8")
    assert "MODEL_ID=a/b" in text


def test_write_model_id_404_when_yaml_missing():
    from chayuan.server.api_server import image_routes as mod
    from fastapi import HTTPException

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=None,
    ):
        with pytest.raises(HTTPException) as exc:
            mod._write_infinity_model_id("a/b")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 路由函数:权限 + 参数 + 触发异步
# ---------------------------------------------------------------------------

def test_mount_endpoint_requires_model_id():
    from chayuan.server.api_server import image_routes as mod
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        mod.mount_to_infinity_endpoint(
            payload={}, user={"id": 1, "role": "admin"},
        )
    assert exc.value.status_code == 400


def test_mount_endpoint_rejects_non_admin():
    from chayuan.server.api_server import image_routes as mod
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        mod.mount_to_infinity_endpoint(
            payload={"model_id": "a/b"},
            user={"id": 2, "role": "user"},
        )
    assert exc.value.status_code == 403


def test_mount_endpoint_allows_guest_when_auth_disabled(tmp_path):
    """AUTH_REQUIRED=false 时 user 是 GUEST_USER,允许操作。"""
    from chayuan.server.api_server import image_routes as mod

    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text("services:\n  infinity:\n    image: x\n", encoding="utf-8")

    kicked = MagicMock()
    with patch.object(mod, "_write_infinity_model_id", return_value=yaml_p), \
         patch.object(mod, "_kick_infinity_restart_async", kicked):
        ret = mod.mount_to_infinity_endpoint(
            payload={"model_id": "x/y"},
            user={"id": -1, "role": "user", "is_guest": True},
        )
    assert ret["code"] == 0
    assert ret["data"]["model_id"] == "x/y"
    kicked.assert_called_once_with(yaml_p)


def test_mount_endpoint_admin_full_path(tmp_path):
    from chayuan.server.api_server import image_routes as mod

    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text("services:\n  infinity:\n    image: x\n", encoding="utf-8")

    kicked = MagicMock()
    with patch.object(mod, "_write_infinity_model_id", return_value=yaml_p), \
         patch.object(mod, "_kick_infinity_restart_async", kicked):
        ret = mod.mount_to_infinity_endpoint(
            payload={"model_id": "jinaai/jina-clip-v1"},
            user={"id": 1, "role": "admin"},
        )
    assert ret["code"] == 0
    assert ret["data"]["model_id"] == "jinaai/jina-clip-v1"
    assert "task_id" in ret["data"]
    assert ret["data"]["estimated_seconds"] == 30
    assert ret["data"]["yaml_path"] == str(yaml_p)


def test_mount_endpoint_propagates_write_error(tmp_path):
    """写 yaml 抛 HTTPException → 直接传出。"""
    from chayuan.server.api_server import image_routes as mod
    from fastapi import HTTPException

    with patch.object(mod, "_write_infinity_model_id",
                      side_effect=HTTPException(404, "no yaml")):
        with pytest.raises(HTTPException) as exc:
            mod.mount_to_infinity_endpoint(
                payload={"model_id": "a/b"},
                user={"id": 1, "role": "admin"},
            )
    assert exc.value.status_code == 404


def test_mount_endpoint_wraps_unknown_error_as_500(tmp_path):
    """非 HTTPException 包成 500。"""
    from chayuan.server.api_server import image_routes as mod
    from fastapi import HTTPException

    with patch.object(mod, "_write_infinity_model_id",
                      side_effect=PermissionError("read-only fs")):
        with pytest.raises(HTTPException) as exc:
            mod.mount_to_infinity_endpoint(
                payload={"model_id": "a/b"},
                user={"id": 1, "role": "admin"},
            )
    assert exc.value.status_code == 500
