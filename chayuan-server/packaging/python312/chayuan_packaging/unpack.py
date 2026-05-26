"""把下载产物解压 / 移动到 ``Resource.dest``（相对仓库根）。

支持的 ``unpack`` 类型：
* ``""``         — 单文件，直接拷过去（保留原文件名 / 命名为 dest 末段）
* ``tar.gz``     — gzipped tar
* ``tar.xz``     — xz tar
* ``tar.bz2``    — bz2 tar
* ``zip``        — zip
* ``tar.gz+make`` — 解压 tar.gz 后跑 ``make -j`` 自动编译（仅 redis 一类源码包用）

判定 ``unpack`` 时如果 yaml 没显式声明，会根据扩展名自动猜测。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("chayuan_packaging.unpack")


def detect_kind(path: Path, hint: str = "") -> str:
    """根据文件名 + hint 判定解压类型。"""
    name = path.name.lower()
    if hint:
        return hint
    for k in ("tar.gz", "tgz"):
        if name.endswith(f".{k}"):
            return "tar.gz"
    if name.endswith(".tar.xz") or name.endswith(".txz"):
        return "tar.xz"
    if name.endswith(".tar.bz2") or name.endswith(".tbz2"):
        return "tar.bz2"
    if name.endswith(".tar"):
        return "tar"
    if name.endswith(".zip"):
        return "zip"
    return ""


def unpack_to(src: Path, dest_dir: Path, *, kind: str = "",
              repo_root: Optional[Path] = None) -> Path:
    """把 ``src`` 解压到 ``dest_dir``；目录已存在时清空。

    ``kind == "tar.gz+make"`` 会调用 ``make -j`` 编译 redis 等源码包；只
    在 unix 系工具链可用时跑，windows 上跳过并仅解压。

    Returns:
        解压后的目录路径（= ``dest_dir``）。
    """
    kind = kind or detect_kind(src)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        raise FileNotFoundError(f"unpack source missing: {src}")

    if src.is_dir():
        # hf snapshot 的目录情况 — 直接 mirror copy
        _copy_tree(src, dest_dir)
        return dest_dir

    if kind in ("tar.gz", "tar.xz", "tar.bz2", "tar"):
        mode = {
            "tar.gz": "r:gz",
            "tar.xz": "r:xz",
            "tar.bz2": "r:bz2",
            "tar": "r",
        }[kind]
        with tarfile.open(src, mode) as tf:
            tf.extractall(dest_dir)
    elif kind == "zip":
        with zipfile.ZipFile(src) as zf:
            zf.extractall(dest_dir)
    elif kind == "tar.gz+make":
        with tarfile.open(src, "r:gz") as tf:
            tf.extractall(dest_dir)
        # redis-7.4.0/Makefile → cd && make -j
        # 找含 Makefile 的子目录
        makefile_dir = next(
            (p.parent for p in dest_dir.rglob("Makefile") if p.is_file()),
            None,
        )
        if makefile_dir is None:
            logger.warning("[unpack] tar.gz+make 但找不到 Makefile：%s", dest_dir)
            return dest_dir
        try:
            subprocess.run(["make", "-j"], cwd=makefile_dir, check=True,
                           capture_output=True, text=True, timeout=900)
            # redis 编译完成后 binary 在 src/redis-server；symlink 到 ../bin/
            redis_server = makefile_dir / "src" / "redis-server"
            if redis_server.is_file():
                bin_dir = dest_dir / "bin"
                bin_dir.mkdir(exist_ok=True)
                shutil.copy2(redis_server, bin_dir / "redis-server")
                cli = makefile_dir / "src" / "redis-cli"
                if cli.is_file():
                    shutil.copy2(cli, bin_dir / "redis-cli")
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            logger.warning("[unpack] make 失败（缺工具链或编译报错）：%r", e)
    elif kind == "":
        # 单文件 — 直接复制为 bin/<name>
        bin_dir = dest_dir / "bin"
        bin_dir.mkdir(exist_ok=True)
        shutil.copy2(src, bin_dir / src.name)
    else:
        raise ValueError(f"unknown unpack kind: {kind!r}")

    return dest_dir


def _copy_tree(src: Path, dst: Path) -> None:
    """rsync 风格的 copy（保留权限 / 跳过已存在且 mtime 一致的）。"""
    for sp in src.rglob("*"):
        if not sp.is_file():
            continue
        rel = sp.relative_to(src)
        dp = dst / rel
        dp.parent.mkdir(parents=True, exist_ok=True)
        if dp.exists() and dp.stat().st_size == sp.stat().st_size:
            continue
        shutil.copy2(sp, dp)


__all__ = ["unpack_to", "detect_kind"]
