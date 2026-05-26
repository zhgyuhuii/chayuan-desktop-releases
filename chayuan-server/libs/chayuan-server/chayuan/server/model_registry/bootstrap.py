"""启动时模型清单自检。

调用方
======

* sidecar 启动钩子在 settings 加载完后调一次 :func:`check_bootstrap`，
  根据返回的报告决定是否要让 sidecar-gate 弹"首启下载向导"。
* admin 路由 ``GET /admin/models/bootstrap`` 把同一份报告暴露给前端，
  用于首启向导 / 模型管理面板 / 健康检查。

实现要点
========

* 完全复用 :mod:`chayuan.server.model_registry.local_index`，不重复扫盘逻辑；
* 仅按 ``capability`` 维度判断"够不够用"，不强行检查具体 ``model_id``——
  避免与 layout.yaml 的 release 命名漂移；
* 必需 capabilities 可配置，默认 ``chat`` + ``text-embedding`` + ``rerank`` 三件套。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Sequence

from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    get_local_index,
    scan_once,
)

logger = logging.getLogger("chayuan.model_registry.bootstrap")

#: server 启动时必须至少各有一个候选的能力清单。
DEFAULT_REQUIRED: tuple[str, ...] = (
    "chat",
    "text-embedding",
    "rerank",
)


@dataclass
class CapabilityStatus:
    """单个 capability 的检测结果。"""

    capability: str
    satisfied: bool
    candidates: List[LocalModelEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "capability": self.capability,
            "satisfied": self.satisfied,
            "candidate_count": len(self.candidates),
            "candidates": [
                {
                    "model_id": e.model_id,
                    "path": e.path,
                    "format": e.format,
                    "family": e.family,
                    "size_bytes": int(e.size_bytes),
                }
                for e in self.candidates
            ],
        }


@dataclass
class BootstrapReport:
    """``check_bootstrap`` 的汇总返回。

    Attributes:
        ready: ``True`` = 所有必需 capability 都至少有一个本地候选。
        statuses: 按 ``required`` 顺序返回的每个 capability 结果。
        missing: 仅未满足的 capability 列表（简化的快速访问）。
    """

    ready: bool
    statuses: List[CapabilityStatus] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "missing": list(self.missing),
            "statuses": [s.to_dict() for s in self.statuses],
        }


def check_bootstrap(
    *,
    required: Sequence[str] = DEFAULT_REQUIRED,
    do_scan: bool = True,
) -> BootstrapReport:
    """检测必需 capability 是否都至少有一个本地候选模型。

    Args:
        required: 必需 capability 列表（与 :mod:`identifier` 用到的字符串口径一致）。
        do_scan: ``True`` 时先跑 ``scan_once`` 刷新本地索引；``False`` 直接读现有
            索引（用于性能敏感场景，例如热路径上不想每次都扫盘）。

    Returns:
        :class:`BootstrapReport`。
    """
    if do_scan:
        try:
            scan_once()
        except Exception as e:  # noqa: BLE001
            logger.warning("[bootstrap] scan_once 失败，按现有索引继续判断: %r", e)

    idx = get_local_index()
    statuses: List[CapabilityStatus] = []
    missing: List[str] = []
    for cap in required:
        candidates = idx.by_capability(cap)
        satisfied = bool(candidates)
        statuses.append(
            CapabilityStatus(
                capability=cap, satisfied=satisfied, candidates=candidates,
            )
        )
        if not satisfied:
            missing.append(cap)

    return BootstrapReport(
        ready=not missing,
        statuses=statuses,
        missing=missing,
    )


__all__ = [
    "DEFAULT_REQUIRED",
    "CapabilityStatus",
    "BootstrapReport",
    "check_bootstrap",
]
