#!/usr/bin/env python3
# ruff: noqa: W605
r"""扫描 ``chayuan-server/vendor/services/`` 报告本次打包要装的运行时 binary。

用途
====

跟 ``check-bundled-models.py`` 配套:打包前 ``build-desktop.{sh,cmd,ps1}``
调一次,把 ``vendor/services/<engine>-server/<platform>/`` 下的二进制按
engine + platform 分组打印,让运维 / 开发明确**本次产物**会带哪些 launcher
binary(llama-server / whisper-server etc.)。

退出码
======

* ``0``  扫到 ≥1 个 engine 在目标平台有 binary(集成版可以建)
* ``1``  目标平台某个 engine 缺 binary(``--require`` 指定时;否则不报错)
* ``2``  vendor/services/ 目录本身不存在

输出格式
========

人类可读:

::

    services 体检 — D:\code\chayuan\...\vendor\services (target: win-x64)
    ├── llama-server   ✓ win-x64/    (12.1 MB, 4 文件)
    │       · win-x64-noavx/ (11.8 MB, 4 文件)
    │       · win-arm64/     (9.3 MB,  3 文件)
    └── whisper-server ✓ win-x64/    (8.4 MB,  2 文件)

JSON 模式(``--json``):机器消费用。

CLI
===

::

    # 默认相对路径,扫所有 engine 所有 platform
    python scripts/check-services.py

    # 显式根
    python scripts/check-services.py --root /path/to/chayuan-server/vendor/services

    # JSON
    python scripts/check-services.py --json

    # 集成版构建要求 win-x64 target 必须 llama + whisper 都齐
    python scripts/check-services.py --target win-x64 --require llama-server --require whisper-server
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional


# 主 binary 名约定 — sync_services 复制后 chayuan-server 运行时按这套查找。
# 注:扁平 layout(同名 binary 直接在 engine 根目录),find_server_exe 兜底命中。
_ENGINE_HINTS = {
    "llama-server":     ("llama-server.exe", "llama-server"),
    "whisper-server":   ("whisper-server.exe", "whisper-server"),
    "rapidocr-server":  ("rapidocr_server.exe", "rapidocr_server"),
    "piper":            ("piper.exe", "piper"),
}

# 与 build.py rust_target_triple / triple_to_vendor_subdir 对齐的 platform 命名
_ALL_PLATFORMS = (
    "win-x64", "win-x64-avx", "win-x64-noavx", "win-x64-avx512", "win-arm64",
    "linux-x64", "linux-arm64",
    "macos-x64", "macos-arm64",
)


@dataclass
class PlatformReport:
    name: str
    n_files: int = 0
    size_bytes: int = 0
    has_main_binary: bool = False
    binaries: List[str] = field(default_factory=list)  # 主 binary 名(若找到)

    @property
    def total_human(self) -> str:
        return _human_size(self.size_bytes)


@dataclass
class EngineReport:
    engine: str
    platforms: List[PlatformReport] = field(default_factory=list)

    @property
    def has_any(self) -> bool:
        return any(p.has_main_binary for p in self.platforms)

    def for_target(self, target: Optional[str]) -> Optional[PlatformReport]:
        if not target:
            return next((p for p in self.platforms if p.has_main_binary), None)
        return next((p for p in self.platforms if p.name == target), None)


def _human_size(n: int) -> str:
    n_f: float = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n_f < 1024:
            return f"{n_f:.0f} {unit}" if unit == "B" else f"{n_f:.2f} {unit}"
        n_f /= 1024
    return f"{n_f:.2f} PB"


def _iter_files(p: Path) -> Iterable[Path]:
    try:
        for sub in p.rglob("*"):
            if sub.is_file() and not sub.name.startswith("."):
                yield sub
    except OSError:
        return


def _scan_platform(engine: str, plat_dir: Path) -> PlatformReport:
    rep = PlatformReport(name=plat_dir.name)
    if not plat_dir.is_dir():
        return rep
    main_names = _ENGINE_HINTS.get(engine, (f"{engine}.exe", engine))
    for f in _iter_files(plat_dir):
        rep.n_files += 1
        rep.size_bytes += f.stat().st_size
        if f.name in main_names:
            rep.has_main_binary = True
            rep.binaries.append(f.name)
    return rep


def _scan_engine(engine_dir: Path) -> EngineReport:
    rep = EngineReport(engine=engine_dir.name)
    if not engine_dir.is_dir():
        return rep
    for plat in sorted(engine_dir.iterdir()):
        if not plat.is_dir() or plat.name.startswith("."):
            continue
        rep.platforms.append(_scan_platform(engine_dir.name, plat))
    # 兜底:扁平 layout(engine_dir 直接含 main binary,没 platform 子目录)
    main_names = _ENGINE_HINTS.get(engine_dir.name, (f"{engine_dir.name}.exe", engine_dir.name))
    flat_files = [
        f for f in engine_dir.iterdir()
        if f.is_file() and not f.name.startswith(".")
    ]
    if flat_files:
        flat_rep = PlatformReport(name="(flat)")
        for f in flat_files:
            flat_rep.n_files += 1
            flat_rep.size_bytes += f.stat().st_size
            if f.name in main_names:
                flat_rep.has_main_binary = True
                flat_rep.binaries.append(f.name)
        if flat_rep.n_files > 0:
            rep.platforms.insert(0, flat_rep)
    return rep


def _default_root() -> Path:
    here = Path(__file__).resolve().parent  # scripts/
    return here.parent / "chayuan-server" / "vendor" / "services"


def _render_text(root: Path, engines: List[EngineReport], target: Optional[str]) -> List[str]:
    lines: List[str] = []
    target_label = f" (target: {target})" if target else ""
    lines.append(f"services 体检 — {root}{target_label}")
    for i, e in enumerate(engines):
        prefix = "└──" if i == len(engines) - 1 else "├──"
        cont   = "    " if i == len(engines) - 1 else "│   "
        if not e.platforms:
            lines.append(f"  {prefix} {e.engine:<16} (空,vendor 没放任何 platform 子目录)")
            continue
        # 主行:目标 platform 优先,否则第一条有 binary 的
        head = e.for_target(target) or next((p for p in e.platforms if p.has_main_binary), e.platforms[0])
        ok_mark = "✓" if head.has_main_binary else "✗"
        bin_label = ", ".join(head.binaries) if head.binaries else "(无主 binary)"
        lines.append(
            f"  {prefix} {e.engine:<16} {ok_mark} {head.name}/   "
            f"({head.total_human}, {head.n_files} 文件;{bin_label})"
        )
        # 其它 platform 子条目
        for p in e.platforms:
            if p is head:
                continue
            mark = "✓" if p.has_main_binary else "✗"
            lines.append(
                f"  {cont}    {mark} {p.name}/   ({p.total_human}, {p.n_files} 文件)"
            )
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--root", type=Path, default=None,
                    help="vendor/services 根(默认 chayuan-server/vendor/services)")
    ap.add_argument("--target", type=str, default=None,
                    metavar="PLATFORM",
                    help="目标 platform(如 win-x64),--require 验证它而不是任意 platform")
    ap.add_argument("--require", action="append", default=[],
                    metavar="ENGINE",
                    help="必须有 binary 的 engine,可多次;缺则退出码 1")
    ap.add_argument("--json", action="store_true", help="JSON 输出(机器消费)")
    args = ap.parse_args()

    root: Path = (args.root or _default_root()).resolve()
    if not root.is_dir():
        msg = f"vendor/services 目录不存在: {root}"
        if args.json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print(f"[FAIL] {msg}", file=sys.stderr)
        return 2

    engines: List[EngineReport] = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            engines.append(_scan_engine(entry))

    missing_required = []
    for req in args.require:
        e = next((x for x in engines if x.engine == req), None)
        if e is None:
            missing_required.append(req)
            continue
        target_rep = e.for_target(args.target) if args.target else None
        if args.target:
            if target_rep is None or not target_rep.has_main_binary:
                missing_required.append(f"{req}@{args.target}")
        else:
            if not e.has_any:
                missing_required.append(req)

    if args.json:
        out = {
            "ok": bool(engines) and not missing_required,
            "root": str(root),
            "target": args.target,
            "engines": [
                {
                    "engine": e.engine,
                    "has_any": e.has_any,
                    "platforms": [
                        {
                            "name": p.name,
                            "n_files": p.n_files,
                            "size_bytes": p.size_bytes,
                            "has_main_binary": p.has_main_binary,
                            "binaries": p.binaries,
                        }
                        for p in e.platforms
                    ],
                }
                for e in engines
            ],
            "missing_required": missing_required,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for line in _render_text(root, engines, args.target):
            print(line)
        if missing_required:
            print(f"[FAIL] --require 缺失: {', '.join(missing_required)}", file=sys.stderr)

    if missing_required:
        return 1
    if not engines or not any(e.has_any for e in engines):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
