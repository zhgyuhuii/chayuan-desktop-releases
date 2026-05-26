"""94-2:从已运行的 Infinity 服务拉**真实加载的模型清单**,按 capability 归类。

业务背景
========
原来 chayuan 在 ④ tab clip 候选列表里靠 hf-cache 文件名关键词("clip" / "siglip"
/ "bge-m3")推断哪些模型可用 — 不准:

  * Infinity 实际加载的模型不一定都在 hf-cache(可能 docker 容器内 cache)
  * hf-cache 里的模型不一定被 Infinity 加载(用户没在 ``--model-id`` 指定)
  * 命名不规范的模型(如 ``my-org/custom-clip``)猜不准

本模块直接调 Infinity 的 OpenAI 兼容 ``/v1/models`` 接口,拿到真实加载的
模型 + capability 标签,5 秒 LRU 缓存避免反复 HTTP。

Infinity ``/v1/models`` 响应示例(0.0.75+):
::

    {
      "data": [
        {
          "id": "jinaai/jina-clip-v1",
          "object": "model",
          "owned_by": "jina",
          "capabilities": ["embed", "image_embed"]    # 或 "task": "image-embedding"
        },
        ...
      ]
    }

不同 Infinity 版本字段名不一,本模块尽量兼容(``capabilities`` / ``capability``
/ ``task`` / ``model_type`` 都尝试,失败回到名字关键词推断)。
"""
from __future__ import annotations

import logging
import threading
import time as _t
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("chayuan.config_panel.infinity_inventory")

# 5 秒 LRU
_INVENTORY_CACHE: Dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 5.0


# Infinity capability label → chayuan capability 归一化
# Infinity 0.0.75+ 用的是 ``embed`` / ``image_embed`` / ``rerank`` 等;
# 个别老版本用 ``embedding`` / ``classify``。
_INFINITY_CAP_NORMALIZE: Dict[str, str] = {
    # 文本嵌入
    "embed": "embedding",
    "embedding": "embedding",
    "embeddings": "embedding",
    "text-embedding": "embedding",
    # 图像 / 跨模态嵌入
    "image_embed": "clip",
    "image-embedding": "clip",
    "clip": "clip",
    "vision_embed": "clip",
    # 重排
    "rerank": "rerank",
    "reranker": "rerank",
    "cross-encoder": "rerank",
}


@dataclass(frozen=True)
class InfinityModel:
    """Infinity 加载的一个模型。"""

    model_id: str                       # HF repo 名,如 "jinaai/jina-clip-v1"
    capabilities: tuple = field(default_factory=tuple)   # 归一化后的 cap 集合
    owned_by: str = ""                  # vendor 字段(显示用)
    raw: Dict[str, Any] = field(default_factory=dict)    # 原响应的整条 record

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "capabilities": list(self.capabilities),
            "owned_by": self.owned_by,
        }


def _guess_capabilities_from_name(model_id: str) -> Set[str]:
    """名字关键词 fallback:Infinity 老版本不返 capabilities 字段时用。"""
    mid = model_id.lower()
    out: Set[str] = set()
    if any(k in mid for k in ("clip", "siglip", "blip", "vision", "vit-")):
        out.add("clip")
    if any(k in mid for k in ("rerank", "reranker", "cross-encoder")):
        out.add("rerank")
    if any(k in mid for k in ("bge", "embedding", "embed", "e5", "gte", "instructor")):
        # bge-reranker 已被上面 rerank 覆盖,不重复 bge 进 embedding
        if "rerank" not in mid:
            out.add("embedding")
    return out


