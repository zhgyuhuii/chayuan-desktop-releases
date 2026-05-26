@echo off
REM dev-start.cmd — Windows 双击启动入口,代理到 dev-start.ps1
REM
REM 1. chcp 65001 保证终端 UTF-8(防中文乱码)
REM 2. -ExecutionPolicy Bypass 绕过未签名脚本拒跑
REM 3. -NoProfile 跳过用户 PS profile,免被自定义 alias 干扰

chcp 65001 > nul 2>&1
setlocal

set "SCRIPT_DIR=%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%dev-start.ps1" %*
set "RC=%ERRORLEVEL%"

if not "%1"=="--quiet" if not "%RC%"=="0" (
    echo.
    echo dev-start.ps1 退出码 %RC%
    echo 看一下上面的红色 / 黄色提示,按 ↑ 重跑或加 -CheckOnly 只 preflight。
    pause
)

exit /b %RC%
