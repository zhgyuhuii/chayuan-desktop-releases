"""88-bis 题:install_dialog 配置 tab 应读 ``<CHAYUAN_ROOT>/compose/<service>.yaml``,
不能读全局聚合 ``docker-compose.yaml`` 当真源。

83 题约定:每个 service 独立 yaml(infinity.yaml / vllm.yaml / ...);
全局 docker-compose.yaml 仅历史 fallback。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


def test_compose_config_path_returns_per_service_when_exists(tmp_path):
    """传 service 名时,应优先返该 service 独立 yaml。"""
    from chayuan.server.config_panel import install_dialog as mod

    per_svc = tmp_path / "infinity.yaml"
    per_svc.write_text("services:\n  infinity:\n    image: x\n", encoding="utf-8")

    aggregated = tmp_path / "docker-compose.yaml"
    aggregated.write_text("services: {}\n", encoding="utf-8")

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=per_svc,
    ), patch(
        "chayuan.server.config_panel.compose_manager.ensure_compose_file",
        return_value=aggregated,
    ):
        got = mod._compose_config_path("infinity")
    assert got == per_svc, (
        f"infinity 应读独立 yaml,got={got}"
    )


def test_compose_config_path_falls_back_to_aggregate_when_no_per_service(tmp_path):
    """per-service yaml 不存在 → 回退到全局聚合(老兼容)。"""
    from chayuan.server.config_panel import install_dialog as mod

    aggregated = tmp_path / "docker-compose.yaml"
    aggregated.write_text("services: {}\n", encoding="utf-8")

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=None,
    ), patch(
        "chayuan.server.config_panel.compose_manager.ensure_compose_file",
        return_value=aggregated,
    ):
        got = mod._compose_config_path("infinity")
    assert got == aggregated


def test_compose_config_path_no_service_arg_returns_aggregate(tmp_path):
    """不传 service(老调用)→ 行为不变,返聚合 yaml。"""
    from chayuan.server.config_panel import install_dialog as mod

    aggregated = tmp_path / "docker-compose.yaml"
    aggregated.write_text("services: {}\n", encoding="utf-8")

    with patch(
        "chayuan.server.config_panel.compose_manager.ensure_compose_file",
        return_value=aggregated,
    ):
        got = mod._compose_config_path()
    assert got == aggregated


def test_compose_config_path_all_fail_returns_none():
    """compose_manager 抛异常 → 返 None,不让 UI 崩。"""
    from chayuan.server.config_panel import install_dialog as mod

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        side_effect=RuntimeError("compose dir missing"),
    ):
        got = mod._compose_config_path("infinity")
    assert got is None


def test_compose_config_path_signature_accepts_optional_service():
    """API 兼容:不传参 / 传 None / 传 service 名都可调用。"""
    import inspect
    from chayuan.server.config_panel import install_dialog as mod

    sig = inspect.signature(mod._compose_config_path)
    params = list(sig.parameters.values())
    assert len(params) == 1
    p = params[0]
    assert p.name == "service"
    assert p.default is None, "service 必须是可选参数(默认 None)以保持向后兼容"
