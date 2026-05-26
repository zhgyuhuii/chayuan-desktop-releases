# =============================================================================
# 把 chayuan-server/libs/ 下的 11 个 ai-platform sibling 包以 editable 模式
# 装到当前 Python 环境。
#
# 用法（PowerShell）：
#   .\scripts\install_ai_platform.ps1
#   .\scripts\install_ai_platform.ps1 -Pip C:\envs\py312\Scripts\pip.exe
#
# Windows 注意:
#   * 运行前请关闭所有 Python 进程(VSCode / PyCharm / Jupyter / chayuan server),
#     避免 PyTorch _C.pyd 等已加载 DLL 被锁定导致 "[WinError 5] 拒绝访问"。
#   * 如遇 _C.pyd 报错,通常是 torch 被锁:
#       1) 关闭所有 Python 进程
#       2) 关闭 IDE 的"自动激活 venv" 功能
#       3) 关闭 Windows Defender 实时保护(临时,装完再开)
#       4) 用 *管理员* 模式重开 PowerShell / Anaconda Prompt 重试
# =============================================================================
param(
    [string]$Pip = "pip"
)

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
$Libs = Join-Path $Root "libs"

$Pkgs = @(
    "chayuan-core",
    "chayuan-identify",
    "chayuan-registry",
    "chayuan-discovery",
    "chayuan-modelmgr",
    "chayuan-runtime",
    "chayuan-supervisor",
    "chayuan-gateway",
    "chayuan-preflight",
    "chayuan-packager",
    "chayuan-cli"
)

Write-Host "[install_ai_platform] pip   : $(& $Pip --version)"
Write-Host ""

# Windows 友好提示: 检测到正在运行的 python.exe 时给警告
if ($IsWindows -or $env:OS -eq "Windows_NT") {
    $running = @(Get-Process -Name "python","pythonw","jupyter*","chayuan*" -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) {
        Write-Host "[install_ai_platform] ⚠ 检测到 $($running.Count) 个 Python 相关进程在运行:" -ForegroundColor Yellow
        $running | ForEach-Object { Write-Host "    PID $($_.Id) · $($_.ProcessName)" -ForegroundColor Yellow }
        Write-Host "[install_ai_platform] 这可能导致 torch _C.pyd 等 DLL 被锁住,pip 重装时报 [WinError 5]。" -ForegroundColor Yellow
        Write-Host "[install_ai_platform] 强烈建议先关闭这些进程再继续。" -ForegroundColor Yellow
        Write-Host ""
    }
}

foreach ($pkg in $Pkgs) {
    $src = Join-Path $Libs $pkg
    if (-not (Test-Path $src)) {
        Write-Host "  ! $pkg : 目录不存在 $src，跳过" -ForegroundColor Yellow
        continue
    }
    Write-Host "→ pip install -e $pkg"
    & $Pip install -e $src --no-deps
    if ($LASTEXITCODE -ne 0) { throw "pip install -e $src failed" }
}

Write-Host ""
Write-Host "→ 解析依赖(只装缺失的,不动已有 torch / numpy 等):"
# 关键修复: --upgrade-strategy only-if-needed 避免 pip 重装已经满足约束的 torch,
# 否则 Windows 上 _C.pyd 文件被锁会触发 [WinError 5] 拒绝访问。
& $Pip install -e (Join-Path $Libs "chayuan-cli") --upgrade-strategy only-if-needed
if ($LASTEXITCODE -ne 0) {
    Write-Host "[install_ai_platform] ✗ chayuan-cli 依赖解析失败" -ForegroundColor Red
    Write-Host "" -ForegroundColor Red
    Write-Host "如果错误是 [WinError 5] 拒绝访问 _C.pyd:" -ForegroundColor Red
    Write-Host "  1. 关闭所有 Python 进程(IDE/Jupyter/chayuan server)" -ForegroundColor Red
    Write-Host "  2. 临时关闭 Windows Defender 实时保护" -ForegroundColor Red
    Write-Host "  3. 用 *管理员* 模式重新运行此脚本" -ForegroundColor Red
    Write-Host "  4. 仍失败则: pip uninstall -y torch torchvision torchaudio 后再装" -ForegroundColor Red
    throw "chayuan-cli install failed"
}

Write-Host ""
Write-Host "[install_ai_platform] ✓ 11 个包已安装。验证：" -ForegroundColor Green
Write-Host "    chayuan ai-platform --help"
Write-Host "    chayuan ai-platform service info"
Write-Host "    chayuan ai-platform model ls"
