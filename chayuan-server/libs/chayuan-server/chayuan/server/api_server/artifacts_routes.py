"""``/v1/artifacts/<sha>.<ext>`` — 暴露模态路由生成的图/音/视产物。

模态 Connector 把上游/本地推理出来的二进制内容通过
``modality.router.artifacts.save_bytes`` 落到 ``<CHAYUAN_ROOT>/artifacts/YYYY-MM/``,
拿到 ``/v1/artifacts/<sha>.<ext>`` URL,写入 v5 ``file`` 事件 → 前端 ``<img>`` /
``<audio>`` / ``<video>`` 直接 src 引用本路由。

支持:
  - Range(``FileResponse`` 内置) → 视频/大文件可流式拖动
  - 路径校验防穿越(filename 必须形如 ``<64hex>.<alnum>``)
  - ``Cache-Control: immutable``(内容寻址,产物字节级不可变 → 浏览器可长存)
  - HEAD(返 200 + Content-Length,前端预探测)

鉴权(本单机版):
  - 不做强鉴权 — 单机服务,artifacts URL 已包含 SHA256(64 字符)实际充当 token
  - 多用户/服务端场景后续接 ``require_auth_enabled`` 中间件 + per-user owner 校验
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from chayuan.server.modality.router.artifacts import (
    find_by_filename,
    guess_mime_from_filename,
)

logger = logging.getLogger("chayuan.api.artifacts")

artifacts_router = APIRouter(prefix="/v1/artifacts", tags=["modality:artifacts"])


@artifacts_router.get(
    "/{filename}",
    summary="按内容寻址获取模态产物(图/音/视)",
    response_class=FileResponse,
)
async def get_artifact(filename: str) -> FileResponse:
    """按 ``<sha>.<ext>`` 取产物;Range 自动支持。"""
    path = find_by_filename(filename)
    if path is None:
        # 不区分"非法格式"和"已过期",一律 404 — 避免泄漏存在性
        raise HTTPException(status_code=404, detail="artifact not found")
    mime = guess_mime_from_filename(filename)
    # 内容寻址 → 字节不可变 → 永久缓存 + immutable
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    return FileResponse(path, media_type=mime, headers=headers)


@artifacts_router.head(
    "/{filename}",
    summary="预探测:返 200 + Content-Length 即可",
    include_in_schema=False,
)
async def head_artifact(filename: str) -> Response:
    path = find_by_filename(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    mime = guess_mime_from_filename(filename)
    size = path.stat().st_size
    return Response(
        status_code=200,
        headers={
            "Content-Type": mime,
            "Content-Length": str(size),
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )
