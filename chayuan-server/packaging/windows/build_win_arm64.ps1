# ============================================================================
# Chayuan Windows arm64 打包脚本(MSIX 优先, 失败回退 ZIP)
#
# 历史:
#   v6 之前 windows arm64 没有原生安装包,只产 .zip 让用户自己解压。
#   v6.2 开始改用 MSIX (Windows 10 1809+),由 makeappx.exe + signtool 制作。
#   生效条件:
#     - 构建机有 Windows SDK 10.0.19041+ (含 makeappx.exe / signtool.exe)
#     - 有有效的代码签名证书 (.pfx)。无证书时回退 .zip
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build_win_arm64.ps1 \
#     -Version 6.2.0 \
#     -CertificatePath ./signing.pfx -CertificatePassword '...'
#
# 前置:
#   * vendor\cpython-3.12-aarch64-pc-windows-msvc.tar.gz (可从 astral-sh 拉)
#   * vendor\wheels-arm64\  (用 pip download --platform win_arm64 预取的 wheel)
#
# 产物:
#   packaging\build\Chayuan-{Version}-windows-arm64-dist.msix
#   或 (无证书时) packaging\build\Chayuan-{Version}-windows-arm64-dist.zip
# ============================================================================

[CmdletBinding()]
param(
    [string]$Version = '',
    [string]$CertificatePath = '',
    [string]$CertificatePassword = '',
    [switch]$ZipOnly  # 强制走 zip 兜底路径
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PkgDir    = Split-Path -Parent $ScriptDir
$RepoRoot  = Split-Path -Parent $PkgDir

$VendorDir = Join-Path $PkgDir 'vendor'
$BuildRoot = Join-Path $PkgDir 'build\dist-win-arm64'
$OutputDir = Join-Path $PkgDir 'build'
$ManifestSrc = Join-Path $ScriptDir 'AppxManifest.xml'

# ---- 版本号 ----------------------------------------------------------------
if (-not $Version) {
    $pyproj = Get-Content (Join-Path $RepoRoot 'libs\chayuan-server\pyproject.toml')
    $m = ($pyproj | Select-String -Pattern '^version\s*=\s*"([^"]+)"')
    if ($m) { $Version = $m.Matches[0].Groups[1].Value }
    if (-not $Version) { $Version = '0.0.0.0' }
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Chayuan Windows arm64 打包" -ForegroundColor Cyan
Write-Host "  version : $Version"
Write-Host "  staging : $BuildRoot"
Write-Host "==========================================" -ForegroundColor Cyan

# ---- staging: 复用 build_win.ps1 主体逻辑 ----------------------------------
# 注: 这里假设主脚本已经支持 -Arch arm64 形参; 若没有, 调用方需先扩展它
& "$ScriptDir\build_win.ps1" -Version $Version -SkipNsis -Arch arm64
if ($LASTEXITCODE -ne 0) { throw "Windows arm64 staging failed" }

# ---- 决定走 MSIX 还是 ZIP --------------------------------------------------
$MakeAppx = (Get-Command makeappx.exe -ErrorAction SilentlyContinue).Source
$SignTool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source

$canMsix = (-not $ZipOnly) -and $MakeAppx -and $SignTool -and (Test-Path $CertificatePath) -and $CertificatePassword
if (-not $canMsix) {
    Write-Host "MSIX 工具链或证书缺失, 回退 ZIP 打包" -ForegroundColor Yellow
    $zipPath = Join-Path $OutputDir "Chayuan-$Version-windows-arm64-dist.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path "$BuildRoot\*" -DestinationPath $zipPath
    Write-Host "OK -> $zipPath" -ForegroundColor Green
    exit 0
}

# ---- 准备 AppxManifest.xml --------------------------------------------------
$appxOutDir = Join-Path $OutputDir 'appx-arm64'
if (Test-Path $appxOutDir) { Remove-Item $appxOutDir -Recurse -Force }
Copy-Item $BuildRoot $appxOutDir -Recurse

$manifestDest = Join-Path $appxOutDir 'AppxManifest.xml'
if (Test-Path $ManifestSrc) {
    (Get-Content $ManifestSrc -Raw) `
        -replace '\$VERSION\$', $Version `
        | Set-Content -Encoding UTF8 $manifestDest
} else {
    # 兜底:本目录无 AppxManifest 模板时,内联一份最小可用的
    @"
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities">
  <Identity Name="dev.chayuan.Chayuan"
            Publisher="CN=Chayuan, O=Chayuan, C=CN"
            Version="$Version"
            ProcessorArchitecture="arm64"/>
  <Properties>
    <DisplayName>Chayuan</DisplayName>
    <PublisherDisplayName>Chayuan</PublisherDisplayName>
    <Logo>Assets\Square150x150Logo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.22621.0"/>
  </Dependencies>
  <Resources>
    <Resource Language="zh-cn"/>
    <Resource Language="en-us"/>
  </Resources>
  <Applications>
    <Application Id="App" Executable="chayuan.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="Chayuan"
                          Description="察元一体化多模态 AI 平台"
                          BackgroundColor="transparent"
                          Square150x150Logo="Assets\Square150x150Logo.png"
                          Square44x44Logo="Assets\Square44x44Logo.png"/>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust"/>
  </Capabilities>
</Package>
"@ | Set-Content -Encoding UTF8 $manifestDest
}

# ---- 调 makeappx.exe -------------------------------------------------------
$msixPath = Join-Path $OutputDir "Chayuan-$Version-windows-arm64-dist.msix"
if (Test-Path $msixPath) { Remove-Item $msixPath -Force }

& $MakeAppx pack /h SHA256 /d $appxOutDir /p $msixPath /o
if ($LASTEXITCODE -ne 0) { throw "makeappx pack failed" }

# ---- signtool 签名 ---------------------------------------------------------
& $SignTool sign /fd SHA256 /a /f $CertificatePath /p $CertificatePassword $msixPath
if ($LASTEXITCODE -ne 0) { throw "signtool sign failed" }

Write-Host "OK -> $msixPath" -ForegroundColor Green
