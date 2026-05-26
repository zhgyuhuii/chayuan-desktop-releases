# ============================================================================
# Chayuan Windows 打包脚本（PowerShell）
#
# 用法（在 Windows 构建机上，推荐 Windows 10/11 x64）：
#     cd <repo_root>
#     powershell -ExecutionPolicy Bypass -File packaging\windows\build_win.ps1
#
# 前置条件：
#   1. 同目录下有 vendor\cpython-3.11-x86_64-pc-windows-msvc.tar.gz
#      （packaging\vendor\ 下；可在任意平台用 curl 从 astral-sh 下载）
#   2. 同目录下有 vendor\wheels\ 目录，至少要有 requirements-runtime.txt
#      里面列出的所有包的 Windows amd64 wheel
#      （可在 Windows 上 pip download，或跨平台用 --platform win_amd64 预取）
#   3. 已安装 NSIS（https://nsis.sourceforge.io/），makensis.exe 在 PATH 上
#
# 产物：
#   packaging\build\Chayuan-1.0.0.0-windows-amd64-personal-dist.exe
# ============================================================================

[CmdletBinding()]
param(
    [string]$Version = '',      # 留空则从 pyproject.toml 读
    [switch]$SkipNsis           # 只准备 staging 不调 makensis
)

$ErrorActionPreference = 'Stop'

# ---- 路径 ------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PkgDir    = Split-Path -Parent $ScriptDir
$RepoRoot  = Split-Path -Parent $PkgDir

$VendorDir   = Join-Path $PkgDir 'vendor'
$BuildRoot   = Join-Path $PkgDir 'build\dist-win'
$OutputDir   = Join-Path $PkgDir 'build'

# ---- 版本号 ----------------------------------------------------------------
if (-not $Version) {
    $pyproj = Get-Content (Join-Path $RepoRoot 'libs\chayuan-server\pyproject.toml')
    $Version = ($pyproj | Select-String -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
    if (-not $Version) { $Version = '0.0.0.0' }
}

Write-Host "=========================================="
Write-Host "  Chayuan Windows 打包"
Write-Host "  version : $Version"
Write-Host "  staging : $BuildRoot"
Write-Host "=========================================="

# ---- 前置资源检查 ----------------------------------------------------------
$PyTarball = Join-Path $VendorDir 'cpython-3.11-x86_64-pc-windows-msvc.tar.gz'
$WheelsDir = Join-Path $VendorDir 'wheels'
$ReqFile   = Join-Path $ScriptDir 'dist\requirements-runtime.txt'

$missing = @()
if (-not (Test-Path $PyTarball))    { $missing += "Python runtime tarball: $PyTarball" }
if (-not (Test-Path $WheelsDir))    { $missing += "wheels dir: $WheelsDir" }
if (-not (Test-Path $ReqFile))      { $missing += "requirements-runtime.txt: $ReqFile" }

if ($missing.Count -gt 0) {
    Write-Host '[build_win] 缺少以下资源：' -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host ''
    Write-Host '补齐方式示例：' -ForegroundColor Yellow
    Write-Host '    # 1) 下载 Windows amd64 pbs tarball（~20 MB）'
    Write-Host '    Invoke-WebRequest -Uri "https://github.com/astral-sh/python-build-standalone/releases/download/20260414/cpython-3.11.15+20260414-x86_64-pc-windows-msvc-install_only_stripped.tar.gz" -OutFile packaging\vendor\cpython-3.11-x86_64-pc-windows-msvc.tar.gz'
    Write-Host ''
    Write-Host '    # 2) 预下载 Windows amd64 wheels（建议直接在 Windows 上跑 pip download）'
    Write-Host '    python -m pip download --index-url https://pypi.tuna.tsinghua.edu.cn/simple `'
    Write-Host '           --dest packaging\vendor\wheels --prefer-binary `'
    Write-Host '           -r packaging\windows\dist\requirements-runtime.txt'
    exit 2
}

# ---- 清理并准备 staging ----------------------------------------------------
if (Test-Path $BuildRoot) { Remove-Item -Recurse -Force $BuildRoot }
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $BuildRoot 'bin')  | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $BuildRoot 'src\chayuan-server') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $BuildRoot 'dist\wheels') | Out-Null

