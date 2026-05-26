"""``layout.yaml`` 解析 + Release 视图计算。

设计要点
========

* 三层结构：
  - :class:`Resource` — 单个被打包资源（vendor 二进制 / 模型 / 运行时）；
  - :class:`ReleaseSpec` — lite / standard / pro 三种发布的清单（资源 ID 列表）；
  - :class:`Layout` — 顶层容器，承载 resources + releases；
* matrix 字段在 yaml 里是 ``{platform_key: pattern_or_dict}``；
  解析时把 dict 风格（含 ``ext`` / ``platform_tag`` 等可替换变量）也保留下来，
  fetcher 需要时再做模板替换；
* ``optional`` 资源在 release 引用但 vendor/ 没填时仅警告，不阻塞 build；
* 所有路径相对仓库根（``chayuan-server/``）；packager 内部解析为绝对路径。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from chayuan_packaging.platform_info import PlatformKey

logger = logging.getLogger("chayuan_packaging.manifest")


@dataclass
class Resource:
    """单个待打包资源（一种 vendor/runtimes/<name>/ 或 models/<...>/）。"""

    name: str
    kind: str                            # "runtimes" / "services" / "inference" / "models"
    dest: str                            # 相对仓库根的目标目录
    source: str = ""                     # 默认 source；matrix 里也可以覆盖
    version: str = ""
    optional: bool = False
    note: str = ""
    matrix: Dict[str, Any] = field(default_factory=dict)
    repo: str = ""                       # 仅 models 用：HF repo
    file: str = ""                       # 仅模型/单文件用：从 repo 下哪个文件
    unpack: str = ""                     # tar.gz / zip / tar.xz / tar.gz+make / ""
    release_tags: List[str] = field(default_factory=list)  # ["lite", "standard"]

    def matrix_for(self, platform: PlatformKey) -> Optional[Dict[str, Any]]:
        """取指定平台的 matrix 行；不存在返回 None。

        统一返回 ``dict``：原始可以是 str / dict / 嵌套子键 ``source``。
        """
        raw = self.matrix.get(platform)
        if raw is None:
            return None
        if isinstance(raw, str):
            return {"asset": raw}
        if isinstance(raw, dict):
            return dict(raw)
        return {"raw": raw}

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "dest": self.dest,
            "source": self.source, "version": self.version,
            "optional": self.optional, "note": self.note,
            "matrix": dict(self.matrix), "repo": self.repo, "file": self.file,
            "unpack": self.unpack, "release_tags": list(self.release_tags),
        }


@dataclass
class ReleaseSpec:
    """一种发布矩阵（lite / standard / pro / 自定义）。"""

    name: str
    description: str = ""
    runtimes: List[str] = field(default_factory=list)
    services: List[str] = field(default_factory=list)
    inference: List[str] = field(default_factory=list)
    models: List[str] = field(default_factory=list)

    def all_resource_names(self) -> List[str]:
        return [*self.runtimes, *self.services, *self.inference, *self.models]


@dataclass
class Layout:
    """``layout.yaml`` 顶层视图。"""

    path: Path
    resources_by_name: Dict[str, Resource] = field(default_factory=dict)
    releases: Dict[str, ReleaseSpec] = field(default_factory=dict)

    # ---- 查询 ----------------------------------------------------------

    def get(self, name: str) -> Resource:
        if name not in self.resources_by_name:
            raise KeyError(f"resource {name!r} not in layout")
        return self.resources_by_name[name]

    def release(self, name: str) -> ReleaseSpec:
        if name not in self.releases:
            raise KeyError(f"release {name!r} not in layout")
        return self.releases[name]

    def resources_for_release(self, name: str) -> List[Resource]:
        spec = self.release(name)
        out: List[Resource] = []
        for n in spec.all_resource_names():
            if n in self.resources_by_name:
                out.append(self.resources_by_name[n])
            else:
                logger.warning("[manifest] release %s 引用了未定义资源 %s", name, n)
        return out

    def all_resources(self) -> List[Resource]:
        return list(self.resources_by_name.values())


# ---- 解析 ------------------------------------------------------------------


def _make_resource(kind: str, raw: dict, *, release_tags: Dict[str, List[str]]) -> Resource:
    name = raw["name"]
    return Resource(
        name=name, kind=kind,
        dest=str(raw["dest"]).rstrip("/"),
        source=str(raw.get("source", "")),
        version=str(raw.get("version", "")),
        optional=bool(raw.get("optional", False)),
        note=str(raw.get("note", "")),
        matrix=dict(raw.get("matrix", {}) or {}),
        repo=str(raw.get("repo", "")),
        file=str(raw.get("file", "")),
        unpack=str(raw.get("unpack", "")),
        release_tags=list(release_tags.get(name, [])),
    )


def load_layout(path: Path | str) -> Layout:
    """读 ``layout.yaml`` → :class:`Layout`。

    Raises:
        FileNotFoundError / yaml.YAMLError / ValueError
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"layout.yaml not found: {p}")
    raw: dict = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    # 收集每个资源在 release 中的 tag（lite/standard/pro/...）
    release_tags: Dict[str, List[str]] = {}
    for rname, rraw in (raw.get("releases") or {}).items():
        for cat in ("runtimes", "services", "inference", "models"):
            for n in (rraw or {}).get(cat, []) or []:
                release_tags.setdefault(n, []).append(rname)

    resources: Dict[str, Resource] = {}
    for kind in ("runtimes", "services", "inference", "models"):
        for rraw in (raw.get(kind) or []):
            r = _make_resource(kind, rraw, release_tags=release_tags)
            if r.name in resources:
                raise ValueError(f"duplicate resource name: {r.name}")
            resources[r.name] = r

    releases: Dict[str, ReleaseSpec] = {}
    for rname, rraw in (raw.get("releases") or {}).items():
        rraw = rraw or {}
        releases[rname] = ReleaseSpec(
            name=rname,
            description=str(rraw.get("description", "")),
            runtimes=list(rraw.get("runtimes", []) or []),
            services=list(rraw.get("services", []) or []),
            inference=list(rraw.get("inference", []) or []),
            models=list(rraw.get("models", []) or []),
        )

    return Layout(path=p, resources_by_name=resources, releases=releases)


__all__ = ["Resource", "ReleaseSpec", "Layout", "load_layout"]
