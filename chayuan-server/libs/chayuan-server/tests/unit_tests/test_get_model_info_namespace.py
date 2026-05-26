"""79 题:get_model_info 解析 model_name 中的 ``platform::model`` 命名空间。

跨平台同名模型(deepseek + baidu-qianfan 都有 deepseek-v4-flash)时,
client 必须传 ``deepseek::deepseek-v4-flash`` 让 server 精确路由,
不被 dict 覆盖路由到错误平台。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def _fake_platforms_with_collision():
    """构造跨平台同名 deepseek-v4-flash 的 platforms。"""
    return {
        "deepseek": {
            "platform_name": "deepseek",
            "platform_type": "openai",
            "enabled": True,
            "api_base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-deepseek",
            "auto_detect_model": False,
            "llm_models": ["deepseek-v4-flash", "deepseek-v4-pro"],
            "embed_models": [], "rerank_models": [],
            "text2image_models": [], "image2text_models": [],
            "image2image_models": [],
            "speech2text_models": [], "text2speech_models": [],
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
            "embed_models": [], "rerank_models": [],
            "text2image_models": [], "image2text_models": [],
            "image2image_models": [],
            "speech2text_models": [], "text2speech_models": [],
            "disabled_models": [],
        },
    }


def test_get_model_info_namespace_routes_to_explicit_platform(monkeypatch):
    """``deepseek::deepseek-v4-flash`` → 精确选 deepseek 平台,不被 baidu-qianfan 覆盖。"""
    from chayuan.server import utils

    monkeypatch.setattr(
        utils, "get_config_platforms", _fake_platforms_with_collision,
    )

    info = utils.get_model_info("deepseek::deepseek-v4-flash")
    assert info, "应找到模型"
    assert info.get("platform_name") == "deepseek", (
        f"应路由到 deepseek 平台,实际: {info.get('platform_name')}"
    )
    # api 凭据也是 deepseek 的,不是 baidu-qianfan
    assert info.get("api_base_url") == "https://api.deepseek.com/v1"
    assert info.get("api_key") == "sk-deepseek"


def test_get_model_info_no_namespace_legacy_behavior(monkeypatch):
    """无 ``::`` 的旧 model_name → 走 dict 覆盖路径(取一个,通常是后到的 platform)。"""
    from chayuan.server import utils

    monkeypatch.setattr(
        utils, "get_config_platforms", _fake_platforms_with_collision,
    )

    info = utils.get_model_info("deepseek-v4-flash")
    assert info
    # 旧行为:dict 覆盖,baidu-qianfan 后到覆盖 deepseek
    assert info.get("platform_name") in ("deepseek", "baidu-qianfan")


def test_get_model_info_namespace_explicit_platform_arg_wins(monkeypatch):
    """显式 platform_name 参数优先于 model_name 内嵌的命名空间。"""
    from chayuan.server import utils

    monkeypatch.setattr(
        utils, "get_config_platforms", _fake_platforms_with_collision,
    )

    info = utils.get_model_info(
        "baidu-qianfan::deepseek-v4-flash",
        platform_name="deepseek",  # 显式覆盖
    )
    assert info.get("platform_name") == "deepseek"


def test_get_model_info_namespace_unknown_platform_returns_empty(monkeypatch):
    """命名空间指定不存在的 platform → 返空 dict(不 fallback 到其他)。"""
    from chayuan.server import utils

    monkeypatch.setattr(
        utils, "get_config_platforms", _fake_platforms_with_collision,
    )

    info = utils.get_model_info("nonexistent-platform::deepseek-v4-flash")
    assert info == {}


def test_get_model_info_namespace_empty_after_split(monkeypatch):
    """``::deepseek-v4-flash``(空 platform)→ 走原 model_name 行为,不抛。"""
    from chayuan.server import utils

    monkeypatch.setattr(
        utils, "get_config_platforms", _fake_platforms_with_collision,
    )

    info = utils.get_model_info("::deepseek-v4-flash")
    # 空 platform 跳过解析,直接用整个字符串作 model_name 找
    # 找不到 "::deepseek-v4-flash" 这个 id,返空
    assert info == {} or "platform_name" in info  # 行为宽容,不抛
