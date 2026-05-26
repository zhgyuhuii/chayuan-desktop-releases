<#
.SYNOPSIS
  下载 llama-server.exe (Win64) 到 vendor/services/llama-server/<target>/。

.PARAMETER Version
  llama.cpp release tag,默认 'b9174' (latest 2026-05-16)。
  qwen3 / qwen3-moe / phi-4 / 2025 后的架构需要 b5092+ 才能加载。
  老 b4404 默认不支持 qwen3,撞 'unknown model architecture' 直接 fail。

  注:版本号 / 资产名 schema / GitHub 镜像清单同时被「应用内运行时服务
  下载」复用 —— 真源是
    chayuan-server/libs/.../chayuan/server/runtime/install_service.py
  (_LLAMA_VERSION / _llama_asset_for / _GH_MIRRORS)。改这里记得同步那边。

.PARAMETER Target
  vendor 平台子目录。不传 = 按 PROCESSOR_ARCHITECTURE 选默认 win-x64 / win-arm64。
  支持:
    win-x64         x86_64,默认。运行时 SIMD 探测(AVX2/AVX/SSE)
    win-x64-avx     兼容别名 → 同 win-x64(新版只出一种 zip,自动 SIMD detect)
    win-x64-avx512  兼容别名 → 同 win-x64
    win-x64-noavx   兼容别名 → 同 win-x64
    win-arm64       Surface Pro X / Copilot+ PC
    linux-x64       Ubuntu x64 —— GitHub release zip,可从 Windows 跨平台下
    macos-x64       Intel Mac —— 同上
    macos-arm64     Apple Silicon —— 同上
    linux-arm64     upstream 没发 zip,本脚本拒绝;需 ARM Linux 上 docker/源码
  注:跨平台下的 linux/macos 二进制在 Windows 上不带执行位,打对应平台
  安装包时由 build 脚本 / 运行时补 chmod +x。

  注意:b5400 起 llama.cpp 不再分 avx2/avx/avx512/noavx 多个 zip,改单一 -cpu-x64.zip
  内置运行时 CPU dispatch。旧别名只为脚本调用向后兼容,实际拉的是同一个 asset。

.EXAMPLE
  .\scripts\install-llama-server.ps1                              # 自动 host,最新版
  .\scripts\install-llama-server.ps1 -Target win-arm64            # ARM64 PC
  .\scripts\install-llama-server.ps1 -Version b9000               # 指定 build
#>
[CmdletBinding()]
param(
    [string]$Version = 'b9174',
    [string]$Target = ''
)

try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    chcp 65001 > $null 2>&1
} catch {}

$ErrorActionPreference = 'Stop'
# PS 7+ 默认会把 native exe 写 stderr 当成 PS 错误,叠加 ErrorActionPreference=Stop
# 会把无害的 --help 输出误判为失败而中断,跟 install-whisper-server.ps1 同源。
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

# Auto host detection
if (-not $Target) {
    $cpuArch = ($env:PROCESSOR_ARCHITECTURE + '').ToUpperInvariant()
    if ($cpuArch -eq 'ARM64') {
        $Target = 'win-arm64'
    } else {
        $Target = 'win-x64'  # AMD64 / EM64T → AVX2 默认
    }
}

# Target → upstream asset
# 2026-05-16:b5400 起 llama.cpp 把 win avx/avx2/avx512/noavx 四个 zip 合并成单一
# `-cpu-x64.zip`(运行时 SIMD dispatch)。低于 b5400 的旧 build 是分版本 zip;两套
# schema 都支持。版本号简单 numeric 比较即可(b 前缀剥掉,看后面整数)。
$verNum = 0
if ($Version -match '^b(\d+)$') { $verNum = [int]$Matches[1] }
$useNewSchema = ($verNum -ge 5400)

if ($useNewSchema) {
    $assetMap = @{
        # 新 schema:所有 x64 别名都映到同一个 -cpu-x64.zip(内置 SIMD dispatch)
        'win-x64'        = "llama-$Version-bin-win-cpu-x64.zip"
        'win-x64-avx'    = "llama-$Version-bin-win-cpu-x64.zip"
        'win-x64-avx512' = "llama-$Version-bin-win-cpu-x64.zip"
        'win-x64-noavx'  = "llama-$Version-bin-win-cpu-x64.zip"
        'win-arm64'      = "llama-$Version-bin-win-cpu-arm64.zip"
    }
} else {
    # 旧 schema:四个 SIMD 变体各自 zip + arm64 用 llvm 命名
    $assetMap = @{
        'win-x64'        = "llama-$Version-bin-win-avx2-x64.zip"
        'win-x64-avx'    = "llama-$Version-bin-win-avx-x64.zip"
        'win-x64-avx512' = "llama-$Version-bin-win-avx512-x64.zip"
        'win-x64-noavx'  = "llama-$Version-bin-win-noavx-x64.zip"
        'win-arm64'      = "llama-$Version-bin-win-llvm-arm64.zip"
    }
}
# linux / macos:命名 schema 跟版本无关(只有 win 在 b5400 合并过 avx 变体),
# 三个都是单一 zip。加进 map 让 install-vendor.ps1 能从 Windows 跨平台下全。
# .zip 用 Expand-Archive 在 Windows 上照样解,装到 vendor 给后续打包用。
$assetMap['linux-x64']   = "llama-$Version-bin-ubuntu-x64.zip"
$assetMap['macos-x64']   = "llama-$Version-bin-macos-x64.zip"
$assetMap['macos-arm64'] = "llama-$Version-bin-macos-arm64.zip"

