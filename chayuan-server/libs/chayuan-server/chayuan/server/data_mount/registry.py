"""SourceRegistry —— 全局适配器注册中心。

线程安全;惰性导入 12 种源(避免启动时一次性 import 大堆 langchain 子包)。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from chayuan.server.data_mount.base import DataSourceAdapter

logger = logging.getLogger("chayuan.data_mount.registry")


class SourceRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._adapters: Dict[str, DataSourceAdapter] = {}

    def register(self, adapter: DataSourceAdapter) -> None:
        with self._lock:
            tid = getattr(adapter, "type_id", "")
            if not tid:
                raise ValueError(f"adapter has no type_id: {adapter!r}")
            if tid in self._adapters:
                logger.info("source adapter %s already registered, overwriting", tid)
            self._adapters[tid] = adapter

    def get(self, type_id: str) -> Optional[DataSourceAdapter]:
        with self._lock:
            return self._adapters.get(type_id)

    def list(self) -> List[DataSourceAdapter]:
        with self._lock:
            return list(self._adapters.values())

    def to_catalog(self) -> List[Dict[str, Any]]:
        """前端 ``GET /data-mounts/sources`` 返回的目录。"""
        out: List[Dict[str, Any]] = []
        for a in self.list():
            try:
                form = a.spec_form()
            except Exception as e:  # noqa: BLE001
                logger.warning("spec_form for %s failed: %s", a.type_id, e)
                form = {"fields": []}
            out.append({
                "type_id": a.type_id,
                "label": getattr(a, "label", a.type_id),
                "description": getattr(a, "description", ""),
                "icon": getattr(a, "icon", "database"),
                "capabilities": list(getattr(a, "capabilities", []) or []),
                "spec_form": form,
            })
        return out


_REGISTRY: Optional[SourceRegistry] = None
_REG_LOCK = threading.Lock()


def get_registry() -> SourceRegistry:
    global _REGISTRY
    with _REG_LOCK:
        if _REGISTRY is None:
            _REGISTRY = SourceRegistry()
        return _REGISTRY


def register_default_sources() -> None:
    """惰性注册 12 种默认源。

    每个源在自己的模块顶部 ``register(get_registry(), …)`` 即可,但集中
    在这里 import 一次更可控:谁失败了 logger.warning 而不影响其它。
    """
    reg = get_registry()
    # 已经注册过就跳过(避免重复注册产生 INFO 噪声)
    if reg.list():
        return

    from importlib import import_module

    modules = [
        "kb",
        "knowledge_source",
        "file",
        "annotation",
        "web",
        "sql",
        "s3",
        "mongo",
        "notion",
        "confluence",
        "github",
        "conversation",
    ]
    for name in modules:
        try:
            mod = import_module(f"chayuan.server.data_mount.sources.{name}")
            adapter_cls = getattr(mod, "ADAPTER", None)
            if adapter_cls is None:
                logger.warning("source module %s has no ADAPTER attr", name)
                continue
            reg.register(adapter_cls() if isinstance(adapter_cls, type) else adapter_cls)
        except Exception as e:  # noqa: BLE001
            # 缺依赖(notion / confluence 等可选 SDK)只 warn,不挂主流程
            logger.warning("register source %s failed: %s", name, e)


__all__ = ["SourceRegistry", "get_registry", "register_default_sources"]
