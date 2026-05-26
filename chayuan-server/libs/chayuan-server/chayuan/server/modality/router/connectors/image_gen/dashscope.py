"""阿里百炼(DashScope)文生图 Connector — qwen-image-* / wanx-* 系列。

上游协议
========

DashScope 的图像生成不走 OpenAI 兼容路径,只能调原生 endpoint:

    POST {api_base}/services/aigc/multimodal-generation/generation
    Authorization: Bearer <api_key>
    Content-Type: application/json

请求体(注意 content 强制是 array,只允许一个 text 元素):
::

    {
      "model": "qwen-image-max",
      "input": {
        "messages": [
          {"role": "user", "content": [{"text": "<prompt>"}]}
        ]
      },
      "parameters": {
        "size": "1664*928",       # 注意是 * 不是 x
        "n": 1,
        "watermark": false,
        "prompt_extend": true
      }
    }

响应(同步,200):
::

    {
      "output": {
        "choices": [
          {"message": {"content": [{"image": "https://..."}]}, "finish_reason": "stop"}
        ]
      },
      "usage": {"image_tokens": N, "input_tokens": N},
      "request_id": "..."
    }

模型尺寸约束
============

- ``qwen-image-max`` / ``qwen-image-plus``: 5 个固定尺寸
  1664*928 (16:9) / 928*1664 (9:16) / 1328*1328 (1:1) / 1472*1104 (4:3) / 1104*1472 (3:4)
- ``qwen-image-2.0`` / ``qwen-image-2.0-pro``: 自由,总像素 512*512 ~ 2048*2048
- ``wanx-*``: 各版本不同,通用 1024*1024;部分异步返 task_id(本期同步分支不接,
  PR-4 的 TaskManager 出来再补)

错误翻译
========

上游 4xx 文案大多面向开发者(如 ``content parameter's length invalid``),
通用错误已被 router 兜底翻译;本 Connector 只对**模型层面**的特殊错误
(如 prompt 不符合规则)做精准化解释。
"""

from __future__ import annotations

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

logger = logging.getLogger("chayuan.modality.connectors.image_gen.dashscope")


# ──────────────────────────────────────────────────────────────
# 默认尺寸 / endpoint 推断
# ──────────────────────────────────────────────────────────────


# 各系列默认尺寸 — 用户没在 params.size 里指定时回退
_DEFAULT_SIZE_BY_FAMILY: Dict[str, str] = {
    "qwen-image-max":  "1328*1328",
    "qwen-image-plus": "1328*1328",
    "qwen-image":      "1024*1024",
    "wanx":            "1024*1024",
    "wanxiang":        "1024*1024",
}


def _default_size_for(model: str) -> str:
    low = model.lower()
    for prefix, size in _DEFAULT_SIZE_BY_FAMILY.items():
        if low.startswith(prefix):
            return size
    return "1024*1024"


def _build_endpoint(api_base: str) -> str:
    """从配置的 api_base 推断 native endpoint。

    DashScope 大多数厂商配置写的是 OpenAI 兼容前缀:
        https://dashscope.aliyuncs.com/compatible-mode/v1

    要还原成原生:
        https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation

    其它形态(intl / 自托管代理)按 host 替换尾巴。
    """
    base = (api_base or "").rstrip("/")
    # 已经是 /api/v1 前缀就直接拼
    if base.endswith("/api/v1"):
        return f"{base}/services/aigc/multimodal-generation/generation"
    # 兼容模式前缀 → 切到 native
    base = re.sub(r"/compatible-mode/v\d+$", "", base)
    base = re.sub(r"/v\d+$", "", base)  # 防 /v1 形态
    return f"{base}/api/v1/services/aigc/multimodal-generation/generation"


def _normalize_size(raw: Optional[str], model: str) -> str:
    """规范化尺寸为 DashScope 要求的 ``W*H`` 格式。"""
    if not raw:
        return _default_size_for(model)
    # OpenAI 风格 1024x1024 → 1024*1024
    if "x" in raw and "*" not in raw:
        raw = raw.replace("x", "*")
    if re.fullmatch(r"\d+\*\d+", raw):
        return raw
    return _default_size_for(model)


def _split_wh(size: str) -> Tuple[Optional[int], Optional[int]]:
    try:
        w, h = (int(x) for x in size.split("*"))
        return w, h
    except (ValueError, AttributeError):
        return None, None


# ──────────────────────────────────────────────────────────────
# Connector
# ──────────────────────────────────────────────────────────────


