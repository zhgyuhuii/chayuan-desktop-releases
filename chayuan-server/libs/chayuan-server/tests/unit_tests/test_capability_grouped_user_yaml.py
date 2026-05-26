"""78 题:深度排查 — 用户的真实 yaml 顺序下,_capability_grouped 是否返了
"云 · deepseek" 分组及其 2 个 llm_models。
"""
from __future__ import annotations

import json

import pytest


def _doc_to_platforms_dict(doc):
    """78 题架构修复后:_capability_grouped 走 get_config_platforms()(平铺过滤
    enabled=True 后的 dict {pname: pinfo});测试需要 mock 这个函数。"""
    raw = doc.get("MODEL_PLATFORMS") or []
    return {
        p["platform_name"]: p
        for p in raw
        if isinstance(p, dict) and p.get("enabled", True)
    }


def test_capability_grouped_with_real_user_platform_order(monkeypatch):
    """模拟用户真实 yaml(dump 显示):
    [bailian, deepseek, zhipu, ollama, baidu-qianfan, oneapi]
    deepseek 排第 2,baidu-qianfan 第 5,deepseek 应在 chat 下出现.
    """
    from chayuan.server.config_panel import yaml_store
    from chayuan.server.config_panel.runtime_framework_panel import (
        _capability_grouped,
    )

    fake_doc = {
        "MODEL_PLATFORMS": [
            {
                "platform_name": "bailian",
                "platform_type": "openai",
                "enabled": True,
                "api_key": "sk-bailian",
                "auto_detect_model": False,
                "llm_models": ["qwen-max", "qwen-plus", "deepseek-v3.1"],  # bailian 也有部分 deepseek 名字
                "embed_models": [], "rerank_models": [],
                "text2image_models": [], "image2text_models": [],
                "speech2text_models": [], "text2speech_models": [],
            },
            {
                "platform_name": "deepseek",
                "platform_type": "openai",
                "enabled": True,
                "api_key": "sk-deepseek",
                "auto_detect_model": False,
                "llm_models": ["deepseek-v4-flash", "deepseek-v4-pro"],
                "embed_models": [], "rerank_models": [],
                "text2image_models": [], "image2text_models": [],
                "speech2text_models": [], "text2speech_models": [],
            },
            {
                "platform_name": "zhipu",
                "platform_type": "zhipu",
                "enabled": True,
                "api_key": "sk-zhipu",
                "auto_detect_model": False,
                "llm_models": ["glm-4.5", "glm-4.6"],
                "embed_models": [], "rerank_models": [],
                "text2image_models": [], "image2text_models": [],
                "speech2text_models": [], "text2speech_models": [],
            },
            {
                "platform_name": "ollama",
                "platform_type": "ollama",
                "enabled": True,
                "api_key": "EMPTY",  # ollama 通常 EMPTY
                "auto_detect_model": True,
                "llm_models": ["qwen:7b", "qwen2:7b", "qwen3.5:4b"],
                "embed_models": [], "rerank_models": [],
                "text2image_models": [], "image2text_models": [],
                "speech2text_models": [], "text2speech_models": [],
            },
            {
                "platform_name": "baidu-qianfan",
                "platform_type": "openai",
                "enabled": True,
                "api_key": "qf-key",
                "auto_detect_model": False,
                # 注意:baidu 也有 deepseek-v4-flash / deepseek-v4-pro
                "llm_models": ["ernie-4.0-8k", "deepseek-v4-flash", "deepseek-v4-pro"],
                "embed_models": [], "rerank_models": [],
                "text2image_models": [], "image2text_models": [],
                "speech2text_models": [], "text2speech_models": [],
            },
            {
                "platform_name": "oneapi",
                "platform_type": "openai",
                "enabled": True,
                "api_key": "oneapi-key",
                "auto_detect_model": False,
                "llm_models": ["qwen-max", "ERNIE-Bot"],
                "embed_models": [], "rerank_models": [],
                "text2image_models": [], "image2text_models": [],
                "speech2text_models": [], "text2speech_models": [],
            },
        ],
    }

    from chayuan.server import utils as _utils
    monkeypatch.setattr(
        _utils, "get_config_platforms",
        lambda: _doc_to_platforms_dict(fake_doc),
    )

    grouped = _capability_grouped()
    chat = grouped.get("chat", {})

    print("\n=== chat 分组实际返值 ===")
    for label, models in chat.items():
        print(f"  {label} → {[m for m, _ in models]}")

    # 关键断言 1:有"云 · deepseek"分组
    assert "云 · deepseek" in chat, (
        f"chat 应有 '云 · deepseek' 分组,实际所有: {list(chat.keys())}"
    )
    deepseek_models = [m for m, _ in chat["云 · deepseek"]]
    # 关键断言 2:deepseek 自己的 2 个模型应在它的分组里
    assert "deepseek-v4-flash" in deepseek_models, (
        f"deepseek 分组应有 deepseek-v4-flash,实际: {deepseek_models}"
    )
    assert "deepseek-v4-pro" in deepseek_models, (
        f"deepseek 分组应有 deepseek-v4-pro,实际: {deepseek_models}"
    )

    # 断言 3:78 题架构修复后,**每个平台保留自己的模型**(与 /v1/models 同源行为)
    # baidu-qianfan 也有 deepseek-v4-flash → 它的分组也应有
    baidu_models = [m for m, _ in chat.get("云 · baidu-qianfan", [])]
    assert "deepseek-v4-flash" in baidu_models, (
        "baidu-qianfan 自己 yaml 配了 deepseek-v4-flash,应在它的分组里"
    )
    assert "deepseek-v4-pro" in baidu_models, (
        "baidu-qianfan 自己 yaml 配了 deepseek-v4-pro,应在它的分组里"
    )
    assert "ernie-4.0-8k" in baidu_models, "baidu 应保留自己的非冲突模型"