if ($Target -eq 'linux-arm64') {
    Write-Error ("linux-arm64 upstream 没发 zip。解法:在 ARM Linux 上 " +
        "docker 提取 ghcr.io/ggml-org/llama.cpp:server,或源码 cmake build。")
    exit 2
}
if (-not $assetMap.ContainsKey($Target)) {
    Write-Error "未识别的 target: $Target  (支持: $($assetMap.Keys -join ', '))"
    exit 1
}
$zipName = $assetMap[$Target]

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspaceRoot = Split-Path -Parent $here
$destDir = Join-Path $workspaceRoot ('chayuan-server\vendor\services\llama-server\' + $Target)

if (-not (Test-Path $destDir)) {
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
}

# GitHub 镜像支持:墙内 github.com 不通时,设环境变量
#   $env:GITHUB_MIRROR = "https://gh-proxy.com/"
# 下载 URL 会被拼成 https://gh-proxy.com/https://github.com/...。不设则直连。
# 注:老的 ghproxy.com 已废弃,用 gh-proxy.com 或 ghfast.top。
$rawUrl = "https://github.com/ggerganov/llama.cpp/releases/download/$Version/$zipName"
if ($env:GITHUB_MIRROR) {
    $mirror = $env:GITHUB_MIRROR.TrimEnd('/')
    $url = $mirror + '/' + $rawUrl
    Write-Host "[install-llama-server] 经 GitHub 镜像: $mirror/"
} else {
    $url = $rawUrl
}
$tmpZip = Join-Path $env:TEMP $zipName

Write-Host "[install-llama-server] target=$Target asset=$zipName dest=$destDir"
Write-Host "[install-llama-server] 下载 $url"
Invoke-WebRequest -Uri $url -OutFile $tmpZip -UseBasicParsing
Write-Host "[install-llama-server] 下完,$([math]::Round((Get-Item $tmpZip).Length/1MB,1)) MB"

# 解压
$tmpExtract = Join-Path $env:TEMP ('llama-' + $Version + '-' + $Target + '-extract')
if (Test-Path $tmpExtract) { Remove-Item $tmpExtract -Recurse -Force }
Expand-Archive -Path $tmpZip -DestinationPath $tmpExtract -Force

# 清旧 binary / 共享库。用 -like 匹配 Name(不用 .Extension)—— Linux 的
# 版本化 .so(libggml.so.0.11.1).Extension 会被解析成 ".1",漏删。
Get-ChildItem $destDir -File | Where-Object {
    $_.Name -like '*.exe' -or $_.Name -like '*.dll' `
    -or $_.Name -like '*.so' -or $_.Name -like '*.so.*' `
    -or $_.Name -like '*.dylib' -or $_.Name -eq 'llama-server' `
    -or $_.Name -eq 'ggml-metal.metal'
} | Remove-Item -Force

# 拷主 binary + 共享库:win 是 llama-server.exe + *.dll;linux 是
# llama-server + *.so(可能版本化);macos 是 llama-server + *.dylib +
# ggml-metal.metal。Get-ChildItem -Recurse 兼容 win 顶层 / mac-linux build/bin。
$needed = Get-ChildItem $tmpExtract -Recurse -File | Where-Object {
    $_.Name -eq 'llama-server.exe' -or $_.Name -eq 'llama-server' `
    -or $_.Name -like '*.dll' -or $_.Name -like '*.so' -or $_.Name -like '*.so.*' `
    -or $_.Name -like '*.dylib' -or $_.Name -eq 'ggml-metal.metal'
}
foreach ($f in $needed) {
    Copy-Item $f.FullName (Join-Path $destDir $f.Name) -Force
    Write-Host "  $($f.Name)  ($([math]::Round($f.Length/1MB,2)) MB)"
}

# 元数据
"$Version`n$(Get-Date -Format 'yyyy-MM-dd')`n" | Set-Content -Path (Join-Path $destDir 'VERSION') -NoNewline
$licInZip = Get-ChildItem $tmpExtract -Recurse -File -Filter 'LICENSE' | Select-Object -First 1
if ($licInZip -and -not (Test-Path (Join-Path $destDir 'LICENSE'))) {
    Copy-Item $licInZip.FullName (Join-Path $destDir 'LICENSE') -Force
}

Remove-Item $tmpZip -Force
Remove-Item $tmpExtract -Recurse -Force

Write-Host ""
Write-Host "[install-llama-server] 完成 → $destDir"
Get-ChildItem $destDir -File | Format-Table Name, @{N='MB';E={$([math]::Round($_.Length/1MB,2))}}
