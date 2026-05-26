"""检索器插件协议（N-1/N-6/N-11 基础）。

把 HybridRetriever 里"硬编码 BM25 + Vector"改为"插件列表"；新增检索能力（ColBERT
/ Entity / ColPali）只需实现 ``RetrieverPlugin`` 协议并注册，**无需**改 hybrid_service
核心逻辑。

设计原则：
- 插件本身是**上下文无关**：输入 query + kb_name → 输出 List[Document]
- 插件可在**安装时**延迟加载重依赖（ColBERT 需 ragatouille，ColPali 需 transformers）
- 插件按权重参与 RRF 融合；权重由 kb_settings 配置，可运行时热切

注册：
    from chayuan.server.shared.retriever_plugins import register_plugin
    register_plugin("colbert", ColBertPlugin(), default_weight=0.3)

消费：
    from chayuan.server.shared.retriever_plugins import active_plugins
    for p in active_plugins():
        docs = p.retrieve(query, kb_service, top_k=20)
"""
from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document

logger = logging.getLogger("chayuan.shared.retriever_plugins")


class RetrieverPlugin(ABC):
    """检索器插件契约。所有实现必须 fail-soft（失败返回空列表，不抛）。"""

    name: str = "base"

    @abstractmethod
    def retrieve(self, query: str, kb_service, *, top_k: int = 10) -> List[Document]:
        ...

    def is_available(self) -> bool:
        """能否真正工作（驱动装了、索引 build 过、Settings 开了）。"""
        return True


@dataclass
class _PluginSlot:
    plugin: RetrieverPlugin
    default_weight: float = 0.3
    enabled: bool = True


_REGISTRY: Dict[str, _PluginSlot] = {}
_LOCK = threading.Lock()


def register_plugin(
    name: str, plugin: RetrieverPlugin, *,
    default_weight: float = 0.3, enabled: bool = True,
) -> None:
    with _LOCK:
        _REGISTRY[name] = _PluginSlot(plugin=plugin, default_weight=default_weight,
                                       enabled=enabled)


def unregister_plugin(name: str) -> None:
    with _LOCK:
        _REGISTRY.pop(name, None)


def active_plugins() -> List[RetrieverPlugin]:
    with _LOCK:
        return [s.plugin for s in _REGISTRY.values()
                if s.enabled and s.plugin.is_available()]


def plugin_weight(name: str) -> float:
    with _LOCK:
        slot = _REGISTRY.get(name)
        return float(slot.default_weight) if slot else 0.3


def plugin_list() -> List[Dict[str, Any]]:
    """管理面板用。"""
    with _LOCK:
        return [
            {
                "name": n, "default_weight": s.default_weight,
                "enabled": s.enabled,
                "available": bool(s.plugin.is_available()),
                "class": type(s.plugin).__name__,
            }
            for n, s in _REGISTRY.items()
        ]
