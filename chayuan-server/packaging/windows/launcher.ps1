# ============================================================================
# Chayuan Windows 启动脚本（launcher.ps1）
#
# 对应 macOS 的 launcher.sh。由 launcher.vbs 以 -WindowStyle Hidden 调起。
#
# 工作流程：
#   1. 解析自身路径，定位 .exe 安装目录（含 src\chayuan-server\）和资源；
#   2. 如 %LOCALAPPDATA%\Chayuan\python\pythonw.exe 不存在且 bundle 里有
#      Python runtime tarball，触发首次安装 first_run.ps1；
#   3. 设置 PYTHONPATH / CHAYUAN_ROOT，启动 pythonw -m chayuan.tray.entry；
#   4. pythonw.exe 不带窗口，托盘图标出现在任务栏通知区。
#
# 日志统一落到 %USERPROFILE%\.chayuan\logs\launcher.log，方便复现问题。
# ============================================================================

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# ---- 路径解析 -------------------------------------------------------------
$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
# 安装器布局：<InstallDir>\bin\launcher.ps1，向上一级是 InstallDir
$InstallDir   = Split-Path -Parent $ScriptDir
$ResourcesDir = $InstallDir   # 对齐 macOS 里的 Resources/

$SrcRoot      = Join-Path $ResourcesDir 'src\chayuan-server'
$DistDir      = Join-Path $ResourcesDir 'dist'

# ---- 用户目录（统一到 ~\.chayuan，避免含空格的 AppData 路径）---------------
# Windows 上 %LOCALAPPDATA% 一般是 C:\Users\<name>\AppData\Local（无空格），
# 但 %USERPROFILE% 更稳定，且和 macOS 的 ~/.chayuan 语义一致。
$AppSupport = Join-Path $env:USERPROFILE '.chayuan'
$LogDir     = Join-Path $AppSupport 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LaunchLog  = Join-Path $LogDir 'launcher.log'

function Write-Log($msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $LaunchLog -Value "[$ts] $msg" -Encoding UTF8 -ErrorAction SilentlyContinue
}

function Die-Gui($msg) {
    Write-Log "FATAL: $msg"
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            "Chayuan 启动失败：`n`n$msg`n`n详细日志：$LaunchLog",
            "Chayuan", 'OK', 'Error') | Out-Null
    } catch {
        # WPF 不可用时退回 Win32 MessageBox
        $wshell = New-Object -ComObject WScript.Shell
        $wshell.Popup("Chayuan 启动失败：`n`n$msg`n`n日志：$LaunchLog", 0, 'Chayuan', 16) | Out-Null
    }
    exit 1
}

Write-Log "===== launcher start ====="
Write-Log "InstallDir=$InstallDir"
Write-Log "SrcRoot=$SrcRoot"
Write-Log "AppSupport=$AppSupport"

# ---- 首次安装触发 ----------------------------------------------------------
$UserPythonW   = Join-Path $AppSupport 'python\pythonw.exe'
$UserPythonExe = Join-Path $AppSupport 'python\python.exe'
$PyTarball     = Join-Path $DistDir 'python-runtime.tar.gz'
$FirstRunPs1   = Join-Path $DistDir 'first_run.ps1'

if (-not (Test-Path $UserPythonW) -and (Test-Path $PyTarball) -and (Test-Path $FirstRunPs1)) {
    Write-Log '触发首次安装流程...'
    $env:RESOURCES_DIR = $ResourcesDir
    $env:APP_SUPPORT   = $AppSupport
    $env:INSTALL_LOG   = (Join-Path $LogDir 'first_run.log')
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $FirstRunPs1
        if ($LASTEXITCODE -ne 0) {
            Die-Gui "首次安装失败，日志：$($env:INSTALL_LOG)"
        }
    } catch {
        Die-Gui "首次安装异常：$($_.Exception.Message)"
    }
}

# ---- 选择 Python -----------------------------------------------------------
$PythonBin = $null

# A. 显式指定
if ($env:CHAYUAN_PYTHON -and (Test-Path $env:CHAYUAN_PYTHON)) {
    $PythonBin = $env:CHAYUAN_PYTHON
    Write-Log "using CHAYUAN_PYTHON: $PythonBin"
}

# B. 用户级 pbs（dist 主路径，用 pythonw 避免控制台窗口）
if (-not $PythonBin -and (Test-Path $UserPythonW)) {
    $PythonBin = $UserPythonW
    Write-Log "using user pythonw: $PythonBin"
} elseif (-not $PythonBin -and (Test-Path $UserPythonExe)) {
    $PythonBin = $UserPythonExe
    Write-Log "using user python.exe: $PythonBin"
}

# C. bundle 内 portable python（预留）
$BundledPyW = Join-Path $ResourcesDir 'python\pythonw.exe'
if (-not $PythonBin -and (Test-Path $BundledPyW)) {
    $PythonBin = $BundledPyW
    Write-Log "using bundled pythonw: $PythonBin"
}

# D. PATH 兜底（最后手段：一般不建议，会拉到 PATH 里乱七八糟的 Python）
if (-not $PythonBin) {
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $found) { $found = Get-Command python.exe -ErrorAction SilentlyContinue }
    if ($found) {
        $PythonBin = $found.Source
        Write-Log "using PATH python: $PythonBin"
    }
}

if (-not $PythonBin) {
    Die-Gui '找不到可用的 Python。若这是 dist 首次运行，请检查 dist\ 目录完整性。'
}

# ---- 运行 tray ------------------------------------------------------------
$env:PYTHONPATH        = "$SrcRoot;$($env:PYTHONPATH)"
$env:PYTHONUNBUFFERED  = '1'
$env:PYTHONIOENCODING  = 'utf-8'
# CHAYUAN_ROOT：业务数据 / 知识库 / SQLite 都放这里
if (-not $env:CHAYUAN_ROOT) {
    $env:CHAYUAN_ROOT = (Join-Path $AppSupport 'data')
}

Write-Log "PYTHONPATH=$($env:PYTHONPATH)"
Write-Log "CHAYUAN_ROOT=$($env:CHAYUAN_ROOT)"
Write-Log "exec: $PythonBin -m chayuan.tray.entry"

# -m chayuan.tray.entry 进托盘主循环；失败时把异常追加到 launcher.log
try {
    $proc = Start-Process -FilePath $PythonBin `
        -ArgumentList '-m', 'chayuan.tray.entry' `
        -NoNewWindow -PassThru -RedirectStandardOutput $LaunchLog -RedirectStandardError $LaunchLog
    Write-Log "tray pid=$($proc.Id)"
    # 不 Wait-Process：launcher 启动后立即退出，让 tray 自主持久
} catch {
    Die-Gui "启动 tray 失败：$($_.Exception.Message)"
}
