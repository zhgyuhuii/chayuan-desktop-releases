"""89-10:Dashboard 一键引导横幅 _check_image_embedder_needs_onboarding。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_no_banner_when_infinity_yaml_missing():
    """没装 Infinity → 不引导。"""
    from chayuan.server.config_panel import dashboard as mod

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=None,
    ):
        need, _reason = mod._check_image_embedder_needs_onboarding()
    assert need is False


def test_no_banner_when_infinity_client_healthy(tmp_path):
    """Infinity 已装且 client 在线 → 不引导。"""
    from chayuan.server.config_panel import dashboard as mod

    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text("services:\n  infinity:\n    image: x\n", encoding="utf-8")

    fake_cli = MagicMock()
    fake_cli.kind = "infinity"
    fake_cli.healthcheck = MagicMock(return_value=True)

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ), patch(
        "chayuan.server.image_source.embedder.get_client", return_value=fake_cli,
    ):
        need, _r = mod._check_image_embedder_needs_onboarding()
    assert need is False


def test_no_banner_when_inproc_healthy_already(tmp_path):
    """已 fallback 到 in-proc 且健康 → 不打扰。"""
    from chayuan.server.config_panel import dashboard as mod

    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text("services:\n  infinity:\n    image: x\n", encoding="utf-8")

    fake_cli = MagicMock()
    fake_cli.kind = "inproc"
    fake_cli.healthcheck = MagicMock(return_value=True)

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ), patch(
        "chayuan.server.image_source.embedder.get_client", return_value=fake_cli,
    ):
        need, _r = mod._check_image_embedder_needs_onboarding()
    assert need is False


def test_banner_when_yaml_present_but_client_unavailable(tmp_path):
    """Infinity yaml 在但 get_client 抛 → 引导。"""
    from chayuan.server.config_panel import dashboard as mod
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text("services:\n  infinity:\n    image: x\n", encoding="utf-8")

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ), patch(
        "chayuan.server.image_source.embedder.get_client",
        side_effect=EmbedderUnavailable("nothing"),
    ):
        need, reason = mod._check_image_embedder_needs_onboarding()
    assert need is True
    assert "Infinity" in reason


def test_banner_when_infinity_client_unhealthy(tmp_path):
    """client 是 infinity 但 healthcheck=False → 引导(说明容器没起来)。"""
    from chayuan.server.config_panel import dashboard as mod

    yaml_p = tmp_path / "infinity.yaml"
    yaml_p.write_text("services:\n  infinity:\n    image: x\n", encoding="utf-8")

    fake_cli = MagicMock()
    fake_cli.kind = "infinity"
    fake_cli.healthcheck = MagicMock(return_value=False)

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        return_value=yaml_p,
    ), patch(
        "chayuan.server.image_source.embedder.get_client", return_value=fake_cli,
    ):
        need, _r = mod._check_image_embedder_needs_onboarding()
    assert need is True


def test_check_swallows_compose_manager_error():
    """compose_manager 抛 → 视为没装,不引导(避免错误升级)。"""
    from chayuan.server.config_panel import dashboard as mod

    with patch(
        "chayuan.server.config_panel.compose_manager.get_compose_file_for_service",
        side_effect=RuntimeError("compose dir broken"),
    ):
        need, _r = mod._check_image_embedder_needs_onboarding()
    assert need is False
