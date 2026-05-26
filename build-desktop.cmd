@echo off
REM One-click build wrapper. Bypasses PS execution policy + sets UTF-8 console.
REM
REM Usage:
REM     build-desktop.cmd                       full build = lite + integrated
REM     build-desktop.cmd -LiteOnly             only the lite flavor (no bundled models)
REM     build-desktop.cmd -IntegratedOnly       only the integrated flavor (with bundled models)
REM     build-desktop.cmd -SkipModelCheck       skip vendor/bundled_models inventory scan
REM     build-desktop.cmd -SkipServer           skip PyInstaller, only Tauri
REM     build-desktop.cmd -Clean                clean rebuild
REM     build-desktop.cmd -SkipCleanTarget      skip the auto target\ wipe (fast incremental build)
REM     build-desktop.cmd -NoColor              old console, disable ANSI color
REM
REM NOTE: every build wipes apps\desktop\src-tauri\target\ first, so each
REM       package is a clean full cold build (slower: Rust deps recompile).
REM       Pass -SkipCleanTarget for a fast incremental build, or use
REM       -BundleOnly which reuses the cache and skips the wipe automatically.
REM
REM Outputs:
REM     dist-lite\          lite flavor installers
REM     dist-integrated\    integrated flavor installers
REM     bundled models: see chayuan-server\vendor\bundled_models\README.md
REM
REM NOTE: This .cmd is intentionally ASCII-only because cmd.exe parses
REM       batch files using the OEM code page (GBK on Chinese Windows),
REM       so any non-ASCII char would be mojibake. Chinese output is
REM       handled by the .ps1 (UTF-8 BOM + chcp 65001 inside the script).

setlocal

REM Run from the directory containing this .cmd (cwd may differ on double-click)
cd /d "%~dp0"

REM Pre-set console to UTF-8 so Chinese output from .ps1 doesn't mojibake
chcp 65001 > nul 2>&1

REM Locate a PowerShell host. Prefer Windows PS 5.1 (always present); fall
REM back to PowerShell 7 (pwsh) for slim systems where powershell.exe is gone
REM or stripped from PATH.
set "PS_EXE="
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" (
    set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
) else (
    where powershell.exe >nul 2>&1 && set "PS_EXE=powershell.exe"
)
if "%PS_EXE%"=="" (
    where pwsh.exe >nul 2>&1 && set "PS_EXE=pwsh.exe"
)
if "%PS_EXE%"=="" (
    echo [FAIL] No PowerShell host found ^(neither powershell.exe nor pwsh.exe^).
    echo        Install PowerShell 7: https://github.com/PowerShell/PowerShell/releases
    pause
    exit /b 127
)

echo Using PowerShell host: %PS_EXE%

REM -ExecutionPolicy Bypass: only this process, does not change system policy
REM -NoProfile: skip user profile for faster cold start + cleaner env
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-desktop.ps1" %*

set ERR=%ERRORLEVEL%
if %ERR% NEQ 0 (
  echo.
  echo [FAIL] Build failed with exit code %ERR%
  pause
)
exit /b %ERR%
