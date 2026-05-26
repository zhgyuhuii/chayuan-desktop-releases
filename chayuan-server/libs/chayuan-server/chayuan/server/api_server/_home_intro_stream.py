"""首页"产品介绍"流式生成 — 用 LLM 写一段每次不同视角的察元介绍。

用途:HomePage 流式介绍区,展示给用户"察元 AI 是什么 / 能做什么"。每次进入主页
重新生成一段(不缓存),给用户"AI 应用"的现场感。

跟 ``_platform_describe_stream`` 的差别:那个是给"模型厂商"写一句副标题,这里
是给"整个察元产品"写一段简介,prompt 内容完全不同,所以独立成文件。

LLM 选择复用 ``_model_enrich._pick_llm_for_enrich``。前端在 LLM 不可用时
fallback 到本地静态文案,所以这里 503 就抛 RuntimeError 让前端走兜底。
"""
from __future__ import annotations

import logging
import random
from typing import AsyncGenerator

logger = logging.getLogger("chayuan.api.admin.home_intro")


# 可切换的"切入角度"— 每次随机选一个,让 LLM 的视角不重复。
# 这些不是直接给用户看的文案,而是 prompt 里的 framing 提示。
_ANGLES = (
    "把察元定位为 'AI 时代的本地办公伙伴'",
    "突出多模型对话 + 模型对抗(永道)能力,强调对比试用不同 LLM",
    "突出本地知识库:文档 / 结构化数据 / 向量库统一查询",
    "突出离线场景:不联网、不上传、不依赖 SaaS,数据全在本机",
    "突出与 WPS 加载项「察元 AI 文档助手」的协同 — 同一份知识库,WPS 内可直接调用",
    "突出对国产操作系统的兼容(统信 UOS / 麒麟 / openKylin / Linux 桌面),强调自主可控",
    "突出零配置启动,装完就能跑;模型可云可本地(Ollama / Infinity / vLLM 等),按需切换",
)


_PROMPT_TEMPLATE = """\
你是察元 AI 的产品文案。请为产品「察元 AI · 单机版」写一段中文介绍,目标读者是
办公场景下首次打开应用的用户。要求:

  - 不超过 150 字,自然口语,2-3 句话
  - 必须**明确**提到:多模型对话、本地知识库、离线运行 三者中的至少两项
  - 可以提到:对 WPS 加载项「察元 AI 文档助手」的协同(同一份知识库)
  - 可以提到:支持国产操作系统(统信 UOS / 麒麟 / openKylin)
  - 不要写"欢迎"、"您好"、"我们"等套话;不要 markdown / 项目符号 / 代码块
  - 直接输出正文本身,不加引号、不加前后缀

本次特别角度:{angle}

请开始:"""


async def home_intro_stream(*, timeout_s: float = 60.0) -> AsyncGenerator[str, None]:
    """流式 yield 产品介绍 token。

    协议同 _platform_describe_stream:
      - 单 chunk 直接 yield str(非空)
      - 选不到可用 LLM → 抛 RuntimeError("...")
      - LLM 调用失败 → 抛底层 Exception
    """
    from chayuan.server.api_server._model_enrich import _pick_llm_for_enrich

    picked = _pick_llm_for_enrich(skip_platform=None)
    if picked is None:
        raise RuntimeError("no LLM platform available; 请先配置任意一个有 LLM 的厂商再用 AI 生成介绍")

    import openai
    import httpx

    angle = random.choice(_ANGLES)
    prompt = _PROMPT_TEMPLATE.format(angle=angle)

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

    stream = await client.chat.completions.create(
        model=picked.model_id,
        messages=[
            {"role": "system", "content": "你只输出最终介绍正文本身,不要任何解释或 markdown。"},
            {"role": "user", "content": prompt},
        ],
        # 提一点温度让每次措辞不一样
        temperature=0.7,
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
                continue
    finally:
        try:
            await stream.close()  # type: ignore[func-returns-value]
        except Exception:  # noqa: BLE001
            pass
