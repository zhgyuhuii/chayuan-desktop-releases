#!/usr/bin/env python3
# ruff: noqa: W605
r"""扫描 ``chayuan-server/vendor/torch_wheels/`` 报告本次打包要带哪些平台 wheel。

用途
====

跟 ``check-bundled-models.py`` / ``check-services.py`` 配套:打包前
``build-desktop.{sh,cmd,ps1}`` 调一次,把 ``vendor/torch_wheels/<sub>/*.whl``
按 ``<platform>_py<ver>_<variant>`` 分组打印,让运维 / 开发明确**本次产物**
会带哪些平台的 torch 离线安装包。

退出码
======

* ``0``  扫到 ≥1 个 wheel 配对(torch + torchvision 同 subdir),host 平台齐
* ``1``  host 平台不齐(--require 指定平台缺也算)
* ``2``  vendor/torch_wheels/ 目录本身不存在

输出格式
========

人类可读::

    torch_wheels 体检 — D:\code\chayuan\...\vendor\torch_wheels (host: win_amd64_py312_cpu)
    ├── win_amd64_py312_cpu          ✓ torch(254 MB) + torchvision(7 MB)   [host]
    ├── manylinux2014_x86_64_py312_cpu ✓ torch(252 MB) + torchvision(7 MB)
    ├── macosx_11_0_arm64_py312_cpu  ✓ torch(70 MB)  + torchvision(7 MB)
    └── macosx_10_9_x86_64_py312_cpu ✗ 空 / 不完整(缺 torchvision)

JSON 模式 (``--json``):机器消费用。

CLI
===

::

    # 默认扫所有平台
    python scripts/check-torch-wheels.py

    # 强制要求 host 平台齐(build-desktop 默认走这个)
    python scripts/check-torch-wheels.py --require-host

    # JSON 给 CI
    python scripts/check-torch-wheels.py --json
"""
from __future__ import annotations

