"""OpenAI 兼容 ``/v1/images/generations`` 文生图 Connector。

适用平台:
    - OpenAI 官方(DALL-E 2 / DALL-E 3 / gpt-image-1)
    - 字节豆包 Ark(``https://ark.cn-beijing.volces.com/api/v3``,完整 OpenAI 兼容)
    - 其它 OpenAI-compatible 网关(OpenRouter / Together / Fireworks 的 image gen)

上游协议(完全照搬 OpenAI):
::

    POST {api_base}/images/generations
    Authorization: Bearer <api_key>
    {
      "model": "dall-e-3",
      "prompt": "...",
      "n": 1,                       # DALL-E 3 仅支持 1,其它可 1-10
      "size": "1024x1024",          # ★ 用 x 不是 *(与 DashScope 相反)
      "quality": "standard"|"hd",   # DALL-E 3 特有
      "style": "vivid"|"natural",   # DALL-E 3 特有
      "response_format": "url"      # 我们要 URL,不要 b64_json
    }

响应:
::

    {
      "created": 1700000000,
      "data": [
        {"url": "https://...", "revised_prompt": "..."}
      ]
    }

注意:
- DALL-E 3 强制 ``n=1``;请求 ``n>1`` 会 400。前端发了 ``n=4`` 我们这里 clamp 成 1。
- ``response_format=b64_json`` 也支持(后端解 base64 然后落盘),但默认 URL 更省
  内存;只在用户显式要 b64 时走那条分支。
"""

from __future__ import annotations

import base64
import logging
import re
from typing import AsyncIterator, Dict, Optional, Tuple

import httpx

from chayuan.server.modality.router.artifacts import save_bytes
from chayuan.server.modality.router.connectors.base import Connector, register
from chayuan.server.modality.router.protocol import Capability, GenerateReq
from chayuan.server.modality.router.sse_v5 import (
    data_part,
    error_event,
    file_part,
    text_delta,
    text_end,
    text_start,
)

logger = logging.getLogger("chayuan.modality.connectors.image_gen.openai_compat")


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────


def _normalize_size_openai(raw: Optional[str], model: str) -> str:
    """OpenAI 用 ``WxH`` 不是 ``W*H``;兼容前端传 ``*`` 自动改 ``x``。"""
    if not raw:
        # DALL-E 3 默认 1024x1024;DALL-E 2 也支持 256/512/1024
        return "1024x1024"
    s = raw.replace("*", "x")
    if re.fullmatch(r"\d+x\d+", s):
        return s
    return "1024x1024"


def _split_wh(size: str) -> Tuple[Optional[int], Optional[int]]:
    try:
        w, h = (int(x) for x in size.split("x"))
        return w, h
    except (ValueError, AttributeError):
        return None, None


def _build_endpoint(api_base: str) -> str:
    """从配置 api_base 拼 ``/images/generations``。

    OpenAI 兼容厂商通常 api_base 形如:
        https://api.openai.com/v1
        https://ark.cn-beijing.volces.com/api/v3
        https://openrouter.ai/api/v1
    都按 ``{base}/images/generations`` 拼。
    """
    base = (api_base or "").rstrip("/")
    return f"{base}/images/generations"


# ──────────────────────────────────────────────────────────────
# Connector
# ──────────────────────────────────────────────────────────────


