# =============================================================================
# 智能安装(包装 install_ai_platform.ps1)— 失败时自动诊断 + 一键修复。
#
# 用法:
#   .\scripts\install_smart.ps1               # 失败后只打印诊断
#   .\scripts\install_smart.ps1 -AutoFix      # 失败后逐条交互式执行修复
#
# 工作流:
#   1) 调用 install_ai_platform.ps1,output 同时写到 stdout 和临时日志
#   2) 退出码 == 0 → 完事
#   3) 退出码 != 0 → 把日志喂给 install_diagnose.py 分析
#   4) 诊断器输出已知失败模式 + 修复命令(可选 -AutoFix 自动跑)
# =============================================================================
param(
    [string]$Pip = "pip",
    [switch]$AutoFix = $false,
    [string]$LogFile = ""
)

$ErrorActionPreference = "Continue"  # 失败不立即抛,让诊断器看完整日志

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here

if (-not $LogFile) {
    $LogFile = Join-Path $env:TEMP "chayuan_install_$([System.IO.Path]::GetRandomFileName()).log"
}

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host " chayuan · 智能安装(install_smart.ps1)" -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "  日志文件: $LogFile"
Write-Host "  AutoFix:  $AutoFix"
Write-Host ""

# ---- 预清理:删 site-packages 里的 ~* 残骸目录(避免 pip "Ignoring invalid distribution") ----
# 这些目录是 pip 中断时留下的"准备替换但没成"的孤儿,删了无害,清完后 pip 会停止抱怨
try {
    $sitePackages = & python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])" 2>$null
    if ($sitePackages -and (Test-Path $sitePackages)) {
        $stale = @(Get-ChildItem -Path $sitePackages -Filter "~*" -Directory -Force -ErrorAction SilentlyContinue)
        if ($stale.Count -gt 0) {
            Write-Host "[install_smart] 发现 $($stale.Count) 个残骸目录(将清理):" -ForegroundColor DarkYellow
            $stale | ForEach-Object {
                Write-Host "    $($_.Name)" -ForegroundColor DarkYellow
                Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue
            }
            Write-Host ""
        }
    }
} catch {
    Write-Host "[install_smart] 残骸预清理跳过(非致命): $_" -ForegroundColor DarkGray
}

# ---- 跑底层安装脚本(在当前 PS 进程,不递归 child PS,避免 stderr 被包成 NativeCommandError) ----
$ErrorActionPreference = "Continue"
$WarningPreference = "Continue"
$installScript = Join-Path $Here "install_ai_platform.ps1"
& $installScript -Pip $Pip 2>&1 | Tee-Object -FilePath $LogFile
$rc = $LASTEXITCODE

if ($rc -eq 0) {
    Write-Host ""
    Write-Host "[install_smart] ✓ 安装成功" -ForegroundColor Green
    Remove-Item $LogFile -ErrorAction SilentlyContinue
    exit 0
}

Write-Host ""
Write-Host "[install_smart] ✗ 安装失败(退出码 $rc),自动诊断..." -ForegroundColor Yellow
Write-Host ""

# ---- 喂给诊断器 ----
$DiagPy = Join-Path $Here "install_diagnose.py"
if (-not (Test-Path $DiagPy)) {
    Write-Host "[install_smart] 诊断器不存在: $DiagPy" -ForegroundColor Red
    Write-Host "[install_smart] 完整日志保存在: $LogFile" -ForegroundColor Red
    exit $rc
}

$diagArgs = @($DiagPy, $LogFile)
if ($AutoFix) { $diagArgs += "--auto-fix" }

& python @diagArgs
$diagRc = $LASTEXITCODE

Write-Host ""
Write-Host "完整日志保留在: $LogFile" -ForegroundColor DarkGray

if ($AutoFix -and $diagRc -eq 0) {
    Write-Host "[install_smart] 修复完成,请重新运行: .\scripts\install_smart.ps1" -ForegroundColor Green
}

exit $rc
