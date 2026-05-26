"""73 题:/v1/models 跨平台同名模型不应互相覆盖。

回归 bug:之前用 get_config_models() 的 ``Dict[model_id, info]``,
百度千帆和 deepseek 同时配 ``deepseek-v4-flash`` 时,后者的 entry 会
被 baidu-qianfan 覆盖,deepseek 自己的模型一个不剩。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_list_models_keeps_same_id_under_different_platforms():
    """两个不同 platform 都配同名模型 → 各自独立出现在 /v1/models 响应中。"""
    from chayuan.server.api_server import openai_routes

    fake_platforms = {
        "deepseek": {
            "platform_name": "deepseek",
            "platform_type": "openai",
            "enabled": True,
            "api_base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-deepseek",
            "auto_detect_model": False,
            "llm_models": ["deepseek-v4-flash", "deepseek-v4-pro"],
            "embed_models": [],
            "rerank_models": [],
            "text2image_models": [],
            "image2text_models": [],
            "speech2text_models": [],
            "text2speech_models": [],
            "disabled_models": [],
        },
        "baidu-qianfan": {
            "platform_name": "baidu-qianfan",
            "platform_type": "openai",
            "enabled": True,
            "api_base_url": "https://qianfan.baidubce.com/v2",
            "api_key": "qf-key",
            "auto_detect_model": False,
            "llm_models": ["deepseek-v4-flash", "deepseek-v4-pro", "ernie-3.5-8k"],
            "embed_models": [],
            "rerank_models": [],
            "text2image_models": [],
            "image2text_models": [],
            "speech2text_models": [],
            "text2speech_models": [],
            "disabled_models": [],
        },
    }

    with patch.object(openai_routes, "get_config_platforms",
                      return_value=fake_platforms):
        result = await openai_routes.list_models(type="llm", check_access=False)

    data = result["data"]
    # 关键断言:deepseek 平台和 baidu-qianfan 平台都应有 deepseek-v4-flash 条目
    deepseek_entries = [m for m in data if m["platform_name"] == "deepseek"]
    baidu_entries = [m for m in data if m["platform_name"] == "baidu-qianfan"]

    deepseek_ids = sorted(m["id"] for m in deepseek_entries)
    baidu_ids = sorted(m["id"] for m in baidu_entries)

    assert deepseek_ids == ["deepseek-v4-flash", "deepseek-v4-pro"], (
        f"deepseek 应保留自己的两个模型,实际: {deepseek_ids}"
    )
    assert baidu_ids == ["deepseek-v4-flash", "deepseek-v4-pro", "ernie-3.5-8k"], (
        f"baidu-qianfan 应有 3 个模型,实际: {baidu_ids}"
    )

    # 总条数:2 (deepseek) + 3 (baidu) = 5
    assert len(data) == 5


@pytest.mark.asyncio
async def test_list_models_filters_by_type():
    """?type=embed 只返 embed 字段的模型。"""
    from chayuan.server.api_server import openai_routes

    fake_platforms = {
        "openai": {
            "platform_name": "openai",
            "platform_type": "openai",
            "enabled": True,
            "auto_detect_model": False,
            "llm_models": ["gpt-4o"],
            "embed_models": ["text-embedding-3-small"],
            "rerank_models": [],
            "text2image_models": [],
            "image2text_models": [],
            "speech2text_models": [],
            "text2speech_models": [],
            "disabled_models": [],
        },
    }

    with patch.object(openai_routes, "get_config_platforms",
                      return_value=fake_platforms):
        result = await openai_routes.list_models(type="embed", check_access=False)

    ids = [m["id"] for m in result["data"]]
    assert ids == ["text-embedding-3-small"]
    assert all(m["model_type"] == "embed" for m in result["data"])


@pytest.mark.asyncio
async def test_list_models_skips_disabled_models():
    """disabled_models 中的模型不应出现。"""
    from chayuan.server.api_server import openai_routes

    fake_platforms = {
        "openai": {
            "platform_name": "openai",
            "platform_type": "openai",
            "enabled": True,
            "auto_detect_model": False,
            "llm_models": ["gpt-4o", "gpt-3.5-turbo"],
            "embed_models": [],
            "rerank_models": [],
            "text2image_models": [],
            "image2text_models": [],
            "speech2text_models": [],
            "text2speech_models": [],
            "disabled_models": ["gpt-3.5-turbo"],  # 拉黑
        },
    }

    with patch.object(openai_routes, "get_config_platforms",
                      return_value=fake_platforms):
        result = await openai_routes.list_models(type="llm", check_access=False)

    ids = [m["id"] for m in result["data"]]
    assert ids == ["gpt-4o"]


@pytest.mark.asyncio
async def test_list_models_no_platforms():
    """没有任何启用平台 → 空 data。"""
    from chayuan.server.api_server import openai_routes
    with patch.object(openai_routes, "get_config_platforms", return_value={}):
        result = await openai_routes.list_models(type="llm", check_access=False)
    assert result == {"object": "list", "data": []}
