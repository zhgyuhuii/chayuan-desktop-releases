# =============================================================================
# 修复 Windows 上半装的 PyTorch — 应对 [WinError 5] _C.pyd 中断后的残骸。
#
# 用法(PowerShell):
#   .\scripts\repair_torch.ps1
#   .\scripts\repair_torch.ps1 -TorchVersion 2.4.1
#
# 它做四件事:
#   1) 关掉占用 torch 的 Python 进程(避免 DLL 锁)
#   2) pip uninstall + 手工删 site-packages\torch* 残留
#   3) 重装 torch / torchvision / torchaudio CPU 稳定组合
#   4) 验证 torch.SymInt 可用
# =============================================================================
param(
    [string]$Pip = "pip",
    [string]$TorchVersion = "2.4.1",
    [string]$VisionVersion = "0.19.1",
    [string]$AudioVersion = "2.4.1",
    [string]$IndexUrl = "https://download.pytorch.org/whl/cpu",
    [switch]$NoKill = $false
)

$ErrorActionPreference = "Stop"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " chayuan · 修复 Windows PyTorch 半装残骸" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host ""

# ---- 1. 关闭占用 Python 的进程 ----
if (-not $NoKill) {
    $procs = @(Get-Process python,pythonw,jupyter*,chayuan* -ErrorAction SilentlyContinue)
    if ($procs.Count -gt 0) {
        Write-Host "[1/4] 关闭 $($procs.Count) 个 Python 进程..." -ForegroundColor Yellow
        $procs | ForEach-Object {
            Write-Host "      PID $($_.Id) · $($_.ProcessName)" -ForegroundColor DarkYellow
        }
        $procs | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    } else {
        Write-Host "[1/4] 无需关闭 Python 进程" -ForegroundColor Green
    }
}

# ---- 2. 诊断当前状态 ----
Write-Host ""
Write-Host "[2/4] 诊断当前 torch 状态..." -ForegroundColor Yellow
$diagOut = & python -c @"
try:
    import torch
    print('version:', torch.__version__)
    print('file:', torch.__file__)
    print('SymInt:', 'SymInt' in dir(torch))
except Exception as e:
    print('IMPORT_FAILED:', type(e).__name__, e)
"@ 2>&1
$diagOut | ForEach-Object { Write-Host "      $_" }

# ---- 3. 卸载 + 删残留 ----
Write-Host ""
Write-Host "[3/4] 卸载 torch 系列..." -ForegroundColor Yellow
& $Pip uninstall -y torch torchvision torchaudio 2>&1 | ForEach-Object {
    if ($_ -match "WinError 5|拒绝访问") {
        Write-Host "      ! $_" -ForegroundColor Red
        Write-Host ""
        Write-Host "卸载又被锁:" -ForegroundColor Red
        Write-Host "  - 用 *管理员* 模式重开 PowerShell" -ForegroundColor Red
        Write-Host "  - 临时关闭 Windows Defender 实时保护" -ForegroundColor Red
        Write-Host "  - 重新运行本脚本" -ForegroundColor Red
        exit 3
    } else {
        Write-Host "      $_"
    }
}

# 找 site-packages 路径(从 python 自己问比解析路径稳)
$sitePackages = & python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])" 2>$null
if ($sitePackages -and (Test-Path $sitePackages)) {
    Write-Host "      → 清理残留目录在 $sitePackages"
    Get-ChildItem -Path $sitePackages -Filter "torch*" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "        删除 $($_.Name)" -ForegroundColor DarkYellow
        Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue
    }
}

# ---- 4. 重装 + 验证 ----
Write-Host ""
Write-Host "[4/4] 重装 torch==$TorchVersion (CPU 版)..." -ForegroundColor Yellow
& $Pip install --no-cache-dir `
    "torch==$TorchVersion" "torchvision==$VisionVersion" "torchaudio==$AudioVersion" `
    --index-url $IndexUrl
if ($LASTEXITCODE -ne 0) {
    Write-Host "重装失败,见上方错误" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "验证 torch:" -ForegroundColor Cyan
& python -c @"
import torch
assert hasattr(torch, 'SymInt'), 'SymInt 仍缺失,装坏了'
_ = torch.tensor([1.0])
print('  version: ', torch.__version__)
print('  SymInt:  ', 'SymInt' in dir(torch))
print('  tensor:  OK')
from torch.distributed import RankType
print('  distributed: OK')
"@
if ($LASTEXITCODE -ne 0) {
    Write-Host "验证失败 — torch 仍异常,请检查 Windows 事件查看器是否有 DLL 加载错误" -ForegroundColor Red
    exit 4
}

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host " ✓ torch 修复完成,可以重新启动 chayuan:" -ForegroundColor Green
Write-Host "   python cli.py start -a" -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Green