def test_capability_grouped_chat_is_for_llm_models_field(monkeypatch):
    """sanity:_CAP_TO_GROUP_KEY['chat'] = 'llm_models',从 yaml 的 llm_models 字段读。"""
    from chayuan.server.config_panel.runtime_framework_panel import (
        _CAP_TO_GROUP_KEY,
    )
    assert _CAP_TO_GROUP_KEY["chat"] == "llm_models"


def test_capability_grouped_skips_disabled(monkeypatch):
    """禁用厂商不应出现在分组中。"""
    from chayuan.server.config_panel import yaml_store
    from chayuan.server.config_panel.runtime_framework_panel import (
        _capability_grouped,
    )

    fake_doc = {
        "MODEL_PLATFORMS": [
            {
                "platform_name": "deepseek",
                "platform_type": "openai",
                "enabled": False,  # 禁用!
                "api_key": "sk-key",
                "llm_models": ["deepseek-v4-flash"],
                "embed_models": [], "rerank_models": [],
                "text2image_models": [], "image2text_models": [],
                "speech2text_models": [], "text2speech_models": [],
            },
        ],
    }
    from chayuan.server import utils as _utils
    monkeypatch.setattr(
        _utils, "get_config_platforms",
        lambda: _doc_to_platforms_dict(fake_doc),
    )

    grouped = _capability_grouped()
    assert "云 · deepseek" not in grouped.get("chat", {})


def test_capability_grouped_skips_empty_api_key(monkeypatch):
    """api_key 空 → 不出现。"""
    from chayuan.server.config_panel import yaml_store
    from chayuan.server.config_panel.runtime_framework_panel import (
        _capability_grouped,
    )

    fake_doc = {
        "MODEL_PLATFORMS": [
            {
                "platform_name": "deepseek",
                "platform_type": "openai",
                "enabled": True,
                "api_key": "",  # 空!
                "llm_models": ["deepseek-v4-flash"],
                "embed_models": [], "rerank_models": [],
                "text2image_models": [], "image2text_models": [],
                "speech2text_models": [], "text2speech_models": [],
            },
        ],
    }
    from chayuan.server import utils as _utils
    monkeypatch.setattr(
        _utils, "get_config_platforms",
        lambda: _doc_to_platforms_dict(fake_doc),
    )

    grouped = _capability_grouped()
    assert "云 · deepseek" not in grouped.get("chat", {})