def _parse_record(record: Dict[str, Any]) -> Optional[InfinityModel]:
    """从一条 ``/v1/models`` data 项解析出 InfinityModel。"""
    if not isinstance(record, dict):
        return None
    mid = str(record.get("id") or "").strip()
    if not mid:
        return None

    # 多字段尝试拿 capability
    raw_caps: List[str] = []
    for key in ("capabilities", "capability", "task", "tasks", "model_type"):
        v = record.get(key)
        if isinstance(v, list):
            raw_caps.extend(str(x) for x in v)
        elif isinstance(v, str) and v:
            raw_caps.append(v)
    # 归一化
    caps: Set[str] = set()
    for c in raw_caps:
        nc = _INFINITY_CAP_NORMALIZE.get(c.strip().lower())
        if nc:
            caps.add(nc)
    # 字段都没 → 退到名字关键词推断
    if not caps:
        caps = _guess_capabilities_from_name(mid)

    return InfinityModel(
        model_id=mid,
        capabilities=tuple(sorted(caps)),
        owned_by=str(record.get("owned_by") or "").strip(),
        raw=record,
    )


# Infinity ``/models`` 接口的可能路径(不同版本 / 部署):
#   * 0.0.75+ 默认:``/v1/models``(OpenAI 兼容)
#   * 部分老版本 / 用户自起进程时是 ``/models``(无 ``v1`` 前缀)
# fetch 时按顺序尝试,任一返 200 即用。
_MODELS_ENDPOINT_CANDIDATES: tuple = ("/v1/models", "/models")


def fetch_infinity_models(
    base_url: str, *, timeout: float = 1.5,
) -> List[InfinityModel]:
    """94-2:GET ``<base_url>/v1/models`` 或 ``/models``,返 InfinityModel 列表。

    多端点 fallback(用户的 Infinity 版本可能不带 ``/v1/`` 前缀)。
    5 秒 LRU 缓存(按 base_url 维度);失败返空列表,不抛。
    """
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return []

    now = _t.time()
    with _CACHE_LOCK:
        ent = _INVENTORY_CACHE.get(base_url)
        if ent and now - ent[0] < _CACHE_TTL:
            return list(ent[1])

    try:
        import httpx  # type: ignore
    except ImportError:
        return []

    data = None
    for path in _MODELS_ENDPOINT_CANDIDATES:
        url = f"{base_url}{path}"
        try:
            r = httpx.get(url, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.debug("[infinity_inventory] GET %s failed: %r", url, e)
            continue
        if r.status_code != 200:
            logger.debug("[infinity_inventory] %s returned %d", url, r.status_code)
            continue
        try:
            payload = r.json()
        except Exception as e:  # noqa: BLE001
            logger.debug("[infinity_inventory] json parse failed: %r", e)
            continue
        # OpenAI 兼容 ``{"data": [...]}``;有的版本直接返裸 list / 顶层 ``models``
        if isinstance(payload, dict):
            data = payload.get("data")
            if data is None:
                data = payload.get("models")
        elif isinstance(payload, list):
            data = payload
        if isinstance(data, list):
            break  # 拿到了就用

    if not isinstance(data, list):
        return []

    out: List[InfinityModel] = []
    for record in data:
        m = _parse_record(record)
        if m is not None:
            out.append(m)

    with _CACHE_LOCK:
        _INVENTORY_CACHE[base_url] = (now, out)
    return out


def invalidate_inventory_cache(base_url: str = "") -> None:
    """配置变更后强制重拉(挂模型 / 重启 Infinity)。"""
    with _CACHE_LOCK:
        if base_url:
            _INVENTORY_CACHE.pop(base_url.rstrip("/"), None)
        else:
            _INVENTORY_CACHE.clear()


def get_infinity_models_by_capability(
    base_url: str, *, timeout: float = 1.5,
) -> Dict[str, List[InfinityModel]]:
    """便捷接口:按 capability 反查 → ``{"clip": [...], "embedding": [...], "rerank": [...]}``。"""
    by_cap: Dict[str, List[InfinityModel]] = {
        "clip": [], "embedding": [], "rerank": [],
    }
    for m in fetch_infinity_models(base_url, timeout=timeout):
        for c in m.capabilities:
            if c in by_cap:
                by_cap[c].append(m)
    return by_cap


__all__ = [
    "InfinityModel",
    "fetch_infinity_models",
    "invalidate_inventory_cache",
    "get_infinity_models_by_capability",
]
