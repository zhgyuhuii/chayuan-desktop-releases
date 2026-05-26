"""厂商 / 模型 enabled 双开关 + auto_detect 行为契约测试。

不依赖运行中的 server,直接 mock get_config_platforms 验:
- platform.enabled=False → 该 vendor 所有模型在 get_config_models 中消失
- disabled_models 内的名 → 即使 auto_detect 检测到也不暴露
- type 过滤工作正常
- Ollama auto_detect_models 失败软回落

跑:pytest tests/server/test_model_platforms.py -v
"""
from __future__ import annotations

from unittest import mock

import pytest


def _platforms(*items):
    """快捷:把字面 dict 列表伪装成 get_config_platforms() 返回的 {name: dict} 形态。"""
    return {it["platform_name"]: it for it in items}


@pytest.fixture(autouse=True)
def _clear_caches():
    """每个 case 前清掉 ollama auto-detect 的 60s 缓存,避免互相污染。"""
    from chayuan.server.utils import detect_ollama_models

    detect_ollama_models.cache_clear()  # type: ignore[attr-defined]
    yield


# ──────────────────────────────────────────────────────────────
# 厂商 enabled
# ──────────────────────────────────────────────────────────────

def test_disabled_platform_invisible():
    from chayuan.server import utils

    fake = _platforms(
        {
            "platform_name": "vllm",
            "platform_type": "openai",
            "api_base_url": "http://vllm:8000/v1",
            "enabled": True,
            "auto_detect_model": False,
            "llm_models": ["qwen2.5-14b"],
        },
        {
            "platform_name": "ollama",
            "platform_type": "ollama",
            "api_base_url": "http://127.0.0.1:11434/v1",
            "enabled": False,  # ← 整个被禁
            "auto_detect_model": False,
            "llm_models": ["qwen3:8b", "llama3:8b"],
        },
    )
    # get_config_platforms 已经按 enabled 过滤过 — 模拟它过滤后的结果
    visible = {n: p for n, p in fake.items() if p.get("enabled", True)}
    with mock.patch.object(utils, "get_config_platforms", return_value=visible):
        models = utils.get_config_models(model_type="llm")

    assert "qwen2.5-14b" in models, "vllm 平台启用,模型应可见"
    assert "qwen3:8b" not in models, "ollama 平台被禁,模型不应出现"
    assert "llama3:8b" not in models


# ──────────────────────────────────────────────────────────────
# disabled_models 黑名单
# ──────────────────────────────────────────────────────────────

def test_disabled_models_blacklist():
    from chayuan.server import utils

    fake = _platforms({
        "platform_name": "ollama",
        "platform_type": "ollama",
        "api_base_url": "http://127.0.0.1:11434/v1",
        "enabled": True,
        "auto_detect_model": False,
        "llm_models": ["qwen3:8b", "llama3:8b", "internal-only"],
        "disabled_models": ["internal-only"],  # ← 单条屏蔽
    })
    with mock.patch.object(utils, "get_config_platforms", return_value=fake):
        models = utils.get_config_models(model_type="llm")

    assert "qwen3:8b" in models
    assert "llama3:8b" in models
    assert "internal-only" not in models


# ──────────────────────────────────────────────────────────────
# type 过滤
# ──────────────────────────────────────────────────────────────

def test_model_type_filter():
    from chayuan.server import utils

    fake = _platforms({
        "platform_name": "mixed",
        "platform_type": "openai",
        "api_base_url": "http://x/v1",
        "enabled": True,
        "auto_detect_model": False,
        "llm_models": ["qwen2.5-14b"],
        "embed_models": ["bge-m3"],
        "rerank_models": ["bge-reranker-v2"],
    })
    with mock.patch.object(utils, "get_config_platforms", return_value=fake):
        llms = utils.get_config_models(model_type="llm")
        embeds = utils.get_config_models(model_type="embed")
        reranks = utils.get_config_models(model_type="rerank")

    assert set(llms.keys()) == {"qwen2.5-14b"}
    assert set(embeds.keys()) == {"bge-m3"}
    assert set(reranks.keys()) == {"bge-reranker-v2"}


# ──────────────────────────────────────────────────────────────
# Ollama auto_detect 成功 + 黑名单组合
# ──────────────────────────────────────────────────────────────

def test_ollama_auto_detect_with_blacklist():
    from chayuan.server import utils

    fake = _platforms({
        "platform_name": "ollama",
        "platform_type": "ollama",
        "api_base_url": "http://127.0.0.1:11434/v1",
        "enabled": True,
        "auto_detect_model": True,
        "llm_models": [],   # 全靠 auto_detect
        "embed_models": [],
        "disabled_models": ["secret-v1"],
    })

    fake_tags = {
        "models": [
            {"name": "qwen3:8b"},
            {"name": "llama3:8b"},
            {"name": "secret-v1"},   # 应被黑名单屏蔽
            {"name": "bge-m3"},      # 名字含 bge → 自动归到 embed_models
            {"name": "nomic-embed-text"},  # 同上
        ]
    }

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return fake_tags

    with mock.patch.object(utils, "get_config_platforms", return_value=fake), \
         mock.patch.object(utils.requests, "get", return_value=_Resp()):
        all_models = utils.get_config_models()

    assert "qwen3:8b" in all_models
    assert "llama3:8b" in all_models
    assert "bge-m3" in all_models
    assert "nomic-embed-text" in all_models
    assert "secret-v1" not in all_models, "黑名单应屏蔽 auto_detect 出来的模型"
    # bge-* / nomic-embed-* 应进 embed,不是 llm
    assert all_models["bge-m3"]["model_type"] == "embed"
    assert all_models["qwen3:8b"]["model_type"] == "llm"


# ──────────────────────────────────────────────────────────────
# Ollama auto_detect 连不上 → 软回落到配置 llm_models
# ──────────────────────────────────────────────────────────────

def test_ollama_auto_detect_failure_falls_back():
    from chayuan.server import utils
    import requests as _req

    fake = _platforms({
        "platform_name": "ollama",
        "platform_type": "ollama",
        "api_base_url": "http://nowhere:11434/v1",
        "enabled": True,
        "auto_detect_model": True,
        "llm_models": ["qwen:7b"],  # 配置兜底
        "embed_models": [],
    })

    with mock.patch.object(utils, "get_config_platforms", return_value=fake), \
         mock.patch.object(
             utils.requests, "get",
             side_effect=_req.exceptions.ConnectionError("refused"),
         ):
        models = utils.get_config_models(model_type="llm")

    # 拉不到 ollama,应继续使用配置里的 qwen:7b
    assert "qwen:7b" in models
