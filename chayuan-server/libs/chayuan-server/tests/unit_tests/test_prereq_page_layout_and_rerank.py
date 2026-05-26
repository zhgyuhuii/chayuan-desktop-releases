"""91 题:服务配置(系统先决条件)页面 4+4 两排布局 + 默认 Rerank 卡片。

排版要求(用户原话):
  第一排:业务库 / Redis / 文件存储 / 文件预览
  第二排:向量库 / 默认 LLM / 默认 Embedding / 默认 Rerank

实现要点:
  1. service_checks.all_checks() 必含 RerankServiceCheck
  2. service_config_page._sort_for_render 按固定顺序排
  3. RerankServiceCheck 行为:USE_RERANKER + RERANKER_MODEL 多组合
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# RerankServiceCheck 行为
# ---------------------------------------------------------------------------

def _stub_settings(*, use_rerank: bool, model: str = ""):
    """构造 mock Settings 对象。"""
    fake_kb = MagicMock()
    fake_kb.USE_RERANKER = use_rerank
    fake_kb.RERANKER_MODEL = model
    fake_settings = MagicMock()
    fake_settings.kb_settings = fake_kb
    return fake_settings


def test_rerank_check_no_model_no_use_returns_ok_disabled():
    """USE_RERANKER=False 且未配模型 → ok=True(降级,不挡用户)。"""
    from chayuan.server.config_panel.service_checks import RerankServiceCheck

    with patch("chayuan.settings.Settings", _stub_settings(use_rerank=False)):
        st = RerankServiceCheck().probe()
    assert st.ok is True
    assert st.configured is False
    assert "未启用重排" in st.detail


def test_rerank_check_use_true_no_model_returns_failed():
    """USE_RERANKER=True 但缺模型 → ok=False,标黄提醒。"""
    from chayuan.server.config_panel.service_checks import RerankServiceCheck

    with patch("chayuan.settings.Settings", _stub_settings(use_rerank=True)):
        st = RerankServiceCheck().probe()
    assert st.ok is False
    assert st.configured is False
    assert "USE_RERANKER" in st.detail


def test_rerank_check_model_in_available_returns_ok():
    """RERANKER_MODEL 在 get_config_models 返回里 → ok=True。"""
    from chayuan.server.config_panel.service_checks import RerankServiceCheck

    with patch(
        "chayuan.settings.Settings",
        _stub_settings(use_rerank=True, model="bge-reranker-v2-m3"),
    ), patch(
        "chayuan.server.utils.get_config_models",
        return_value={
            "bge-reranker-v2-m3": {"platform_name": "infinity-local"},
        },
    ):
        st = RerankServiceCheck().probe()
    assert st.ok is True
    assert st.configured is True
    assert st.endpoint == "bge-reranker-v2-m3"
    assert "infinity-local" in st.detail


def test_rerank_check_model_not_in_available_returns_failed():
    """配了 RERANKER_MODEL 但不在可用列表 → ok=False,detail 给提示。"""
    from chayuan.server.config_panel.service_checks import RerankServiceCheck

    with patch(
        "chayuan.settings.Settings",
        _stub_settings(use_rerank=True, model="ghost-model"),
    ), patch(
        "chayuan.server.utils.get_config_models",
        return_value={"bge-reranker-v2-m3": {}},
    ):
        st = RerankServiceCheck().probe()
    assert st.ok is False
    assert "ghost-model" in st.detail
    assert "bge-reranker-v2-m3" in st.detail


def test_rerank_check_get_config_models_raises_returns_friendly_error():
    """get_config_models 抛异常 → ok=False,但 probe 不抛,UI 不崩。"""
    from chayuan.server.config_panel.service_checks import RerankServiceCheck

    with patch(
        "chayuan.settings.Settings",
        _stub_settings(use_rerank=True, model="bge-reranker-v2-m3"),
    ), patch(
        "chayuan.server.utils.get_config_models",
        side_effect=RuntimeError("model_settings.yaml not found"),
    ):
        st = RerankServiceCheck().probe()
    assert st.ok is False
    assert "RuntimeError" in st.detail


def test_rerank_check_level_is_recommended():
    """rerank 是 recommended 级,不挡 system_ready。"""
    from chayuan.server.config_panel.service_checks import RerankServiceCheck
    assert RerankServiceCheck.level == "recommended"


# ---------------------------------------------------------------------------
# all_checks 包含 RerankServiceCheck
# ---------------------------------------------------------------------------

def test_all_checks_contains_rerank():
    from chayuan.server.config_panel.service_checks import (
        all_checks, RerankServiceCheck,
    )
    services = [type(c) for c in all_checks()]
    assert RerankServiceCheck in services


def test_all_checks_unique_service_ids():
    """不能有重复 service id(否则 _sort_for_render 会丢卡片)。"""
    from chayuan.server.config_panel.service_checks import all_checks
    ids = [c.service for c in all_checks()]
    assert len(ids) == len(set(ids)), f"重复 service id: {ids}"


# ---------------------------------------------------------------------------
# 排序逻辑
# ---------------------------------------------------------------------------

def _mk_status(service: str, label: str = ""):
    from chayuan.server.config_panel.service_checks import ServiceStatus
    return ServiceStatus(
        service=service, label=label or service.upper(),
        level="critical", ok=True, configured=True,
        endpoint="", detail="",
    )


def test_sort_for_render_first_row_basics_second_row_models():
    """8 项齐全 → 严格按 业务库/Redis/文件存储/文件预览/向量库/LLM/Embed/Rerank 排。"""
    from chayuan.server.config_panel.service_config_page import _sort_for_render

    # 故意打乱顺序 + 下游(probe 顺序未必固定)
    statuses = [
        _mk_status("rerank"), _mk_status("redis"), _mk_status("vs"),
        _mk_status("llm"), _mk_status("kkfileview"), _mk_status("db"),
        _mk_status("embed"), _mk_status("minio"),
    ]
    out = _sort_for_render(statuses)
    ordered = [s.service for s in out]
    assert ordered == [
        "db", "redis", "minio", "kkfileview",
        "vs", "llm", "embed", "rerank",
    ]


def test_sort_for_render_handles_missing_services():
    """部分 check 未注册时(如老分支无 rerank),不报错,顺序保持。"""
    from chayuan.server.config_panel.service_config_page import _sort_for_render

    statuses = [
        _mk_status("redis"), _mk_status("db"), _mk_status("llm"),
        _mk_status("embed"), _mk_status("vs"),
    ]
    out = _sort_for_render(statuses)
    ordered = [s.service for s in out]
    assert ordered == ["db", "redis", "vs", "llm", "embed"]


def test_sort_for_render_appends_unknown_services_to_end():
    """未来加新 service(比如 'observability') → 不掉,追加在末尾。"""
    from chayuan.server.config_panel.service_config_page import _sort_for_render

    statuses = [
        _mk_status("db"), _mk_status("observability"),
        _mk_status("llm"), _mk_status("custom_x"),
    ]
    out = _sort_for_render(statuses)
    ordered = [s.service for s in out]
    # 已知项按 DESIRED_ORDER,未知项按原顺序追加
    assert ordered[:2] == ["db", "llm"]
    assert set(ordered[2:]) == {"observability", "custom_x"}


def test_sort_for_render_empty_list():
    from chayuan.server.config_panel.service_config_page import _sort_for_render
    assert _sort_for_render([]) == []


def test_jump_target_for_rerank_points_to_defaults_tab():
    """rerank 的 jump_target 应跳 ④ 默认模型 tab。"""
    import inspect
    from chayuan.server.config_panel import service_config_page as mod

    src = inspect.getsource(mod._render_topology)
    # 字符串硬编码在 jump_targets dict 里
    assert '"rerank": "file:model_settings.yaml#defaults"' in src
