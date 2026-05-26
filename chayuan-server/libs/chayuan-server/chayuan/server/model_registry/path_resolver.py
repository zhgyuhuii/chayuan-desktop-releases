"""模型 id ↔ 磁盘路径解析。

用途
====

``capability_router.resolve_model(cap)`` 返回的字符串可能是:

* 一个 **HF repo id**(``"BAAI/bge-m3"``) —— sentence_transformers /
  huggingface_hub 能直接吃;
* 一个 **绝对路径**(``"/opt/models/bge-m3"``) —— 直接传给 loader;
* 一个 **local_index 风格的 model_id**(``"models/embedding/BAAI--bge-m3"``) ——
  layout.yaml 释放出来后被 local_index 扫到,以 ``<source_tag>/<relpath>``
  为标识。下游 loader(``CrossEncoder`` / ``SentenceTransformer`` / 各种
  embedder)**不认这个**,需要先翻译成磁盘绝对路径。

本模块给一个统一翻译入口 :func:`resolve_model_id_to_path`:碰到第三种形式
就查 :mod:`local_index` 把它换成 ``entry.path``;其它原样返回。

为什么不让 capability_router 直接存路径
=====================================

* model_id 是稳定的"产品标识",同一份模型在不同机器上路径不同,但 id 相同;
* GUI / yaml / config_center 之间共享配置时,id 比路径更可移植;
* 翻译只在 loader 边界做一次,保留架构清洁。
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("chayuan.model_registry.path_resolver")


def resolve_model_id_to_path(model_id: str) -> str:
    """如果 ``model_id`` 是 :mod:`local_index` 已知条目,返回 ``entry.path``;
    否则原样返回。

    Args:
        model_id: 用户配的 default model 字符串(可能是 repo id / 路径 /
            local_index 标识)。

    Returns:
        loader 可吃的字符串:磁盘路径 或 原值。
    """
    if not model_id:
        return model_id
    try:
        from chayuan.server.model_registry.local_index import get_local_index
        entry = get_local_index().get(model_id)
        if entry is not None and entry.path:
            return entry.path
    except Exception as e:  # noqa: BLE001
        logger.debug("[path_resolver] local_index 查 %r 失败: %r", model_id, e)
    return model_id


def maybe_resolve(model_id: Optional[str]) -> Optional[str]:
    """容许 ``None``/空串的便捷形式。"""
    if not model_id:
        return model_id
    return resolve_model_id_to_path(model_id)


__all__ = ["resolve_model_id_to_path", "maybe_resolve"]
