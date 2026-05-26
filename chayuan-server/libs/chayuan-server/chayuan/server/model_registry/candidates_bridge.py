"""把 :mod:`local_index` 扫到的本地模型桥接到 ``capability_defaults`` 候选列表。

为什么需要
==========

``GET /admin/capability_defaults`` 的 candidates 原本**只**来自
:mod:`model_platform_repository`（用户在 GUI 上配的云端厂商 + 模型）。
但 chayuan-desktop 装机后，``layout.yaml`` 把 GGUF 文件释放到本地，
由 :mod:`local_index` 扫盘发现 —— 这些条目原来**没有**进入 candidates 列表。

结果：用户安装完后在 GUI 看不到自家随包的本地模型，没法选为 default，
启动也起不来。

本模块负责把 local_index 的条目按 capability 映射成与 admin_routes 已有
candidates 同形状的字典，由 ``get_capability_defaults`` 在汇总阶段合并进去。

映射约定
========

* :mod:`identifier` 的 capability 词遵循 *kebab-case*（``text-embedding``）；
* :mod:`config_panel.runtime_framework_panel.CAPABILITY_LABELS` 用的是 panel
  侧短名（``embedding`` / ``clip`` / ``asr`` / ``ocr`` / ``t2i`` / ``t2v`` / ``tts``）。

这里给出一个**单向**映射 :data:`LOCAL_TO_PANEL_CAP`，反向查取（同一 panel cap
可能对应多个 local cap）由 :func:`local_candidates_for` 处理。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from chayuan.server.model_registry.local_index import get_local_index, scan_once

logger = logging.getLogger("chayuan.model_registry.candidates_bridge")


#: local_index ``identifier`` 的 capability 词 → ``CAPABILITY_LABELS`` panel cap。
LOCAL_TO_PANEL_CAP: Dict[str, str] = {
    "chat":            "chat",
    "text-embedding":  "embedding",
    "image-embedding": "clip",
    "rerank":          "rerank",
    "speech-to-text":  "asr",
    "image-to-text":   "ocr",
    "text-to-image":   "t2i",
    "text-to-video":   "t2v",
    "text-to-audio":   "tts",
}


def _local_caps_for_panel(panel_cap: str) -> List[str]:
    """panel 短名 → local_index 全名（可能 1-N，例如未来同一 panel cap 对应多个）。"""
    return [
        local for local, panel in LOCAL_TO_PANEL_CAP.items() if panel == panel_cap
    ]


def local_candidates_for(panel_cap: str, *, do_scan: bool = True) -> List[Dict[str, Any]]:
    """返回 ``panel_cap`` 对应的本地候选模型，格式与 ``get_capability_defaults``
    已有的 candidates 字段一致。

    输出形状
    --------
    每条记录::

        {
            "id":          str,        # local_index entry.model_id
            "runtime":     "local",    # 标识"非云端厂商"
            "format":      str,        # gguf / hf_transformers / onnx / ...
            "size_bytes":  int,
            "is_default":  False,      # 由调用方根据 defaults dict 后置改写
            "source":      "local_index",
            "path":        str,
            "family":      str,
        }

    Args:
        panel_cap: ``CAPABILITY_LABELS`` 的 cap 短名（``chat`` / ``embedding`` / ...）。
        do_scan: ``True`` 时先 ``scan_once`` 刷新本地索引；``False`` 直接读现有索引。

    Returns:
        本地候选列表；无对应映射或无候选时返回空列表。
    """
    local_caps = _local_caps_for_panel(panel_cap)
    if not local_caps:
        return []

    if do_scan:
        try:
            scan_once()
        except Exception as e:  # noqa: BLE001
            logger.debug("[candidates_bridge] scan_once 失败，沿用现有索引: %r", e)

    idx = get_local_index()
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for local_cap in local_caps:
        for e in idx.by_capability(local_cap):
            if e.model_id in seen:
                continue
            seen.add(e.model_id)
            out.append({
                "id": e.model_id,
                "runtime": "local",
                # platform_name 让前端按厂商分组时把 scanner 直出条目归到「本地模型」
                "platform_name": f"local-{panel_cap}",
                "format": e.format,
                "size_bytes": int(e.size_bytes),
                "is_default": False,
                "source": "local_index",
                "path": e.path,
                "family": e.family,
            })
    return out


def merge_local_into_candidates(
    candidates: Dict[str, List[Dict[str, Any]]],
    *,
    defaults: Dict[str, Any] | None = None,
    do_scan: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """就地把 ``local_candidates_for`` 的结果合并到现有 ``candidates`` 字典里。

    Args:
        candidates: ``{panel_cap: [候选 dict, ...]}``；本函数会**就地**追加本地条目。
        defaults: 可选；``{panel_cap: model_id}``。如果某条本地候选 ``id`` 等于
            ``defaults[panel_cap]``，把它的 ``is_default`` 置为 True。
        do_scan: 透传给 :func:`local_candidates_for`；多 cap 合并时只在第一次扫
            （内部缓存让后续调用免扫）。

    Returns:
        同一个 ``candidates`` 字典（方便链式调用）。

    去重策略
    --------
    用 ``id`` 字段（model_id）做唯一键。若某 model_id 已经从 platforms 进来，
    本地条目就**不再**追加（platform 端往往带 runtime/format 更丰富的字段）。
    """
    defaults = defaults or {}
    scan_done = False
    for panel_cap in list(candidates.keys()):
        ds = do_scan and not scan_done
        local = local_candidates_for(panel_cap, do_scan=ds)
        scan_done = scan_done or ds
        if not local:
            continue
        existing_ids = {c.get("id") for c in candidates[panel_cap]}
        default_id = defaults.get(panel_cap)
        for entry in local:
            if entry["id"] in existing_ids:
                continue
            if default_id and entry["id"] == default_id:
                entry["is_default"] = True
            candidates[panel_cap].append(entry)
    return candidates


__all__ = [
    "LOCAL_TO_PANEL_CAP",
    "local_candidates_for",
    "merge_local_into_candidates",
]
