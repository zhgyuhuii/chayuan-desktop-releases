@echo off
REM dev-all.cmd — Windows 双击入口,一键拉起 server + 6 个模型服务 + 桌面 app
REM 详细说明见 dev-all.ps1 顶部注释

chcp 65001 > nul 2>&1
setlocal

set "SCRIPT_DIR=%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%dev-all.ps1" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo dev-all.ps1 退出码 %RC%
    echo 看上面的红色 / 黄色提示。
    pause
)

exit /b %RC%