@register(Capability.T2I, "dashscope")
class DashScopeT2IConnector(Connector):
    """qwen-image-* / wanx-* 同步文生图。

    异步任务模式(wanx 某些版本)留给 PR-4 TaskManager;本连接器只走同步分支,
    上游返 200 + 图片 URL → 我们下载落 artifacts → 吐 v5 file part。
    """

    async def generate(self, req: GenerateReq) -> AsyncIterator[Dict]:
        # 1. 入参校验
        prompt = (req.prompt or "").strip()
        if not prompt:
            yield error_event("文生图需要输入提示词描述", code="empty_prompt")
            return

        # 2. 参数规范化
        size = _normalize_size(req.params.get("size"), req.model)
        try:
            n = max(1, min(4, int(req.params.get("n") or 1)))
        except (TypeError, ValueError):
            n = 1
        watermark = bool(req.params.get("watermark", False))
        prompt_extend = bool(req.params.get("prompt_extend", True))
        seed = req.params.get("seed")

        # 3. UI:简短文本提示用户在等什么(用户在消息流里也能看到这一行)
        text_id = "t0"
        yield text_start(text_id)
        yield text_delta(text_id, f"正在生成 {n} 张图像({size}),约需 5-15 秒…")
        yield text_end(text_id)
        yield data_part("task-progress", {"percent": 5, "message": "提交生成请求"})

        # 4. 调上游
        endpoint = _build_endpoint(req.api_base)
        body: Dict = {
            "model": req.model,
            "input": {
                "messages": [
                    {"role": "user", "content": [{"text": prompt}]}
                ]
            },
            "parameters": {
                "size": size,
                "n": n,
                "watermark": watermark,
                "prompt_extend": prompt_extend,
            },
        }
        if seed is not None:
            try:
                body["parameters"]["seed"] = int(seed)
            except (TypeError, ValueError):
                pass
        headers = {
            "Authorization": f"Bearer {req.api_key}",
            "Content-Type": "application/json",
        }

        # qwen-image-max 同步推理通常 5-15 秒;给宽裕 timeout 但有上限避免挂死
        # (连不上 5 秒认输,读响应 120 秒)
        timeout = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(endpoint, json=body, headers=headers)
        except httpx.RequestError as e:
            yield error_event(
                f"无法连接 DashScope 文生图接口: {e}",
                code="upstream_unreachable",
            )
            return

        # 5. 错误响应翻译(上游 4xx/5xx)
        if resp.status_code != 200:
            try:
                err_body = resp.json()
            except ValueError:
                err_body = {"raw": resp.text[:500]}
            yield error_event(
                f"DashScope {resp.status_code}: "
                f"{err_body.get('message') or err_body}",
                code=f"upstream_{resp.status_code}",
            )
            return

        # 6. 解析 output.choices[].message.content[].image
        data = resp.json()
        choices = (data.get("output") or {}).get("choices") or []
        if not choices:
            # 某些异步分支返 task_id,本期不处理 → 提示用户
            if data.get("output", {}).get("task_id"):
                yield error_event(
                    "该模型走异步任务模式(返 task_id),本期暂不支持。"
                    "建议改用 qwen-image-max / qwen-image-plus 等同步模型。",
                    code="async_not_supported",
                )
            else:
                yield error_event(
                    f"上游返回为空: {data}",
                    code="empty_output",
                )
            return

        yield data_part("task-progress", {"percent": 70, "message": "下载图像数据"})

        # 7. 逐张下载 + 落 artifacts + 吐 file part
        w, h = _split_wh(size)
        image_count = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            for choice in choices:
                if req.cancelled is not None and req.cancelled.is_set():
                    yield error_event("用户已取消", code="cancelled")
                    return
                content = ((choice.get("message") or {}).get("content")) or []
                for item in content:
                    img_url = item.get("image")
                    if not img_url:
                        continue
                    try:
                        ir = await client.get(img_url)
                        ir.raise_for_status()
                    except Exception as e:  # noqa: BLE001
                        # 单张失败不阻塞其它,但要提示
                        logger.warning("[dashscope.t2i] download failed: %r", e)
                        yield error_event(
                            f"图像 {image_count + 1} 下载失败: {e}",
                            code="download_failed",
                        )
                        continue
                    img_bytes = ir.content
                    mime = (ir.headers.get("content-type") or "image/png").split(";")[0].strip()
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
                            "model": req.model,
                            "platform": req.platform_name,
                            "seed": body["parameters"].get("seed"),
                        },
                    )
                    image_count += 1

        # 8. usage + 进度终结
        usage = data.get("usage") or {}
        yield data_part(
            "usage",
            {
                "image_count": image_count,
                "image_tokens": usage.get("image_tokens"),
                "input_tokens": usage.get("input_tokens"),
                "request_id": data.get("request_id"),
                "model": req.model,
            },
        )
        yield data_part("task-progress", {"percent": 100, "message": f"完成 {image_count} 张"})

        if image_count == 0:
            yield error_event(
                "上游返回 200 但没有可下载的图片 URL,可能是审核拦截。",
                code="no_image_in_response",
            )
