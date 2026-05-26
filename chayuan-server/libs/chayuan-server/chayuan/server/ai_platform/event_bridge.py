"""把 chayuan-server 的 ``local_index.scan_once`` ScanDelta → chayuan_core 事件总线。

工作机制
========

* chayuan-server 已经有一个全功能的 watchdog（``model_registry/watcher.py``） +
  60s 的 admin 接口扫描；
* chayuan_gateway 的 ``/v1/models/events`` SSE 订阅 ``chayuan_core.events.get_bus()``；
* 桥接：把 ``scan_once`` 的返回 ``ScanDelta`` 拆成 ``model.added`` / ``model.updated``
  / ``model.removed`` 三种事件，挨条 publish。这样：
  - 拖入文件 60s 内 → 前端 EventSource 立刻收到；
  - 已有 watcher 路径不动；
  - chayuan_gateway 可独立运行（事件总线工作）也可以共生（数据来自 chayuan-server）。

实现选择：**装饰器替身**（不是 monkey-patch 函数对象）。我们在 bootstrap 时
把 ``chayuan.server.model_registry.local_index.scan_once`` 的引用包一层；这个
做法的代价是：直接 ``from local_index import scan_once`` 拿到的旧引用不会被
替换。chayuan-server 内部用法基本是 ``local_index.scan_once(...)`` （模块属性
访问），所以替换有效。

公开 API：
* :func:`enable_event_bridge`
* :func:`disable_event_bridge`
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("chayuan.ai_platform.event_bridge")

_ORIG_SCAN = None  # 用于 disable 还原


def _publish_delta(delta) -> None:
    """ScanDelta → chayuan_core 事件总线。"""
    try:
        from chayuan_core.events import (
            TOPIC_MODEL_ADDED, TOPIC_MODEL_REMOVED, TOPIC_MODEL_UPDATED,
            get_bus,
        )
        from chayuan.server.ai_platform.repo_bridge import _entry_to_model
    except Exception as e:  # noqa: BLE001
        logger.debug("[event_bridge] 依赖未就绪：%r", e)
        return

    bus = get_bus()
    for e in getattr(delta, "added", None) or []:
        m = _entry_to_model(e)
        bus.publish(TOPIC_MODEL_ADDED, m.to_public())
    for e in getattr(delta, "updated", None) or []:
        m = _entry_to_model(e)
        bus.publish(TOPIC_MODEL_UPDATED, m.to_public())
    for mid in getattr(delta, "removed", None) or []:
        bus.publish(TOPIC_MODEL_REMOVED, {"id": mid})


def enable_event_bridge() -> bool:
    """把 ``scan_once`` 包一层 → 完成后转发 ScanDelta 到事件总线。

    Returns:
        True 装上 / False 已装过 / 包失败
    """
    global _ORIG_SCAN
    if _ORIG_SCAN is not None:
        return False  # 已经装过

    try:
        from chayuan.server.model_registry import local_index as li_mod
    except Exception as e:  # noqa: BLE001
        logger.warning("[event_bridge] 加载 local_index 失败：%r；不装总线桥", e)
        return False

    _ORIG_SCAN = li_mod.scan_once

    def _patched(*args, **kwargs):
        delta = _ORIG_SCAN(*args, **kwargs)
        try:
            _publish_delta(delta)
        except Exception:  # noqa: BLE001
            logger.exception("[event_bridge] publish_delta failed")
        return delta

    li_mod.scan_once = _patched
    logger.info("[event_bridge] scan_once → bus.publish 包装已启用")
    return True


def disable_event_bridge() -> bool:
    """还原 ``scan_once`` 原引用（测试 / 关停时用）。"""
    global _ORIG_SCAN
    if _ORIG_SCAN is None:
        return False
    try:
        from chayuan.server.model_registry import local_index as li_mod
        li_mod.scan_once = _ORIG_SCAN
    except Exception:  # noqa: BLE001
        pass
    _ORIG_SCAN = None
    return True


__all__ = ["enable_event_bridge", "disable_event_bridge"]
