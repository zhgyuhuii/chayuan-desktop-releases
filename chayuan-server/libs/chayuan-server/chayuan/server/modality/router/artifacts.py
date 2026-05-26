"""模态产物(图/音/视)落盘 + 通过 ``/v1/artifacts/<sha>.<ext>`` 暴露。

设计要点
--------

1. 内容寻址:文件名 = ``sha256(bytes).<ext>``,天然去重 + 防注入
2. 按月分目录:``<CHAYUAN_ROOT>/artifacts/YYYY-MM/<sha>.<ext>`` —
   单目录文件数不会爆 + 老月份手动归档/迁 OSS 方便
3. 索引存内存 + 启动时扫盘重建;DB 表后续接(P4 异步 t2v 才必须)
4. 清理:LRU + TTL,本期先实现 TTL(默认 30 天),LRU 后续接
5. URL 形态:``/v1/artifacts/<sha>.<ext>``;sha 必填,ext 给浏览器猜 MIME 用
6. 鉴权:HTTP 路由那一层做(本模块只做存储),单机版可放宽到 optional

后续 OSS 适配:抽 ``ArtifactBackend`` Protocol,本模块实现 ``LocalBackend``,
S3/OSS 版本另起类即可,业务代码透明。
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import time
from pathlib import Path
from typing import Optional

from chayuan.settings import CHAYUAN_ROOT

logger = logging.getLogger("chayuan.modality.artifacts")


# ──────────────────────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────────────────────

ARTIFACTS_ROOT: Path = CHAYUAN_ROOT / "artifacts"
DEFAULT_TTL_DAYS: int = 30   # 兜底清理阈值,可被 Settings 覆盖

# 已知 MIME → 推荐扩展(部分上游返的 MIME 不带 ext 信息)
_MIME_TO_EXT: dict[str, str] = {
    "image/png":   "png",
    "image/jpeg":  "jpg",
    "image/webp":  "webp",
    "image/gif":   "gif",
    "audio/mpeg":  "mp3",
    "audio/wav":   "wav",
    "audio/x-wav": "wav",
    "audio/ogg":   "ogg",
    "audio/mp4":   "m4a",
    "video/mp4":   "mp4",
    "video/webm":  "webm",
    "application/pdf": "pdf",
}


def _ensure_root() -> None:
    ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)


def _yyyymm_dir() -> Path:
    d = ARTIFACTS_ROOT / time.strftime("%Y-%m")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ext_for(mime: str, fallback: Optional[str] = None) -> str:
    if mime in _MIME_TO_EXT:
        return _MIME_TO_EXT[mime]
    guessed = mimetypes.guess_extension(mime)
    if guessed:
        return guessed.lstrip(".")
    return fallback or "bin"


# ──────────────────────────────────────────────────────────────
# 存:save_bytes → 返回 {sha256, url, size, ext, mime}
# ──────────────────────────────────────────────────────────────


def save_bytes(content: bytes, mime: str, ext: Optional[str] = None) -> dict:
    """落盘并返回元数据(含可访问的 URL)。

    Args:
        content: 二进制内容
        mime:    MIME(image/png / audio/wav / video/mp4 ...)
        ext:    强制扩展名(不传则按 mime 推断)
    """
    _ensure_root()
    sha = hashlib.sha256(content).hexdigest()
    final_ext = (ext or _ext_for(mime)).lower().lstrip(".")
    filename = f"{sha}.{final_ext}"
    path = _yyyymm_dir() / filename
    if not path.exists():
        # 原子写:先 .tmp 再 rename,防止 GC 看到半成品
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)
        logger.info("[artifacts] saved %s (%d bytes, mime=%s)", filename, len(content), mime)
    return {
        "sha256": sha,
        "ext": final_ext,
        "mime": mime,
        "url": f"/v1/artifacts/{filename}",
        "size": len(content),
        "filename": filename,
    }


# ──────────────────────────────────────────────────────────────
# 查:filename → Path(找不到返 None)
# ──────────────────────────────────────────────────────────────


def find_by_filename(filename: str) -> Optional[Path]:
    """按 ``<sha>.<ext>`` 全文件名查 — 路径校验防穿越。"""
    # 安全检查:filename 不能含路径分隔符
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    # sha 部分必须是 64 hex
    parts = filename.split(".", 1)
    if len(parts) != 2:
        return None
    sha, ext = parts
    if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha):
        return None
    if not ext or not ext.isalnum():
        return None
    _ensure_root()
    # 月份索引未建,扫所有 yyyy-mm 子目录(单机版数量有限,O(N) 可接受;
    # P4 引索引表后改成 O(1) 查表)
    for ym in sorted(ARTIFACTS_ROOT.iterdir(), reverse=True):
        if not ym.is_dir():
            continue
        candidate = ym / filename
        if candidate.is_file():
            return candidate
    return None


def guess_mime_from_filename(filename: str) -> str:
    """根据 ``<sha>.<ext>`` 给前端 Content-Type。"""
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


# ──────────────────────────────────────────────────────────────
# 清理:按 mtime 删超过 TTL 的文件
# ──────────────────────────────────────────────────────────────


def purge_expired(ttl_days: int = DEFAULT_TTL_DAYS) -> int:
    """删 mtime 超过 ``ttl_days`` 的文件,返回删除数量。

    单机版定时任务 / 启动时调一次即可;OSS 版会换成生命周期策略。
    """
    if not ARTIFACTS_ROOT.exists():
        return 0
    cutoff = time.time() - ttl_days * 86400
    removed = 0
    for ym in ARTIFACTS_ROOT.iterdir():
        if not ym.is_dir():
            continue
        for f in ym.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError as e:
                logger.warning("[artifacts] purge skip %s: %r", f, e)
        # 空目录顺手清
        try:
            if not any(ym.iterdir()):
                ym.rmdir()
        except OSError:
            pass
    if removed:
        logger.info("[artifacts] purged %d expired files (ttl=%d days)", removed, ttl_days)
    return removed
