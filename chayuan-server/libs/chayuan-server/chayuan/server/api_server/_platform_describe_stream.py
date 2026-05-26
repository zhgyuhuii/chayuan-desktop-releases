"""厂商简介流式生成 — 用 LLM 给某个 model_platform 写一段 hero/卡片副标题文案。

跟 ``_model_enrich.py`` 的区别:
  - 那边是给 model_id 列表批量出 JSON 元信息(description/release_date/...),落 model_metadata 表
  - 这边是给"厂商本身"写一段中文简介,流式 token 推给前端,实时填到设置弹窗的 description Input
  - 不落库;前端用户看到流出来的内容,自己点"保存"才会通过 PATCH 写到 platform.description

设计要点:
  - LLM 选取复用 ``_model_enrich._pick_llm_for_enrich``(优先 deepseek/zhipu/openai 等已配齐的,
    skip_platform=name 防"配 deepseek 时拿 deepseek 自己写"的循环依赖 — 万一 key 还没保存)
  - 输出**纯文本**,不 JSON;OpenAI 兼容 stream 协议:逐 token yield delta.content
  - 任何步骤失败 → yield 一个 error 事件,前端能展示并停止 loading
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator, Optional

logger = logging.getLogger("chayuan.api.admin.describe_stream")


_DESCRIBE_PROMPT = """\
你是模型服务商档案专家。请基于公开信息为模型服务商「{display_name}」写一段中文简介,\
用于产品卡片副标题。要求:
  - 2-3 句话,总长度严格 ≤120 字
  - 突出该服务商的定位 / 主打模型 / 主要场景
  - 不写"我们""您"等第二人称,不带广告语,不加引号、不加 markdown
  - 只输出简介正文本身,不要前后缀

例:
"DeepSeek 由幻方量化孵化,专注高性价比通用大模型,V4 / R 系列在代码与推理任务上接近第一梯队,常用于成本敏感的对话与 agent 场景。"

现在请为「{display_name}」生成简介:"""


async def describe_platform_stream(
    *,
    target_platform: str,
    target_display_name: str,
    timeout_s: float = 60.0,
) -> AsyncGenerator[str, None]:
    """流式 yield 简介 token 文本(已是 str,直接拼到前端 textarea)。

    协议:
      - 每次 yield 一段非空 chunk(可能 1 个字 / 一组词)
      - 不 yield 空字符串
      - 流自然结束 = 整段文案完成;调用方 emit done 事件
      - 抛 RuntimeError("...") = 选不到可用 LLM,调用方包成 503 事件
      - 抛其它 Exception = LLM 调用层失败,调用方包成 502 事件
    """
    # 复用 model_enrich 的 LLM 选择逻辑;skip_platform 避免循环依赖
    from chayuan.server.api_server._model_enrich import _pick_llm_for_enrich

    picked = _pick_llm_for_enrich(skip_platform=target_platform)
    if picked is None:
        raise RuntimeError("no LLM platform available; 请先配置任意一个有 LLM 的厂商再用 AI 生成简介")

    import openai
    import httpx

    prompt = _DESCRIBE_PROMPT.format(
        display_name=target_display_name or target_platform,
    )

    params: dict = {
        "base_url": picked.api_base_url,
        "api_key": picked.api_key,
    }
    if picked.api_proxy:
        params["http_client"] = httpx.AsyncClient(
            proxies=picked.api_proxy,
            timeout=timeout_s,
        )
    client = openai.AsyncClient(**params)

    # 用 stream=True 拿到 chat.completions 的增量响应,逐 chunk yield delta.content
    stream = await client.chat.completions.create(
        model=picked.model_id,
        messages=[
            {"role": "system", "content": "你只输出最终简介正文,不要任何解释或 markdown。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        timeout=timeout_s,
        stream=True,
    )

    try:
        async for chunk in stream:
            try:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield content
            except Exception:  # noqa: BLE001
                # 单个 chunk 解析失败不要终止整流;OpenAI 兼容服务有时会发空 chunk / role-only chunk
                continue
    finally:
        # 关闭底层 stream 释放连接
        try:
            await stream.close()  # type: ignore[func-returns-value]
        except Exception:  # noqa: BLE001
            pass
