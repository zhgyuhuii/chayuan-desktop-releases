"""Langfuse Python 客户端进程级单例。

设计：
- `get_langfuse()` 惰性创建一个 Langfuse 实例；缺少 host/key 时返回 None。
- 与 langfuse_integration.py 解耦：那边管理 LangChain callback handler；
  这里只暴露最小同步 API（`score()` / `event()`），供 chat_routes 直接写。
- 失败软降级：所有调用方应 try/except，避免阻塞主链路。
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger("chayuan.observability.lf")

_lf_singleton: Optional[Any] = None
_lf_lock = threading.Lock()
_unavailable = False


def get_langfuse() -> Optional[Any]:
    """返回进程级 Langfuse 客户端；未配置或导入失败返回 None。"""
    global _lf_singleton, _unavailable
    if _unavailable:
        return None
    if _lf_singleton is not None:
        return _lf_singleton
    with _lf_lock:
        if _lf_singleton is not None:
            return _lf_singleton
        if _unavailable:
            return None

        host = os.getenv("LANGFUSE_HOST", "").strip()
        public = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        secret = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        if not (host and public and secret):
            logger.info("langfuse env not configured; client disabled")
            _unavailable = True
            return None

        try:
            from langfuse import Langfuse  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.warning("langfuse import failed: %r", exc)
            _unavailable = True
            return None

        try:
            _lf_singleton = Langfuse(public_key=public, secret_key=secret, host=host)
        except Exception as exc:  # noqa: BLE001
            logger.warning("langfuse client init failed: %r", exc)
            _unavailable = True
            return None
        return _lf_singleton


def shutdown() -> None:
    global _lf_singleton
    if _lf_singleton is None:
        return
    try:
        _lf_singleton.flush()
    except Exception:  # noqa: BLE001
        pass
    _lf_singleton = None