class _OpenAICompatT2I(Connector):
    """OpenAI 兼容 t2i 实现 — 跨多 platform_type 复用同一份代码。

    子类通过 ``@register`` 选自己对应的 ``platform_type``:openai / volcengine /
    openrouter 等。本类无任何 platform-specific 分支,所有差异通过 model_platform
    表里的 ``api_base_url`` + ``api_key`` 配。
    """

    async def generate(self, req: GenerateReq) -> AsyncIterator[Dict]:
        prompt = (req.prompt or "").strip()
        if not prompt:
            yield error_event("文生图需要输入提示词描述", code="empty_prompt")
            return

        # 参数:size / n / quality / style / response_format / seed
        size = _normalize_size_openai(req.params.get("size"), req.model)
        try:
            raw_n = int(req.params.get("n") or 1)
        except (TypeError, ValueError):
            raw_n = 1
        # DALL-E 3 仅支持 n=1
        is_dalle3 = "dall-e-3" in req.model.lower() or "gpt-image" in req.model.lower()
        n = 1 if is_dalle3 else max(1, min(10, raw_n))
        response_format = req.params.get("response_format", "url")
        if response_format not in ("url", "b64_json"):
            response_format = "url"

        text_id = "t0"
        yield text_start(text_id)
        yield text_delta(text_id, f"正在生成 {n} 张图像({size}),约需 10-30 秒…")
        yield text_end(text_id)
        yield data_part("task-progress", {"percent": 5, "message": "提交生成请求"})

        endpoint = _build_endpoint(req.api_base)
        body: Dict = {
            "model": req.model,
            "prompt": prompt,
            "n": n,
            "size": size,
            "response_format": response_format,
        }
        if is_dalle3:
            quality = req.params.get("quality")
            if quality in ("standard", "hd"):
                body["quality"] = quality
            style = req.params.get("style")
            if style in ("vivid", "natural"):
                body["style"] = style
        headers = {
            "Authorization": f"Bearer {req.api_key}",
            "Content-Type": "application/json",
        }

        # 文生图 latency 高,30s 连接 / 120s 读
        timeout = httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(endpoint, json=body, headers=headers)
        except httpx.RequestError as e:
            yield error_event(
                f"无法连接 {req.platform_name or 'OpenAI 兼容'} 接口: {e}",
                code="upstream_unreachable",
            )
            return

        if resp.status_code != 200:
            try:
                err_body = resp.json()
            except ValueError:
                err_body = {"raw": resp.text[:500]}
            # OpenAI 的错误嵌在 error.message
            err_msg = (
                err_body.get("error", {}).get("message")
                if isinstance(err_body.get("error"), dict)
                else err_body.get("message") or err_body
            )
            yield error_event(
                f"{req.platform_name or 'OpenAI'} {resp.status_code}: {err_msg}",
                code=f"upstream_{resp.status_code}",
            )
            return

        data = resp.json()
        items = data.get("data") or []
        if not items:
            yield error_event(
                f"上游返回为空: {data}",
                code="empty_output",
            )
            return

        yield data_part("task-progress", {"percent": 70, "message": "下载图像数据"})

        w, h = _split_wh(size)
        image_count = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            for item in items:
                if req.cancelled is not None and req.cancelled.is_set():
                    yield error_event("用户已取消", code="cancelled")
                    return

                img_bytes: Optional[bytes] = None
                mime = "image/png"

                if response_format == "b64_json":
                    b64 = item.get("b64_json")
                    if not b64:
                        continue
                    try:
                        img_bytes = base64.b64decode(b64)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("[openai_compat.t2i] b64 decode failed: %r", e)
                        continue
                else:
                    url = item.get("url")
                    if not url:
                        continue
                    try:
                        ir = await client.get(url)
                        ir.raise_for_status()
                        img_bytes = ir.content
                        mime = (ir.headers.get("content-type") or "image/png").split(";")[0].strip()
                    except Exception as e:  # noqa: BLE001
                        logger.warning("[openai_compat.t2i] download failed: %r", e)
                        yield error_event(
                            f"图像 {image_count + 1} 下载失败: {e}",
                            code="download_failed",
                        )
                        continue

                if not img_bytes:
                    continue
                if not mime.startswith("image/"):
                    mime = "image/png"
                saved = save_bytes(img_bytes, mime)
                yield file_part(
                    media_type=mime,
                    url=saved["url"],
                    metadata={
                        "sha256": saved["sha256"],
                        "size_bytes": saved["size"],
                        "width": w,
                        "height": h,
                        "prompt": prompt,
                        # OpenAI DALL-E 3 会重写 prompt,留作 metadata 让 UI 显示
                        "revised_prompt": item.get("revised_prompt"),
                        "model": req.model,
                        "platform": req.platform_name,
                    },
                )
                image_count += 1

        # OpenAI 不返 usage 字段(image 系列);自填我们的视角
        yield data_part(
            "usage",
            {
                "image_count": image_count,
                "model": req.model,
                "size": size,
            },
        )
        yield data_part("task-progress", {"percent": 100, "message": f"完成 {image_count} 张"})

        if image_count == 0:
            yield error_event(
                "上游返回 200 但没有可用图片(可能内容审核拦截)。",
                code="no_image_in_response",
            )


# ──────────────────────────────────────────────────────────────
# 注册 — 一份实现服务多个 platform_type
# ──────────────────────────────────────────────────────────────


@register(Capability.T2I, "openai")
class OpenAIT2IConnector(_OpenAICompatT2I):
    """官方 OpenAI / OpenAI-compatible(默认 api_base = https://api.openai.com/v1)。"""


@register(Capability.T2I, "openrouter")
class OpenRouterT2IConnector(_OpenAICompatT2I):
    """OpenRouter — 代理多家,签名同 OpenAI。"""


@register(Capability.T2I, "volcengine")
class VolcengineT2IConnector(_OpenAICompatT2I):
    """字节豆包 Ark(``ark.cn-beijing.volces.com/api/v3``)— Ark 支持 OpenAI 兼容
    模式的 images.generations,签名同 OpenAI(Bearer ARK API Key)。

    不与火山的另一条"视觉生成"接口(``visual.volcengineapi.com``,AK/SK 签名)混淆 —
    那条协议不同,需要另写 Connector,未来需要时补。
    """
