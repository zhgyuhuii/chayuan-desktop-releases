<#
.SYNOPSIS
  下载 whisper-server.exe (Win x64 pre-built) 到 vendor/services/whisper-server/。

.PARAMETER Version
  whisper.cpp release tag，优先尝试此版本，失败时自动 fallback 到旧版。
  默认 'v1.7.6'。
  注意:v1.7.4/v1.7.3/v1.7.2 upstream **没发** binary asset(只发 source tarball),
       Win pre-built `whisper-bin-x64.zip` 从 v1.7.6 开始才有,所以默认提到 v1.7.6。

.EXAMPLE
  .\scripts\install-whisper-server.ps1
  .\scripts\install-whisper-server.ps1 -Version v1.8.0
#>
[CmdletBinding()]
param(
    [string]$Version = 'v1.7.6'
)

try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    chcp 65001 > $null 2>&1
} catch {}

$ErrorActionPreference = 'Stop'
# PS 7+ 默认会把 native exe 写 stderr 视作 PS 错误,与 ErrorActionPreference=Stop
# 叠加后会把"whisper-server.exe --help 顺手往 stderr 写说明"误判成失败而中断整个脚本。
# 与 dev-start.ps1 的 Get-PyVersion 同源问题,这里统一关闭。
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspaceRoot = Split-Path -Parent $here
# 注意:必须装到 chayuan-server\vendor\services\ 才能被
# chayuan/server/model_registry/local_runtime.py:_default_install_services_dirs()
# 的 parents[5] = chayuan-server 搜到(跟 install-llama-server 一致)。
$destDir = Join-Path $workspaceRoot 'chayuan-server\vendor\services\whisper-server'

if (-not (Test-Path $destDir)) {
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
}

# whisper.cpp Win release tag 优先级 (新→老 fallback)
# 注意:v1.7.5 / v1.7.4 / v1.7.3 / v1.7.2 upstream 没发 binary asset,跳过
$Tags = @($Version, 'v1.8.0', 'v1.7.6') | Select-Object -Unique
$Asset = 'whisper-bin-x64.zip'  # Win pre-built 二进制 zip(v1.7.6+ 才有)

$ok = $false
foreach ($tag in $Tags) {
    $Url = "https://github.com/ggerganov/whisper.cpp/releases/download/$tag/$Asset"
    Write-Host "[install-whisper-server] 尝试拉 $Url"
    $tmpZip = Join-Path $env:TEMP "whisper-server-$tag.zip"
    try {
        Invoke-WebRequest -Uri $Url -OutFile $tmpZip -UseBasicParsing -ErrorAction Stop
        Write-Host "[install-whisper-server] 下完,$([math]::Round((Get-Item $tmpZip).Length/1MB,1)) MB"

        $tmpDir = Join-Path $env:TEMP "whisper-server-$tag-extract"
        if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
        Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force

        $exe = Get-ChildItem -Recurse -Path $tmpDir -Filter 'whisper-server.exe' | Select-Object -First 1
        if (-not $exe) {
            Write-Warning "[install-whisper-server] $tag zip 里没找到 whisper-server.exe，试下一个 tag"
            Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
            Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
            continue
        }

        # 清旧 exe / dll
        Get-ChildItem $destDir -File -ErrorAction SilentlyContinue | Where-Object {
            $_.Extension -in '.exe', '.dll', '.so', '.dylib'
        } | Remove-Item -Force

        # 拷 whisper-server.exe
        Copy-Item -Path $exe.FullName -Destination (Join-Path $destDir 'whisper-server.exe') -Force
        Write-Host "  whisper-server.exe  ($([math]::Round($exe.Length/1MB,2)) MB)"

        # 同时复制依赖 DLL (whisper.cpp Win build 通常带几个)
        Get-ChildItem -Recurse -Path $tmpDir -Filter '*.dll' | ForEach-Object {
            Copy-Item -Path $_.FullName -Destination (Join-Path $destDir $_.Name) -Force
            Write-Host "  $($_.Name)  ($([math]::Round($_.Length/1MB,2)) MB)"
        }

        # 写 VERSION 文件
        "$tag`n$(Get-Date -Format 'yyyy-MM-dd')`n" | Set-Content -Path (Join-Path $destDir 'VERSION') -NoNewline

        Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
        Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

        $ok = $true
        Write-Host "[install-whisper-server] 装好 $tag → $destDir"
        break
    } catch {
        Write-Warning "[install-whisper-server] $tag 失败: $_"
        Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
    }
}

if (-not $ok) {
    throw "[install-whisper-server] 所有 release tag 都失败，检查网络 / GitHub release 可达性"
}

# 验证 binary 可执行
$bin = Join-Path $destDir 'whisper-server.exe'
Write-Host ""
Write-Host "[install-whisper-server] 完成。$destDir 内容:"
Get-ChildItem $destDir -File | Format-Table Name, @{N='MB';E={$([math]::Round($_.Length/1MB,2))}}

Write-Host "[install-whisper-server] 验证 binary..."
try {
    # 用 cmd.exe 包一层规避 PS native stderr 误判;Select-Object 截前 3 行避免刷屏。
    $testOut = & cmd.exe /c "`"$bin`" --help 2>&1" | Select-Object -First 3
    $exit = $LASTEXITCODE
    $testOut | ForEach-Object { Write-Host "  $_" }
    if ($exit -ne 0 -and $null -ne $exit) {
        Write-Warning "[install-whisper-server] whisper-server.exe --help 退出码 $exit，可能 AVX2 / Visual C++ Runtime 缺失"
    } else {
        Write-Host "[install-whisper-server] OK"
    }
} catch {
    Write-Warning "[install-whisper-server] verify 步骤异常(不影响 binary 已安装): $($_.Exception.Message)"
}
