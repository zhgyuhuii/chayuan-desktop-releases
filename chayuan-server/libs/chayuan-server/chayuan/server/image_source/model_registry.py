"""图像向量化模型 · Ready 状态注册表（SSOT）。

本模块是"模型是否可用"的**唯一真相源**：

- 「下载了」未必「依赖装了」（torch / transformers / PIL 可能缺）
- 「依赖装了」未必「能跑」（HF snapshot 可能不完整 / 结构变化）
- 所以必须三要素同时满足才算 "ready"：

    ReadyStatus = deps_available  AND  cached  AND  smoke_tested

UI / KB 选择下拉 / 检索入口都只应调用本模块的：

- :func:`list_models_for_ui`  面板列表（含全部三态，便于渲染按钮）
- :func:`list_ready_models`    KB 下拉（只返回严格 ready 的）
- :func:`is_model_ready`       单模型探测，无副作用

测试通过态用**磁盘 sentinel**（`<cache_root>/.chayuan_smoke/<safe_name>.ok`）
持久化——零依赖、跨进程、重启保留；比写 SQLite 简单，也避免 Redis 依赖。
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from chayuan.server.image_source.embedder import (
    SUPPORTED_MODELS,
    is_model_ready as _deps_probe,
)
from chayuan.server.image_source.model_manager import (
    _cache_root,
    _dir_size,
    _model_local_dir,
)

logger = logging.getLogger("chayuan.image_source.model_registry")

_SENTINEL_DIRNAME = ".chayuan_smoke"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class ReadyStatus:
    """单个模型的三态就绪信息。UI / 调用方都读这个。"""

    name: str
    deps_available: bool
    deps_reason: str
    cached: bool
    cached_size_mb: float
    smoke_tested: bool
    smoke_tested_at: Optional[int]  # epoch 秒；None 表示从未测过
    smoke_error: str

    @property
    def ready(self) -> bool:
        """三要素齐全才真正可用。"""
        return bool(self.deps_available and self.cached and self.smoke_tested)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["ready"] = self.ready
        return d


# ---------------------------------------------------------------------------
# Sentinel 读写
# ---------------------------------------------------------------------------


def _sentinel_dir() -> Path:
    p = _cache_root() / _SENTINEL_DIRNAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe(name: str) -> str:
    return name.replace("/", "--").replace(":", "_")


def _sentinel_path(name: str) -> Path:
    return _sentinel_dir() / f"{_safe(name)}.ok"


def _read_sentinel(name: str) -> Dict[str, Any]:
    p = _sentinel_path(name)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def mark_smoke_tested(name: str, dim: int, extra: Optional[Dict[str, Any]] = None) -> None:
    """标记模型已通过 smoke 测试。调用方：``model_manager.smoke_test_model``。"""
    payload = {
        "name": name,
        "dim": int(dim or 0),
        "tested_at": int(time.time()),
    }
    if extra:
        payload.update(extra)
    try:
        _sentinel_path(name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("写 smoke sentinel 失败 %s：%r", name, e)


def clear_smoke_tested(name: str) -> None:
    """模型重下 / 删除时调用，避免拿旧测试结果给用户误导。"""
    p = _sentinel_path(name)
    try:
        if p.exists():
            p.unlink()
    except Exception as e:  # noqa: BLE001
        logger.debug("清 smoke sentinel 失败 %s：%r", name, e)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def is_model_ready(name: str) -> ReadyStatus:
    """**权威入口**：三要素齐查，无副作用。"""
    spec = SUPPORTED_MODELS.get(name)
    local_dir = _model_local_dir(name)
    cached = local_dir.exists() and any(local_dir.rglob("config.json"))
    cached_size_mb = round(_dir_size(local_dir) / (1024 * 1024), 1) if local_dir.exists() else 0.0

    # 依赖探测（embedder.is_model_ready 只看 torch/transformers/PIL，不走网络）
    probe = _deps_probe(name)
    deps_available = bool(probe.get("available"))
    deps_reason = str(probe.get("reason") or "")

    meta = _read_sentinel(name)
    smoke_tested = bool(meta) and bool(cached)  # 删缓存后 sentinel 作废
    tested_at = int(meta.get("tested_at") or 0) or None
    smoke_error = str(meta.get("error") or "")

    return ReadyStatus(
        name=name,
        deps_available=deps_available,
        deps_reason=deps_reason,
        cached=bool(cached),
        cached_size_mb=cached_size_mb,
        smoke_tested=smoke_tested,
        smoke_tested_at=tested_at,
        smoke_error=smoke_error,
    )


def list_models_for_ui() -> List[Dict[str, Any]]:
    """面板列表：覆盖全部支持模型，含 Spec 元信息 + ReadyStatus + capabilities。

    用于「模型配置」页的图像模型卡片——需要对未下载 / 未测试的也展示，
    便于用户点按钮下载 / 测试。
    """
    out: List[Dict[str, Any]] = []
    for name, spec in SUPPORTED_MODELS.items():
        rs = is_model_ready(name)
        out.append({
            "name": name,
            "family": spec.family,
            "dim": spec.dim,
            "approx_size_mb": spec.approx_size_mb,
            "chinese_level": spec.chinese_level,
            "languages": spec.languages,
            "description": spec.description,
            "capabilities": spec.capabilities.to_dict(),
            "extra_deps": list(spec.extra_deps),
            "license": spec.license,
            **rs.to_dict(),
        })
    # 排序：已 ready → 仅缓存 → 未下载；同类内 跨模态优先、中文强优先、大小小优先
    _cn_rank = {"strong": 0, "medium": 1, "weak": 2}

    def _key(item: Dict[str, Any]):
        caps = item.get("capabilities") or {}
        stage = 0 if item["ready"] else (1 if item["cached"] else 2)
        return (
            stage,
            0 if caps.get("crossmodal") else 1,   # 跨模态优先
            _cn_rank.get(item.get("chinese_level", "medium"), 1),
            int(item.get("approx_size_mb") or 0),  # 小模型优先
        )

    out.sort(key=_key)
    return out


def list_ready_models(crossmodal_only: bool = False) -> List[Dict[str, Any]]:
    """**KB 下拉专用**：只返回严格 ready（依赖+缓存+测试 三齐）的模型。

    :param crossmodal_only: 若为 True，仅返回跨模态模型（text+image 同空间）。
                            用于默认文本查图场景——DINOv2/ResNet 只能以图搜图，
                            在这里会被过滤掉。

    返回轻量字段——只够下拉渲染 + KB 创建 payload 用。
    """
    out: List[Dict[str, Any]] = []
    for item in list_models_for_ui():
        if not item.get("ready"):
            continue
        caps = item.get("capabilities") or {}
        if crossmodal_only and not caps.get("crossmodal"):
            continue
        out.append({
            "name": item["name"],
            "family": item["family"],
            "dim": item["dim"],
            "chinese_level": item["chinese_level"],
            "languages": item["languages"],
            "description": item["description"],
            "cached_size_mb": item["cached_size_mb"],
            "smoke_tested_at": item["smoke_tested_at"],
            "capabilities": caps,
        })
    return out


def any_model_ready(crossmodal_only: bool = False) -> bool:
    """KB 页引导跳转用：一个就绪模型都没有时展示「前往模型配置页下载」。"""
    return bool(list_ready_models(crossmodal_only=crossmodal_only))


__all__ = [
    "ReadyStatus",
    "is_model_ready",
    "list_models_for_ui",
    "list_ready_models",
    "any_model_ready",
    "mark_smoke_tested",
    "clear_smoke_tested",
]
