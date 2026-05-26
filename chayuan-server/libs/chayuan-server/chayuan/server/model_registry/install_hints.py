"""为"缺模型"场景生成下载指引。

输入: :class:`~chayuan.server.model_registry.bootstrap.BootstrapReport` 里
``missing`` 的 capability 列表

输出: 推荐的 release 名称 + 镜像源 + 大致大小，供前端首启向导 / 模型管理面板展示。

跟 layout.yaml 的关系
=====================

这里写死的 release → capability 映射应当与
``packaging/python312/layout.yaml`` 的 release.models 段保持一致；
有一个保护性单测 :func:`test_install_hints_align_with_layout_yaml` 防止漂移。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List


# capability → 推荐 release（与 layout.yaml releases.<name>.models 的 capability 覆盖一致）。
# 每个 capability 至少有一个 release 能提供。
_CAP_TO_RELEASE: Dict[str, str] = {
    "chat": "lite",
    "text-embedding": "lite",
    "rerank": "lite",
    "speech-to-text": "lite",
    "image-to-text": "standard",
}


# release → 大致下载量 / 描述。数值是经验估算，用于前端进度提示，不参与校验。
_RELEASE_INFO: Dict[str, Dict[str, object]] = {
    "lite": {
        "approx_size_mb": 3500,
        "description": "单机轻量版，含默认 chat + embedding + rerank + asr",
    },
    "standard": {
        "approx_size_mb": 7000,
        "description": "服务器标准版，含 OCR + 高精度 embedding",
    },
    "pro": {
        "approx_size_mb": 12000,
        "description": "企业全功能版，含 Qwen3-7B 等更大模型",
    },
}


#: 推荐镜像，靠前优先。
DEFAULT_MIRRORS: List[Dict[str, str]] = [
    {"name": "hf-mirror", "endpoint": "https://hf-mirror.com",
     "note": "中国大陆推荐"},
    {"name": "modelscope", "endpoint": "https://modelscope.cn",
     "note": "阿里魔搭"},
    {"name": "huggingface", "endpoint": "https://huggingface.co",
     "note": "国际"},
]


@dataclass
class InstallHint:
    """单个 release 的安装指引。"""

    release: str
    description: str
    approx_size_mb: int
    covered_capabilities: List[str] = field(default_factory=list)
    mirrors: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "release": self.release,
            "description": self.description,
            "approx_size_mb": int(self.approx_size_mb),
            "covered_capabilities": list(self.covered_capabilities),
            "mirrors": [dict(m) for m in self.mirrors],
        }


def build_install_hints(missing: Iterable[str]) -> List[InstallHint]:
    """把缺失的 capability 列表聚合成 release 级别的下载推荐。

    一个 release 通常一次性覆盖多个 capability；在去重的同时按"档位从小到
    大"排序（``lite < standard < pro``），让用户优先看到最经济的选项。
    """
    missing_set = set(missing)
    if not missing_set:
        return []

    # capability → release 反向汇总
    release_caps: Dict[str, List[str]] = {}
    for cap in missing_set:
        release = _CAP_TO_RELEASE.get(cap, "lite")
        release_caps.setdefault(release, []).append(cap)

    order = ["lite", "standard", "pro"]
    hints: List[InstallHint] = []
    for r in order:
        if r not in release_caps:
            continue
        info = _RELEASE_INFO.get(r, {})
        hints.append(
            InstallHint(
                release=r,
                description=str(info.get("description", "")),
                approx_size_mb=int(info.get("approx_size_mb", 0) or 0),
                covered_capabilities=sorted(release_caps[r]),
                mirrors=[dict(m) for m in DEFAULT_MIRRORS],
            )
        )
    return hints


__all__ = [
    "DEFAULT_MIRRORS",
    "InstallHint",
    "build_install_hints",
]
