"""Windows 安装包：优先 NSIS（``makensis``），缺工具时回退到 zip。

NSIS 模板从 ``packaging/windows/Chayuan.nsi``（已有）派生；
本模块只生成 staging 目录、launcher 和最终产物。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import zipfile
from pathlib import Path

logger = logging.getLogger("chayuan_packaging.targets.windows")


class WindowsFinalizer:
    name = "windows"

    def finalize(self, *, staging: Path, out_dir: Path, platform: str,
                 release: str, version: str) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        arch = platform.split("-", 1)[1]

        # 写 launcher.bat
        launcher = staging / "chayuan.bat"
        launcher.write_text(_LAUNCHER_BAT, encoding="cp936", errors="ignore")

        # 1) NSIS
        makensis = shutil.which("makensis")
        if makensis:
            nsi_template = staging / "_installer.nsi"
            nsi_template.write_text(
                _NSI_TEMPLATE
                .replace("__VERSION__", version)
                .replace("__RELEASE__", release)
                .replace("__ARCH__", arch),
                encoding="utf-8",
            )
            out = out_dir / f"chayuan-{version}-{release}-win-{arch}-setup.exe"
            try:
                subprocess.run([
                    makensis, f"/DOUTFILE={out}", str(nsi_template),
                ], cwd=staging, check=True)
                return out
            except subprocess.CalledProcessError as e:
                logger.warning("[windows] makensis 失败：%r；回退 zip", e)

        # 2) 兜底 zip
        out = out_dir / f"chayuan-{version}-{release}-win-{arch}.zip"
        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in staging.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(staging))
        return out


_LAUNCHER_BAT = r"""@echo off
REM 察元 Windows 启动器
set "HERE=%~dp0"
if not defined CHAYUAN_HOME set "CHAYUAN_HOME=%USERPROFILE%\.chayuan"
if not exist "%CHAYUAN_HOME%\logs" mkdir "%CHAYUAN_HOME%\logs"
"%HERE%vendor\runtimes\python\python.exe" -m chayuan start -a >> "%CHAYUAN_HOME%\logs\launch.log" 2>&1
"""

_NSI_TEMPLATE = r"""!define APPNAME "Chayuan"
!define COMPANYNAME "Chayuan Team"
!define DESCRIPTION "察元多模态 AI 一体化平台"
!define VERSIONMAJOR 1
!define VERSIONMINOR 0
!define INSTALLSIZE 3000000

OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\Chayuan"
RequestExecutionLevel admin

Page directory
Page instfiles

Section "Install"
    SetOutPath $INSTDIR
    File /r "*.*"
    CreateDirectory "$SMPROGRAMS\Chayuan"
    CreateShortCut  "$SMPROGRAMS\Chayuan\Chayuan.lnk" "$INSTDIR\chayuan.bat" "" "$INSTDIR\chayuan.bat"
    WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
    Delete   "$INSTDIR\uninstall.exe"
    RMDir /r "$INSTDIR"
    Delete   "$SMPROGRAMS\Chayuan\Chayuan.lnk"
SectionEnd
"""


__all__ = ["WindowsFinalizer"]
