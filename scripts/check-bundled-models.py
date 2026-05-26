#!/usr/bin/env python3
"""扫描 ``chayuan-server/vendor/bundled_models/`` 报告本次打包要打哪些模型。

用途
====

打包前由 ``build-desktop.{sh,cmd,ps1}`` 调一次,把当前 bundled_models 目录
内的文件按 capability 分组打印,让运维 / 开发明确**本次产物**会带哪些权重。

退出码
======

* ``0``  扫到 ≥1 个有效模型(集成版可以建)
* ``1``  目录全空(只有 .gitkeep)— 集成版构建会等价于轻量版
* ``2``  bundled_models 目录本身不存在(异常)

输出格式
========

人类可读:

::

    bundled_models 体检
    ├── chat        ✓ qwen3-4b-q4_k_m.gguf (2.45 GB)
    ├── embedding   ✓ bge-m3/ (568 MB, multi-file repo)
    ├── rerank      ✓ bge-reranker-v2-m3/ (561 MB, multi-file repo)
    ├── asr         ✓ ggml-tiny.bin (74 MB)
    ├── ocr         (空)
    ├── image       (空)
    └── custom      (空)
    总计:4 个 capability 有模型,总大小 ~3.6 GB

JSON 模式(``--json``):机器消费用。

CLI
===

::

    # 默认相对路径
    python scripts/check-bundled-models.py

    # 显式根
    python scripts/check-bundled-models.py --root /path/to/chayuan-server/vendor/bundled_models

    # JSON
    python scripts/check-bundled-models.py --json

    # 集成版构建要求:某 cap 必须有模型,否则非 0 退出
    python scripts/check-bundled-models.py --require chat --require embedding
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# 与 chayuan-server local_index.BUNDLED_CAPABILITY_DIRS 对齐
_CAPS = ("chat", "embedding", "rerank", "asr", "ocr", "image", "custom")

# 单文件直接当模型的扩展名(其余扩展需要同目录有标志文件)
_SINGLE_FILE_FORMATS = (".gguf", ".onnx", ".safetensors", ".bin", ".pt", ".pth")
# 目录里若同时出现这些标志文件 -> 视为完整模型仓库
_DIR_MARKERS = ("config.json", "model_index.json", "tokenizer.json", "manifest.json")


@dataclass
class CapReport:
    cap: str
    items: list[dict] = field(default_factory=list)  # {name, kind, size_bytes}

    @property
    def has_models(self) -> bool:
        return bool(self.items)

    @property
    def total_bytes(self) -> int:
        return sum(int(it["size_bytes"]) for it in self.items)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.2f} PB"


def _iter_files(p: Path) -> Iterable[Path]:
    try:
        for sub in p.rglob("*"):
            if sub.is_file():
                yield sub
    except OSError:
        return


def _dir_total_size(p: Path) -> int:
    return sum(f.stat().st_size for f in _iter_files(p))


def _is_dir_repo(p: Path) -> bool:
    if not p.is_dir():
        return False
    for marker in _DIR_MARKERS:
        if (p / marker).is_file():
            return True
    # 任何模型权重文件存在即视为完整仓库:
    # - .gguf / .onnx 单文件量化(目录里只有一个 .gguf 也是 vendor 合规摆法,
    #   如 chat/<repo>/foo.gguf,跟 README 的"chat/<repo>/...gguf"约定一致)
    # - .safetensors / .bin (Transformers 风格的多文件仓库)
    # - .pt / .pth (PyTorch checkpoint)
    for ext in _SINGLE_FILE_FORMATS:
        if any(p.glob(f"*{ext}")):
            return True
    return False


def _scan_cap(cap_dir: Path, max_depth: int = 5) -> CapReport:
    """递归扫一个 cap 目录,枚举**所有**模型文件 / 仓库子项。

    与 build.py ``sync_bundled_models`` 的 rglob 行为对齐 — 凡是会被打进
    Tauri resources 的内容,都该出现在 check 报告里。

    判定规则:
      - 子目录里有标志文件 (config.json / tokenizer.json / 任意权重)
        → 当成一个完整仓库,不再下钻(避免把仓库内的 sharded weight 拆成多条)
      - 散落的 .gguf / .onnx / .safetensors / .bin / .pt / .pth
        → 每个文件一条
      - 既不是仓库也不带模型扩展的目录,继续下钻(覆盖 chat/foo/bar/...gguf 这种嵌套)
    """
    rep = CapReport(cap=cap_dir.name)
    if not cap_dir.is_dir():
        return rep

    def _walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for entry in sorted(d.iterdir()):
            if entry.name.startswith(".") or entry.name in ("__pycache__",):
                continue
            if entry.is_dir():
                if _is_dir_repo(entry):
                    rep.items.append({
                        "name": entry.relative_to(cap_dir).as_posix() + "/",
                        "kind": "multi-file-repo",
                        "size_bytes": _dir_total_size(entry),
                    })
                    # 仓库当原子单元,不下钻
                else:
                    _walk(entry, depth + 1)
            elif entry.is_file():
                if entry.suffix.lower() in _SINGLE_FILE_FORMATS:
                    rep.items.append({
                        "name": entry.relative_to(cap_dir).as_posix(),
                        "kind": "single-file",
                        "size_bytes": entry.stat().st_size,
                    })

    _walk(cap_dir, depth=0)
    return rep


def _default_root() -> Path:
    here = Path(__file__).resolve().parent  # scripts/
    return here.parent / "chayuan-server" / "vendor" / "bundled_models"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=None,
                    help="bundled_models 根(默认 chayuan-server/vendor/bundled_models)")
    ap.add_argument("--json", action="store_true", help="JSON 输出(机器消费)")
    ap.add_argument("--require", action="append", default=[],
                    metavar="CAP",
                    help="集成版强制要求 cap 有模型,可多次;无则退出码 1")
    args = ap.parse_args()

    root: Path = (args.root or _default_root()).resolve()
    if not root.is_dir():
        msg = f"bundled_models 目录不存在: {root}"
        if args.json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print(f"[FAIL] {msg}", file=sys.stderr)
        return 2

    reports = [_scan_cap(root / cap) for cap in _CAPS]
    total_caps = sum(1 for r in reports if r.has_models)
    total_bytes = sum(r.total_bytes for r in reports)

    missing_required = [
        cap for cap in args.require if not any(r.cap == cap and r.has_models for r in reports)
    ]

    if args.json:
        out = {
            "ok": total_caps > 0 and not missing_required,
            "root": str(root),
            "caps": [
                {
                    "cap": r.cap,
                    "has_models": r.has_models,
                    "items": r.items,
                    "total_bytes": r.total_bytes,
                }
                for r in reports
            ],
            "total_caps": total_caps,
            "total_bytes": total_bytes,
            "missing_required": missing_required,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"bundled_models 体检 — {root}")
        for i, r in enumerate(reports):
            prefix = "└──" if i == len(reports) - 1 else "├──"
            if not r.has_models:
                print(f"  {prefix} {r.cap:<10} (空)")
                continue
            head = r.items[0]
            print(
                f"  {prefix} {r.cap:<10} ✓ {head['name']} ({_human_size(int(head['size_bytes']))})"
                + (f" + {len(r.items) - 1} 个" if len(r.items) > 1 else "")
            )
            for it in r.items[1:]:
                print(f"  {'    ' if i == len(reports) - 1 else '│   '}    · {it['name']} ({_human_size(int(it['size_bytes']))})")
        print(f"总计:{total_caps} 个 capability 有模型,总大小 ~{_human_size(total_bytes)}")
        if missing_required:
            print(f"[FAIL] --require 缺失: {', '.join(missing_required)}", file=sys.stderr)

    if missing_required:
        return 1
    if total_caps == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
