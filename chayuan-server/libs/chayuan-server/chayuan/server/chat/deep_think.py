"""根据模型族 + 用户的"深度思考"开关,计算注入给上游 LLM 的扩展参数。

为什么是模型级
==============
"深度思考"在各家上游协议里的名字 / 形态完全不同:

  - 阿里 Qwen3 / Qwen-Plus / Qwen-Max(2025+):
      extra_body = {"enable_thinking": True}
      触发后输出里会有 ``reasoning_content`` 字段(思维链)
  - Anthropic Claude(Sonnet 3.7+ / Opus 4+):
      extra_body = {"thinking": {"type": "enabled", "budget_tokens": 8000}}
      响应里 content 数组包含 ``type: thinking`` block
  - OpenAI o1 / o3 / o4 系列:
      推理是**模型内置**,API 调用方不传额外参数;开关其实是"换模型"
      —— 用户应该直接选 ``o1-preview`` / ``o3-mini`` 而不是给 gpt-4o 开 deep_think
  - DeepSeek:
      ``deepseek-reasoner`` 自带 reasoning;``deepseek-chat`` 不支持 → 让用户
      明确换模型,我们不在这一层悄悄帮换
  - 字节豆包 / 智谱 / Moonshot:
      暂不公开支持参数;保持现状

不能命中上面任何分支的模型,deep_think 直接 noop —— UI 上 pill 仍能按,但
传到上游就丢了。我们 log 一条 INFO 避免静默(用户疑惑"为啥没生效")。

设计要点
========
- 返回 ``{}`` 表示无 extra,调用方 spread 进 model_kwargs 即可
- 模型名带 ``platform::`` 前缀的先剥掉
- 大小写不敏感:用户在 model_platform 里写 ``Qwen-Max`` / ``QWEN-MAX`` 都识别
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("chayuan.chat.deep_think")


# Anthropic 思考预算 — 8k token 是文档默认推荐,够长推理同时不爆出 max_tokens
_ANTHROPIC_BUDGET_TOKENS = 8000


def _normalize(model_name: str) -> str:
    name = (model_name or "").strip().lower()
    if "::" in name:
        name = name.split("::", 1)[1].strip()
    # provider/model 形式(OpenRouter)
    if "/" in name:
        name = name.split("/", 1)[1].strip()
    return name


def compute_deep_think_kwargs(model_name: str, deep_think: bool) -> Dict[str, Any]:
    """返回 ChatOpenAI ``model_kwargs`` 应该多带的字段。

    用法(graph/runner 里):
        extras = compute_deep_think_kwargs(req.model, req.deep_think)
        kwargs.setdefault("model_kwargs", {}).update(extras)
        runnable = get_ChatOpenAI(..., **kwargs)
    """
    if not deep_think:
        return {}
    low = _normalize(model_name)
    if not low:
        return {}

    # ── 阿里 Qwen3 / Qwen-Plus / Qwen-Max(支持 enable_thinking) ──
    # qwen-plus / qwen-max / qwen-turbo 都接受 enable_thinking;qwen-vl 系列也兼容
    if (
        low.startswith("qwen3-")
        or low.startswith("qwen-plus")
        or low.startswith("qwen-max")
        or low.startswith("qwen-turbo")
        or low.startswith("qwen-flash")
        or low.startswith("qwen-omni")
    ):
        return {"extra_body": {"enable_thinking": True}}

    # ── Anthropic Claude(Sonnet 3.7+ / Opus 4+ 支持 extended thinking) ──
    if (
        low.startswith("claude-opus-4")
        or low.startswith("claude-sonnet-4")
        or low.startswith("claude-3-7-sonnet")
        or low.startswith("claude-haiku-4")
    ):
        return {
            "extra_body": {
                "thinking": {"type": "enabled", "budget_tokens": _ANTHROPIC_BUDGET_TOKENS},
            },
        }

    # ── OpenAI o-series / DeepSeek-Reasoner:推理是模型内置,不需要 deep_think 参数 ──
    # 这些模型即便 deep_think=False 也走推理,所以这里 noop(不报错)
    if (
        low.startswith("o1-")
        or low.startswith("o3-")
        or low.startswith("o4-")
        or low.startswith("deepseek-reasoner")
    ):
        return {}

    # ── 其它模型 — 不支持,只记录一条 INFO 让用户知道为啥不生效 ──
    logger.info(
        "[deep_think] 模型 %s 未接入深度思考开关,忽略本次 deep_think=True",
        model_name,
    )
    return {}
