@echo off
REM 双击入口:体检 Windows 装 MSI 的环境前置条件
REM (杀软 / C: 盘空间 / MSI 服务 / 缓存目录权限 / Win 版本)

setlocal
cd /d "%~dp0\.."
chcp 65001 > nul 2>&1

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
    echo [FAIL] No PowerShell host found.
    pause
    exit /b 127
)

"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose-msi-env.ps1" %*
echo.
echo === 体检结束,按任意键关闭 ===
pause > nul
exit /b %ERRORLEVEL%
