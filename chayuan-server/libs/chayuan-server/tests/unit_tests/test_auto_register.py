"""auto_register 单元测试 (Phase 3)。

策略:
  * mock httpx 调用 /v1/models
  * 用临时 yaml 验证 model_settings.yaml 写入正确
  * 验证 skip 逻辑(onlyoffice 类纯服务)
  * 验证模型分类(关键字推断)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from chayuan.server.config_panel.auto_register import (
    RegisterReport,
    _SERVICE_CAPABILITIES,
    _classify_models,
    _write_to_model_settings,
    register_after_healthy,
)


# ============================================================================
# _classify_models
# ============================================================================


def test_classify_embedding_by_keyword():
    res = _classify_models(
        [{"id": "BAAI/bge-large-zh-v1.5"}, {"id": "jina-embeddings-v3"}],
        ["embedding", "rerank"],
    )
    assert "BAAI/bge-large-zh-v1.5" in res["embedding"]
    assert "jina-embeddings-v3" in res["embedding"]
    assert res["rerank"] == []


def test_classify_rerank_by_keyword():
    res = _classify_models(
        [{"id": "bge-reranker-v2-m3"}, {"id": "jina-rerank-v1"}],
        ["embedding", "rerank"],
    )
    assert "bge-reranker-v2-m3" in res["rerank"]
    assert "jina-rerank-v1" in res["rerank"]


def test_classify_chat_models():
    res = _classify_models(
        [{"id": "qwen2.5-72b-instruct"}, {"id": "llama-3-chat"}],
        ["chat"],
    )
    assert len(res["chat"]) == 2


def test_classify_unknown_falls_back_to_first_capability():
    """看不出关键字时归到 service 的第一个 capability。"""
    res = _classify_models(
        [{"id": "some-random-name"}],
        ["embedding", "rerank"],
    )
    assert "some-random-name" in res["embedding"]


def test_classify_skips_models_without_id():
    res = _classify_models(
        [{"id": ""}, {"object": "model"}, {"id": "valid"}],
        ["chat"],
    )
    # 只有 valid 进
    assert res["chat"] == ["valid"]


# ============================================================================
# _write_to_model_settings
# ============================================================================


@pytest.fixture
def tmp_chayuan_root(tmp_path, monkeypatch):
    """临时 CHAYUAN_ROOT,带初始 model_settings.yaml。"""
    root = tmp_path / "chayuan_data"
    root.mkdir()
    yaml_file = root / "model_settings.yaml"
    yaml_file.write_text(yaml.safe_dump({
        "MODEL_PLATFORMS": [],
        "DEFAULT_LLM_MODEL": "",
        "DEFAULT_EMBEDDING_MODEL": "",
    }, allow_unicode=True))

    monkeypatch.setattr(
        "chayuan.settings.CHAYUAN_ROOT", root,
    )
    return root


def test_write_adds_new_platform(tmp_chayuan_root):
    """新装时,platform 不存在 → 添加。"""
    added, defaults = _write_to_model_settings(
        service="infinity",
        api_base_url="http://127.0.0.1:37997",
        models_by_cap={"embedding": ["bge-large-zh"], "rerank": ["bge-rerank"]},
    )
    assert added is True

    # 读回验证
    yaml_file = tmp_chayuan_root / "model_settings.yaml"
    doc = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    plats = doc["MODEL_PLATFORMS"]
    assert len(plats) == 1
    p = plats[0]
    assert p["platform_name"] == "docker:infinity"
    assert p["api_base_url"] == "http://127.0.0.1:37997/v1"
    assert p["api_key"] == "EMPTY"
    assert p["embed_models"] == ["bge-large-zh"]
    assert p["rerank_models"] == ["bge-rerank"]
    assert p["source"] == "docker:infinity"


def test_write_updates_existing_platform(tmp_chayuan_root):
    """二次注册同 service → 覆盖,不重复。"""
    # 先写一遍
    _write_to_model_settings(
        service="infinity",
        api_base_url="http://127.0.0.1:37997",
        models_by_cap={"embedding": ["old-model"]},
    )
    # 再写一遍(模拟用户重启容器,拉到新模型清单)
    added, _ = _write_to_model_settings(
        service="infinity",
        api_base_url="http://127.0.0.1:37997",
        models_by_cap={"embedding": ["new-model-1", "new-model-2"]},
    )
    assert added is False  # 是 update,不是 add

    yaml_file = tmp_chayuan_root / "model_settings.yaml"
    doc = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    plats = doc["MODEL_PLATFORMS"]
    assert len(plats) == 1  # 没重复
    assert plats[0]["embed_models"] == ["new-model-1", "new-model-2"]


def test_write_sets_default_when_empty(tmp_chayuan_root):
    """DEFAULT_EMBEDDING_MODEL 为空 → 自动设第一个新模型。"""
    _, defaults = _write_to_model_settings(
        service="infinity",
        api_base_url="http://127.0.0.1:37997",
        models_by_cap={"embedding": ["bge-large-zh"]},
    )
    assert "embedding" in defaults
    assert defaults["embedding"] == "bge-large-zh"

    yaml_file = tmp_chayuan_root / "model_settings.yaml"
    doc = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    assert doc["DEFAULT_EMBEDDING_MODEL"] == "bge-large-zh"


def test_write_does_not_overwrite_existing_default(tmp_chayuan_root):
    """用户已设过的 default 不被覆盖。"""
    yaml_file = tmp_chayuan_root / "model_settings.yaml"
    doc = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    doc["DEFAULT_EMBEDDING_MODEL"] = "user-chosen-model"
    yaml_file.write_text(yaml.safe_dump(doc, allow_unicode=True))

    _, defaults = _write_to_model_settings(
        service="infinity",
        api_base_url="http://127.0.0.1:37997",
        models_by_cap={"embedding": ["bge-large-zh"]},
    )
    # 不在 defaults_set 里(因为已有非空值)
    assert "embedding" not in defaults

    doc2 = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    assert doc2["DEFAULT_EMBEDDING_MODEL"] == "user-chosen-model"


# ============================================================================
# register_after_healthy 整合
# ============================================================================


@pytest.mark.asyncio
async def test_register_skips_non_model_service():
    """onlyoffice / 不在 _SERVICE_CAPABILITIES 表里的 → skip。"""
    rpt = await register_after_healthy("onlyoffice", write_yaml=False)
    assert rpt.ok
    assert rpt.skipped_reason
    assert "不提供" in rpt.skipped_reason


@pytest.mark.asyncio
async def test_register_skips_unknown_service():
    rpt = await register_after_healthy("unknown-service", write_yaml=False)
    assert rpt.ok
    assert rpt.skipped_reason


@pytest.mark.asyncio
async def test_register_returns_url_failure_when_compose_lookup_fails(monkeypatch):
    """compose_manager 没找到 host_port → ok=False。"""
    async def _fake_resolve(*_a, **_kw):
        return ""
    monkeypatch.setattr(
        "chayuan.server.config_panel.auto_register._resolve_endpoint_url",
        _fake_resolve,
    )

    rpt = await register_after_healthy("infinity", write_yaml=False)
    assert not rpt.ok
    assert any("endpoint" in e for e in rpt.errors)


@pytest.mark.asyncio
async def test_register_full_happy_path(monkeypatch, tmp_chayuan_root):
    """端到端:resolve URL → fetch /v1/models → 写 yaml → 设默认。"""

    # mock endpoint resolution
    async def _fake_resolve(svc):
        return "http://127.0.0.1:37997"
    monkeypatch.setattr(
        "chayuan.server.config_panel.auto_register._resolve_endpoint_url",
        _fake_resolve,
    )

    # mock /v1/models
    async def _fake_fetch(url, timeout=5.0):
        return [
            {"id": "BAAI/bge-large-zh-v1.5"},
            {"id": "BAAI/bge-reranker-v2-m3"},
        ]
    monkeypatch.setattr(
        "chayuan.server.config_panel.auto_register._fetch_openai_models",
        _fake_fetch,
    )

    # mock runtime reload(避免依赖)
    monkeypatch.setattr(
        "chayuan.server.config_panel.auto_register._reload_chayuan_runtime_registry",
        lambda: None,
    )

    rpt = await register_after_healthy("infinity")
    assert rpt.ok
    assert rpt.platform_added
    assert rpt.platform_name == "docker:infinity"
    assert rpt.api_base_url == "http://127.0.0.1:37997"
    assert rpt.models_discovered == 2
    assert "BAAI/bge-large-zh-v1.5" in rpt.models_by_capability["embedding"]
    assert "BAAI/bge-reranker-v2-m3" in rpt.models_by_capability["rerank"]
    # 自动设默认
    assert rpt.defaults_set.get("embedding") == "BAAI/bge-large-zh-v1.5"

    # yaml 已落盘
    yaml_file = tmp_chayuan_root / "model_settings.yaml"
    doc = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    assert doc["DEFAULT_EMBEDDING_MODEL"] == "BAAI/bge-large-zh-v1.5"


# ============================================================================
# RegisterReport.summary
# ============================================================================


def test_report_summary_skipped():
    r = RegisterReport(service="onlyoffice", ok=True,
                       skipped_reason="不提供 OpenAI API")
    s = r.summary()
    assert "跳过" in s and "onlyoffice" in s


def test_report_summary_failed():
    r = RegisterReport(service="infinity", ok=False,
                       errors=["pull failed", "x"])
    s = r.summary()
    assert "失败" in s and "pull failed" in s


def test_report_summary_success_with_defaults():
    r = RegisterReport(
        service="infinity", ok=True,
        api_base_url="http://127.0.0.1:37997",
        models_discovered=12,
        defaults_set={"embedding": "bge-large-zh"},
    )
    s = r.summary()
    assert "✓" in s and "infinity" in s
    assert "12" in s
    assert "bge-large-zh" in s
