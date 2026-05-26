"""Linux 安装包：优先 AppImage，缺 appimagetool 时回退到 ``tar.zst``。"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tarfile
from pathlib import Path

logger = logging.getLogger("chayuan_packaging.targets.linux")


class LinuxFinalizer:
    name = "linux"

    def finalize(self, *, staging: Path, out_dir: Path, platform: str,
                 release: str, version: str) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        arch = platform.split("-", 1)[1]   # x86_64 / arm64
        appimagetool = shutil.which("appimagetool") or shutil.which("appimagetool-x86_64.AppImage")
        if appimagetool:
            return self._make_appimage(
                staging, out_dir, arch=arch, release=release,
                version=version, tool=appimagetool,
            )
        logger.warning("[linux] appimagetool 不在 PATH；回退到 tar.zst")
        return self._make_tarzst(staging, out_dir, arch=arch, release=release, version=version)

    # ---- 子目标 --------------------------------------------------------

    def _make_appimage(self, staging: Path, out_dir: Path,
                       *, arch: str, release: str, version: str,
                       tool: str) -> Path:
        appdir = out_dir / "Chayuan.AppDir"
        if appdir.exists():
            shutil.rmtree(appdir)
        shutil.copytree(staging, appdir)

        # AppRun 脚本
        apprun = appdir / "AppRun"
        apprun.write_text(_APPRUN_SH, encoding="utf-8")
        apprun.chmod(0o755)

        # .desktop & icon（最小占位）
        (appdir / "chayuan.desktop").write_text(_DESKTOP, encoding="utf-8")
        if not (appdir / "chayuan.png").exists():
            (appdir / "chayuan.png").write_bytes(b"")  # AppImage 允许空 PNG

        out = out_dir / f"chayuan-{version}-{release}-linux-{arch}.AppImage"
        env = {"ARCH": "x86_64" if arch == "x86_64" else "aarch64"}
        try:
            subprocess.run([tool, str(appdir), str(out)],
                           check=True, env={**env, "PATH": "/usr/bin:/bin:/usr/local/bin"})
        except subprocess.CalledProcessError as e:
            logger.warning("[linux] appimagetool 失败：%r；回退 tar.zst", e)
            return self._make_tarzst(staging, out_dir, arch=arch,
                                     release=release, version=version)
        return out

    def _make_tarzst(self, staging: Path, out_dir: Path,
                     *, arch: str, release: str, version: str) -> Path:
        out = out_dir / f"chayuan-{version}-{release}-linux-{arch}.tar.zst"
        try:
            import zstandard as zstd
        except ImportError:
            # 退一步用 tar.gz
            out = out.with_suffix("").with_suffix(".tar.gz")
            with tarfile.open(out, "w:gz") as tf:
                tf.add(staging, arcname="chayuan")
            return out
        cctx = zstd.ZstdCompressor(level=15, threads=-1)
        with out.open("wb") as raw, cctx.stream_writer(raw) as zw, \
                tarfile.open(fileobj=zw, mode="w|") as tf:
            tf.add(staging, arcname="chayuan")
        return out


_APPRUN_SH = """#!/usr/bin/env bash
# 察元 AppImage 启动器
HERE="$(dirname "$(readlink -f "$0")")"
export CHAYUAN_HOME="${CHAYUAN_HOME:-$HOME/.chayuan}"
mkdir -p "$CHAYUAN_HOME/logs"
cd "$HERE"
exec "$HERE/vendor/runtimes/python/bin/python" -m chayuan start -a 2>>"$CHAYUAN_HOME/logs/launch.err"
"""

_DESKTOP = """[Desktop Entry]
Name=Chayuan
Comment=察元多模态 AI 一体化平台
Exec=AppRun
Icon=chayuan
Type=Application
Categories=Development;
"""


__all__ = ["LinuxFinalizer"]
