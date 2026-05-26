"""vision 消息里的本地图片 URL → 内联 data URL 转换。

为什么需要这层
==============
chayuan-server 桌面/单机版给前端的图片 URL 是
``http://127.0.0.1:62581/v1/files/<file_id>/content`` —— 浏览器和 sidecar
之间用 loopback 互相访问没问题。

但 vision 模型(qwen-vl / GPT-4o / Claude vision …)运行在**上游云端**,
那个云端服务连不到我们本机的 loopback 端口。直接把这个 URL 塞进 message
后,上游 DashScope 报:

    InternalError.Algo.InvalidParameter: The provided URL does not appear
    to be valid. Ensure it is correctly formatted.

(其实是"无法解析/访问",DashScope 的错误信息委婉。)

解法
====
在消息**送出本机**之前,把所有"本地"形态的图片 URL 替换成 inline base64:

    ``data:<mime>;base64,<b64>``

这样 vision 上游不需要回拉本机就能拿到图,完美。

覆盖的 URL 形态
================
1. ``http://127.0.0.1:<port>/v1/files/<id>/content`` —— /v1/files 端点(主要)
2. ``http://localhost:<port>/v1/files/<id>/content`` —— 同上
3. ``/v1/files/<id>/content`` —— 前端忘拼 baseURL 的兜底
4. ``http://127.0.0.1:<port>/v1/artifacts/<sha>.<ext>`` —— 模态产物
5. ``http://localhost:<port>/v1/artifacts/<sha>.<ext>``
6. ``/v1/artifacts/<sha>.<ext>``

外部 https://、CDN、上游已存活的 URL 不动。

不要在这层做大压缩
==================
有些 vision 模型对 base64 size 敏感(比如 OpenAI vision 4MB 上限,
qwen-vl 5MB);超大的图本来就应该走 file_url(public URL)。我们这里
只做**直接转码**,大小由前端 / artifacts 上传时控制,不在这里 resize。
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("chayuan.chat.vision_inline")


_LOCAL_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "::1")


def _is_local_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    # 相对路径(/v1/files/…)算本地
    if not parsed.scheme and url.startswith("/"):
        return True
    # 绝对路径走 host 判断
    return (parsed.hostname or "") in _LOCAL_HOSTS


def _guess_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    return mime or "image/png"


def _resolve_files_path(file_id: str) -> Optional[str]:
    """``/v1/files/<file_id>/content`` 里的 ``file_id`` → 本地磁盘路径。"""
    try:
        from chayuan.server.api_server.openai_routes import _get_file_path
        path = _get_file_path(file_id)
        if path and os.path.isfile(path):
            return path
    except Exception as e:  # noqa: BLE001
        logger.debug("[vision_inline] _resolve_files_path failed: %r", e)
    return None


def _resolve_artifacts_path(filename: str) -> Optional[str]:
    """``/v1/artifacts/<sha>.<ext>`` 里的 ``filename`` → 本地磁盘路径。"""
    try:
        from chayuan.server.modality.router.artifacts import find_by_filename
        p = find_by_filename(filename)
        if p and p.is_file():
            return str(p)
    except Exception as e:  # noqa: BLE001
        logger.debug("[vision_inline] _resolve_artifacts_path failed: %r", e)
    return None


_FILES_RE = re.compile(r"/v1/files/([^/?#]+)/content")
_ARTIFACT_RE = re.compile(r"/v1/artifacts/([^/?#]+)")


def inline_local_image_url(url: str) -> str:
    """如果 ``url`` 是本机 file/artifact URL,改成 ``data:`` URL;否则原样返回。

    失败兜底:返原 URL —— 让上层报错好定位,不要静默吃掉问题。
    """
    if not url:
        return url
    if url.startswith("data:"):
        return url  # 已经是 data URL,不动
    if not _is_local_url(url):
        return url

    path: Optional[str] = None
    # 优先 /v1/files
    m = _FILES_RE.search(url)
    if m:
        path = _resolve_files_path(m.group(1))
    if not path:
        m = _ARTIFACT_RE.search(url)
        if m:
            path = _resolve_artifacts_path(m.group(1))

    if not path:
        logger.warning("[vision_inline] 无法解析本地 URL → 本地路径: %s", url)
        return url

    try:
        with open(path, "rb") as fp:
            raw = fp.read()
    except OSError as e:
        logger.warning("[vision_inline] 读取本地图片失败 %s: %r", path, e)
        return url

    mime = _guess_mime(path)
    if not mime.startswith("image/"):
        # 兜底:vision 上游基本只认 image/*;如果不是图片这里其实不该走这条链路
        mime = "image/png"
    b64 = base64.b64encode(raw).decode("ascii")
    logger.info(
        "[vision_inline] inlined %s → data URL (%d bytes, mime=%s)",
        url, len(raw), mime,
    )
    return f"data:{mime};base64,{b64}"
