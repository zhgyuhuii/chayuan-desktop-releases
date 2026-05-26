@echo off
REM 双击入口:诊断 dist-integrated\*.msi 安装失败原因。
REM 把 PS 调起来跑 .ps1,完事按任意键关窗(避免双击立刻闪退看不到结果)。

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

"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose-msi-install.ps1" %*
echo.
echo === 诊断结束,按任意键关闭 ===
pause > nul
exit /b %ERRORLEVEL%