# ---- 托盘图标 tray_icon.png 生成（若缺失）--------------------------------
# Windows 没有 sips，用 Python PIL 缩到 44×44。因为本机可能没 PIL，
# 保底直接拷贝大图；run-time 时 pystray 会自己拉伸到 16×16，效果可用。
$imgDir   = Join-Path $RepoRoot 'libs\chayuan-server\chayuan\img'
$trayIcon = Join-Path $imgDir 'tray_icon.png'
$logoPng  = Join-Path $imgDir 'logo.png'
if (-not (Test-Path $trayIcon) -and (Test-Path $logoPng)) {
    try {
        python -c "from PIL import Image; im = Image.open(r'$logoPng'); im.thumbnail((44,44)); im.save(r'$trayIcon')"
    } catch {
        Write-Host '[build_win] PIL 不可用，直接拷贝 logo.png 作为托盘图标' -ForegroundColor Yellow
        Copy-Item $logoPng $trayIcon
    }
}

# ---- 业务源码 --------------------------------------------------------------
Write-Host '[build_win] 拷贝业务源码...'
$src = Join-Path $RepoRoot 'libs\chayuan-server\chayuan'
$dst = Join-Path $BuildRoot 'src\chayuan-server\chayuan'
robocopy $src $dst /E /XD __pycache__ tests data /XF *.pyc *.pyo .DS_Store | Out-Null

$lc_src = Join-Path $RepoRoot 'libs\chayuan-server\langchain_chayuan'
if (Test-Path $lc_src) {
    $lc_dst = Join-Path $BuildRoot 'src\chayuan-server\langchain_chayuan'
    robocopy $lc_src $lc_dst /E /XD __pycache__ /XF *.pyc | Out-Null
}

# ---- 启动脚本 / first_run --------------------------------------------------
Copy-Item (Join-Path $ScriptDir 'launcher.vbs') (Join-Path $BuildRoot 'bin\launcher.vbs')
Copy-Item (Join-Path $ScriptDir 'launcher.ps1') (Join-Path $BuildRoot 'bin\launcher.ps1')

Copy-Item $PyTarball (Join-Path $BuildRoot 'dist\python-runtime.tar.gz')
Copy-Item $ReqFile   (Join-Path $BuildRoot 'dist\requirements-runtime.txt')
Copy-Item (Join-Path $ScriptDir 'dist\first_run.ps1') (Join-Path $BuildRoot 'dist\first_run.ps1')

Write-Host '[build_win] 拷贝 wheels...'
robocopy $WheelsDir (Join-Path $BuildRoot 'dist\wheels') /E | Out-Null

# ---- 图标 .ico ------------------------------------------------------------
# NSIS 需要 .ico 格式（多尺寸 icon）；PIL 可以直接写 .ico。
$icoPath = Join-Path $BuildRoot 'AppIcon.ico'
if (-not (Test-Path $icoPath)) {
    if (Test-Path $logoPng) {
        try {
            python -c "from PIL import Image; im = Image.open(r'$logoPng'); im.save(r'$icoPath', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
        } catch {
            Write-Host '[build_win] 无法用 PIL 生成 .ico，安装器将使用 NSIS 默认图标' -ForegroundColor Yellow
        }
    }
}

# ---- 调 NSIS ---------------------------------------------------------------
if ($SkipNsis) {
    Write-Host "[build_win] 已准备 staging：$BuildRoot（/SkipNsis 已指定，跳过 makensis 打包）"
    exit 0
}

$makensis = Get-Command makensis.exe -ErrorAction SilentlyContinue
if (-not $makensis) {
    Write-Host '[build_win] 找不到 makensis.exe，请先安装 NSIS：https://nsis.sourceforge.io/' -ForegroundColor Red
    Write-Host '(可用 -SkipNsis 参数跳过打包，只准备 staging)' -ForegroundColor Yellow
    exit 3
}

$nsi = Join-Path $ScriptDir 'Chayuan.nsi'
& $makensis.Source `
    "/DCHAYUAN_VERSION=$Version" `
    "/DBUILD_ROOT=$BuildRoot" `
    $nsi

if ($LASTEXITCODE -ne 0) {
    Write-Host "[build_win] makensis 失败（退出码 $LASTEXITCODE）" -ForegroundColor Red
    exit $LASTEXITCODE
}

$setupExe = Join-Path $OutputDir "Chayuan-$Version-windows-amd64-personal-dist.exe"
Write-Host ''
Write-Host '[build_win] 构建完成 OK' -ForegroundColor Green
Write-Host "  installer: $setupExe"
Write-Host "  size     : $((Get-Item $setupExe).Length / 1MB) MB"