import argparse
import json
import platform as _plat
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class WheelGroup:
    """一个 ``<platform>_py<ver>_<variant>`` 子目录的状态。"""

    subdir: str
    has_torch: bool = False
    has_torchvision: bool = False
    torch_size: int = 0
    torchvision_size: int = 0
    extra_files: List[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.has_torch and self.has_torchvision

    @property
    def total_bytes(self) -> int:
        return self.torch_size + self.torchvision_size


def _human(n: int) -> str:
    n_f: float = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if n_f < 1024:
            return f"{n_f:.0f} {u}" if u == "B" else f"{n_f:.1f} {u}"
        n_f /= 1024
    return f"{n_f:.1f} TB"


def _detect_host_subdir(py_versions: List[str], variants: List[str]) -> str:
    """根据本机 OS+arch 拼 host subdir(取 py_versions / variants 第一个)。

    例:Linux x64 + py312 + cpu → ``manylinux2014_x86_64_py312_cpu``。
    返回 ``""`` = 平台不在已知映射(罕见架构),build-desktop 不强制 host 检查。
    """
    sys_name = _plat.system().lower()
    machine = _plat.machine().lower()
    is_x64 = machine in ("x86_64", "amd64")
    is_arm64 = machine in ("arm64", "aarch64")

    plat_tag = ""
    if sys_name == "windows" and is_x64:
        plat_tag = "win_amd64"
    elif sys_name == "linux" and is_x64:
        plat_tag = "manylinux2014_x86_64"
    elif sys_name == "linux" and is_arm64:
        plat_tag = "manylinux2014_aarch64"
    elif sys_name == "darwin" and is_arm64:
        plat_tag = "macosx_11_0_arm64"
    elif sys_name == "darwin" and is_x64:
        plat_tag = "macosx_10_9_x86_64"
    if not plat_tag:
        return ""
    py = py_versions[0] if py_versions else "312"
    var = variants[0] if variants else "cpu"
    return f"{plat_tag}_py{py}_{var}"


_WHEEL_TORCH_RE = re.compile(r"^torch-[0-9]", re.IGNORECASE)
_WHEEL_TV_RE = re.compile(r"^torchvision-[0-9]", re.IGNORECASE)


def _scan_subdir(d: Path) -> WheelGroup:
    g = WheelGroup(subdir=d.name)
    if not d.is_dir():
        return g
    for f in d.iterdir():
        if not f.is_file() or f.name.startswith("."):
            continue
        if f.suffix.lower() != ".whl":
            g.extra_files.append(f.name)
            continue
        if _WHEEL_TORCH_RE.match(f.name):
            g.has_torch = True
            g.torch_size = f.stat().st_size
        elif _WHEEL_TV_RE.match(f.name):
            g.has_torchvision = True
            g.torchvision_size = f.stat().st_size
        else:
            g.extra_files.append(f.name)
    return g


def _default_root() -> Path:
    here = Path(__file__).resolve().parent
    return here.parent / "chayuan-server" / "vendor" / "torch_wheels"


def _render_text(root: Path, groups: List[WheelGroup], host_subdir: str) -> List[str]:
    lines: List[str] = []
    host_label = f" (host: {host_subdir})" if host_subdir else " (host: 未知架构)"
    lines.append(f"torch_wheels 体检 — {root}{host_label}")
    for i, g in enumerate(groups):
        prefix = "└──" if i == len(groups) - 1 else "├──"
        is_host = g.subdir == host_subdir
        host_marker = "  [host]" if is_host else ""
        if g.is_complete:
            t = _human(g.torch_size)
            v = _human(g.torchvision_size)
            lines.append(
                f"  {prefix} {g.subdir:<36} ✓ torch({t}) + torchvision({v}){host_marker}"
            )
        elif g.has_torch or g.has_torchvision:
            missing = "torchvision" if g.has_torch else "torch"
            lines.append(
                f"  {prefix} {g.subdir:<36} ✗ 缺 {missing}{host_marker}"
            )
        else:
            lines.append(
                f"  {prefix} {g.subdir:<36} ✗ 空 / 没 wheel{host_marker}"
            )
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--root", type=Path, default=None,
                    help="vendor/torch_wheels 根(默认 chayuan-server/vendor/torch_wheels)")
    ap.add_argument("--py-versions", default="312",
                    help="逗号分隔 Python 版本,用于探 host subdir;默认 312")
    ap.add_argument("--variants", default="cpu",
                    help="逗号分隔 variant,用于探 host subdir;默认 cpu")
    ap.add_argument("--require-host", action="store_true",
                    help="host 平台必须有完整 torch+torchvision;缺则退出 1")
    ap.add_argument("--require", action="append", default=[],
                    metavar="SUBDIR",
                    help="必须存在的 subdir(完整),可多次。缺则退出 1")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    root: Path = (args.root or _default_root()).resolve()
    if not root.is_dir():
        msg = f"vendor/torch_wheels 目录不存在: {root}"
        if args.json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print(f"[FAIL] {msg}", file=sys.stderr)
        return 2

    py_versions = [v.strip() for v in args.py_versions.split(",") if v.strip()]
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    host_subdir = _detect_host_subdir(py_versions, variants)

    groups: List[WheelGroup] = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            groups.append(_scan_subdir(entry))

    missing_required: List[str] = []
    for req in args.require:
        g = next((x for x in groups if x.subdir == req), None)
        if g is None or not g.is_complete:
            missing_required.append(req)
    host_missing = False
    if args.require_host and host_subdir:
        host_g = next((x for x in groups if x.subdir == host_subdir), None)
        if host_g is None or not host_g.is_complete:
            host_missing = True

    any_complete = any(g.is_complete for g in groups)

    if args.json:
        print(json.dumps({
            "ok": any_complete and not missing_required and not host_missing,
            "root": str(root),
            "host_subdir": host_subdir,
            "groups": [
                {
                    "subdir": g.subdir,
                    "complete": g.is_complete,
                    "has_torch": g.has_torch,
                    "has_torchvision": g.has_torchvision,
                    "torch_size": g.torch_size,
                    "torchvision_size": g.torchvision_size,
                    "extra_files": g.extra_files,
                }
                for g in groups
            ],
            "missing_required": missing_required,
            "host_missing": host_missing,
        }, ensure_ascii=False, indent=2))
    else:
        for line in _render_text(root, groups, host_subdir):
            print(line)
        if host_missing:
            print(f"[FAIL] --require-host:host 平台 {host_subdir!r} 不齐",
                  file=sys.stderr)
        if missing_required:
            print(f"[FAIL] --require 缺失: {', '.join(missing_required)}",
                  file=sys.stderr)

    if missing_required or host_missing:
        return 1
    if not any_complete:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
