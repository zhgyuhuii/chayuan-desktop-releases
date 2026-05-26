"""多模态 capability 的调用日志 — in-memory 环形 buffer,前端"调用日志"面板用。

每个 key(如 "asr" / "ocr")独立 deque(maxlen=200),线程安全。
不持久化 — 进程重启即丢。只为帮用户看清楚每次调用的耗时、入参大小、文本预览、错误。

调用方约定:
    record("asr", success=True, duration_ms=520, bytes_in=12345,
           preview="你好 hello", error="")

读取方:
    get_log("asr")  # 全量(最多 200)
    get_log("asr", limit=50)  # 最近 50
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List

_MAX = 200
_LOG: Dict[str, Deque[dict]] = {}
_LOCK = threading.Lock()


def record(
    key: str,
    *,
    success: bool,
    duration_ms: float,
    bytes_in: int = 0,
    preview: str = "",
    error: str = "",
    extra: dict | None = None,
) -> None:
    """记一条调用。preview / error 各自截到 300 字符避免 buffer 爆。"""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "success": bool(success),
        "duration_ms": int(max(0, duration_ms)),
        "bytes_in": int(max(0, bytes_in)),
        # preview 是用户内容,300 够;error 可能是多层 diagnostics,放宽到 1500
        "preview": (preview or "")[:300],
        "error": (error or "")[:1500],
    }
    if extra:
        # 不让 extra 覆盖标准字段
        for k, v in extra.items():
            if k not in entry:
                entry[k] = v
    with _LOCK:
        dq = _LOG.get(key)
        if dq is None:
            dq = deque(maxlen=_MAX)
            _LOG[key] = dq
        dq.append(entry)


def get_log(key: str, *, limit: int = _MAX) -> List[dict]:
    """返回时间升序的最近 N 条;key 不存在返 []。"""
    with _LOCK:
        dq = _LOG.get(key)
        if not dq:
            return []
        # 直接 list(deque) 已经按插入顺序,取 tail
        items = list(dq)
    if limit and limit < len(items):
        return items[-limit:]
    return items


def clear(key: str | None = None) -> None:
    """清空(单 key 或全部)。测试 / debug 用。"""
    with _LOCK:
        if key is None:
            _LOG.clear()
        else:
            _LOG.pop(key, None)
