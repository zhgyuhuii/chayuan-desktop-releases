"""DashScope qwen-image-edit Connector(同步)。

上游协议
========

复用文生图同一个 multimodal-generation endpoint,但 content 数组多一项
``{"image": "<URL or data URL>"}``:

::

    POST {api_base}/api/v1/services/aigc/multimodal-generation/generation
    Authorization: Bearer <api_key>
    {
      "model": "qwen-image-edit-2509",
      "input": {
        "messages": [
          {
            "role": "user",
            "content": [
              {"image": "data:image/png;base64,...."},
              {"text": "把背景换成海边"}
            ]
          }
        ]
      },
      "parameters": {
        "size": "1328*1328",
        "n": 1,
        "watermark": false
      }
    }

响应:同 t2i。``output.choices[].message.content[].image`` 是 URL,我们下载落 artifacts。

参考图来源
==========

GenerateReq.attachments[0].url 是 ``/v1/artifacts/<sha>.<ext>``;Connector 走
``find_by_filename`` 读本地文件 → base64 → data URL 喂给 DashScope。

为什么不直接传 ``/v1/artifacts/...``:那是后端**本机**路径,DashScope 在公网,
拿不到。除非我们部署带公网域名,否则只能内联 base64。
"""

from __future__ import annotations

import base64
import logging
import re
from typing import AsyncIterator, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

from chayuan.server.modality.router.artifacts import find_by_filename, save_bytes
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

logger = logging.getLogger("chayuan.modality.connectors.image_edit.dashscope")


# ──────────────────────────────────────────────────────────────
# 复用 t2i 的尺寸/endpoint helpers(微调)
# ──────────────────────────────────────────────────────────────


def _normalize_size(raw: Optional[str]) -> str:
    if not raw:
        return "1328*1328"
    if "x" in raw and "*" not in raw:
        raw = raw.replace("x", "*")
    if re.fullmatch(r"\d+\*\d+", raw):
        return raw
    return "1328*1328"


def _split_wh(size: str) -> Tuple[Optional[int], Optional[int]]:
    try:
        w, h = (int(x) for x in size.split("*"))
        return w, h
    except (ValueError, AttributeError):
        return None, None


def _build_endpoint(api_base: str) -> str:
    base = (api_base or "").rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}/services/aigc/multimodal-generation/generation"
    base = re.sub(r"/compatible-mode/v\d+$", "", base)
    base = re.sub(r"/v\d+$", "", base)
    return f"{base}/api/v1/services/aigc/multimodal-generation/generation"


def _local_artifact_to_data_url(url: str) -> Optional[str]:
    """从 ``/v1/artifacts/<sha>.<ext>`` URL 反查本地文件 → 转 base64 data URL。

    DashScope 在公网,拿不到我们本地 ``/v1/artifacts``,必须内联。
    """
    try:
        path = urlparse(url).path
    except Exception:  # noqa: BLE001
        return None
    if not path.startswith("/v1/artifacts/"):
        return None
    filename = path.rsplit("/", 1)[-1]
    local = find_by_filename(filename)
    if not local or not local.is_file():
        return None
    try:
        raw = local.read_bytes()
    except OSError as e:
        logger.warning("[image_edit.dashscope] cannot read artifact %s: %r", local, e)
        return None
    # MIME 推断:从扩展名;失败回退 image/png
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    mime_map = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "gif": "image/gif",
    }
    mime = mime_map.get(ext, "image/png")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ──────────────────────────────────────────────────────────────
# Connector
# ──────────────────────────────────────────────────────────────


@register(Capability.IMAGE_EDIT, "dashscope")
class DashScopeImageEditConnector(Connector):
    """qwen-image-edit-* 同步图像编辑。"""

    async def generate(self, req: GenerateReq) -> AsyncIterator[Dict]:
        prompt = (req.prompt or "").strip()
        if not prompt:
            yield error_event("图像编辑需要描述要做什么修改", code="empty_prompt")
            return
        if not req.attachments:
            yield error_event("图像编辑必须先上传一张参考图", code="missing_reference_image")
            return

        ref = req.attachments[0]
        # 用第一张图像;mime 不是图像 → 拒
        if not (ref.mime or "").startswith("image/"):
            yield error_event(
                f"参考图必须是图像类型(当前 mime={ref.mime})",
                code="invalid_reference_mime",
            )
            return

        # ref.url 是我们本地 artifacts URL,需要转 base64 data URL 给 DashScope
        data_url = _local_artifact_to_data_url(ref.url or "")
        if not data_url:
            yield error_event(
                "参考图已过期或读取失败,请重新上传",
                code="reference_unreadable",
            )
            return

        # 参数
        size = _normalize_size(req.params.get("size"))
        try:
            n = max(1, min(4, int(req.params.get("n") or 1)))
        except (TypeError, ValueError):
            n = 1
        watermark = bool(req.params.get("watermark", False))
        seed = req.params.get("seed")

        text_id = "t0"
        yield text_start(text_id)
        yield text_delta(text_id, f"正在编辑图像({size}),约需 5-15 秒…")
        yield text_end(text_id)
        yield data_part("task-progress", {"percent": 5, "message": "提交编辑请求"})

        endpoint = _build_endpoint(req.api_base)
        body: Dict = {
            "model": req.model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": data_url},
                            {"text": prompt},
                        ],
                    }
                ]
            },
            "parameters": {
                "size": size,
                "n": n,
                "watermark": watermark,
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

        timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(endpoint, json=body, headers=headers)
        except httpx.RequestError as e:
            yield error_event(
                f"无法连接 DashScope 图像编辑接口: {e}",
                code="upstream_unreachable",
            )
            return

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

        data = resp.json()
        choices = (data.get("output") or {}).get("choices") or []
        if not choices:
            yield error_event(f"上游返回为空: {data}", code="empty_output")
            return

        yield data_part("task-progress", {"percent": 70, "message": "下载编辑结果"})

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
                            "edit_of_sha256": (urlparse(ref.url or "").path.split("/")[-1].split(".")[0]
                                               if ref.url else None),
                        },
                    )
                    image_count += 1

        usage = data.get("usage") or {}
        yield data_part(
            "usage",
            {
                "image_count": image_count,
                "input_tokens": usage.get("input_tokens"),
                "image_tokens": usage.get("image_tokens"),
                "model": req.model,
                "request_id": data.get("request_id"),
            },
        )
        yield data_part("task-progress", {"percent": 100, "message": f"完成 {image_count} 张"})

        if image_count == 0:
            yield error_event(
                "上游返回 200 但没有可用图片(可能内容审核或参考图不达标)",
                code="no_image_in_response",
            )
