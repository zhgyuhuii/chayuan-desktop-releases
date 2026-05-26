"""macOS 安装包：优先 ``.dmg``，缺 hdiutil 时回退 ``.tar.gz``。

发布要求：
* 真正用户分发要走 Apple Developer Program 的开发者证书 + notarization；
* 本 finalizer 只生成未签名的产物，签名 / notarize 是 CI 的额外步骤。
"""
from __future__ import annotations

import logging
import plistlib
import shutil
import subprocess
import tarfile
from pathlib import Path

logger = logging.getLogger("chayuan_packaging.targets.mac")


class MacFinalizer:
    name = "mac"

    def finalize(self, *, staging: Path, out_dir: Path, platform: str,
                 release: str, version: str) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        arch = platform.split("-", 1)[1]

        # 1) 先做出 .app bundle
        app_bundle = out_dir / "Chayuan.app"
        if app_bundle.exists():
            shutil.rmtree(app_bundle)
        contents = app_bundle / "Contents"
        macos = contents / "MacOS"
        resources = contents / "Resources"
        macos.mkdir(parents=True)
        resources.mkdir(parents=True)

        # 业务 + vendor + models 全部塞 Resources
        for child in staging.iterdir():
            target = resources / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)

        # MacOS/chayuan launcher
        launcher = macos / "chayuan"
        launcher.write_text(_LAUNCHER_SH.replace(
            "__VERSION__", version,
        ), encoding="utf-8")
        launcher.chmod(0o755)

        # Info.plist
        plist = {
            "CFBundleName": "Chayuan",
            "CFBundleDisplayName": "察元 AI 一体化平台",
            "CFBundleExecutable": "chayuan",
            "CFBundleIdentifier": "com.chayuan.platform",
            "CFBundleVersion": version,
            "CFBundleShortVersionString": version,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        }
        with (contents / "Info.plist").open("wb") as f:
            plistlib.dump(plist, f)

        # 2) 打 dmg（hdiutil 在 macOS 自带）
        if shutil.which("hdiutil"):
            dmg = out_dir / f"chayuan-{version}-{release}-mac-{arch}.dmg"
            try:
                subprocess.run([
                    "hdiutil", "create",
                    "-volname", f"Chayuan {version}",
                    "-srcfolder", str(app_bundle),
                    "-ov", "-format", "UDZO", str(dmg),
                ], check=True)
                return dmg
            except subprocess.CalledProcessError as e:
                logger.warning("[mac] hdiutil 失败：%r；回退 tar.gz", e)

        # 3) 兜底 tar.gz
        out = out_dir / f"chayuan-{version}-{release}-mac-{arch}.tar.gz"
        with tarfile.open(out, "w:gz") as tf:
            tf.add(app_bundle, arcname="Chayuan.app")
        return out


_LAUNCHER_SH = """#!/usr/bin/env bash
# 察元 macOS launcher (v__VERSION__)
APP_DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
export CHAYUAN_HOME="${CHAYUAN_HOME:-$HOME/Library/Application Support/Chayuan}"
mkdir -p "$CHAYUAN_HOME/logs"
exec "$APP_DIR/vendor/runtimes/python/bin/python3" -m chayuan start -a \
    >> "$CHAYUAN_HOME/logs/launch.log" 2>&1
"""


__all__ = ["MacFinalizer"]
