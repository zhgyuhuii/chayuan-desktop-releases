<#
.SYNOPSIS
  手动跑 WiX light.exe,看 Tauri 吞掉的真实 LGHT 错误码。

.DESCRIPTION
  Tauri 2 在 light.exe 失败时只打印 "failed to run light.exe",真实的
  LGHT0xxx error stderr 被吞了。这个脚本绕过 Tauri,直接调
  WixTools314\light.exe 跑同一份 main.wixobj,把完整 stdout/stderr
  写到日志文件 + 在控制台打印最后 80 行供复制粘贴。

  调用前提:已经跑过一次 .\build-desktop.cmd -IntegratedOnly(失败
  也无所谓,只要 Tauri 已经生成了 main.wixobj + .wxl 即可)。

.PARAMETER WorkspaceRoot
  仓库根。默认本脚本 ..

.PARAMETER LogPath
  日志输出路径。默认 %TEMP%\chayuan-light-debug.log

.EXAMPLE
  .\scripts\debug-wix-light.ps1
  # 跑诊断,日志落到默认路径,控制台显示后 80 行
#>
[CmdletBinding()]
param(
    [string]$WorkspaceRoot,
    [string]$LogPath = (Join-Path $env:TEMP 'chayuan-light-debug.log')
)

# UTF-8 控制台,避免中文乱码
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    chcp 65001 > $null 2>&1
} catch {}

$ErrorActionPreference = 'Stop'

if (-not $WorkspaceRoot) {
    $WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

$wixDir = Join-Path $WorkspaceRoot 'chayuan-client\apps\desktop\src-tauri\target\release\wix\x64'
$light  = Join-Path $env:LOCALAPPDATA 'tauri\WixTools314\light.exe'

Write-Host "[debug-light] WorkspaceRoot = $WorkspaceRoot"
Write-Host "[debug-light] WiX 目录       = $wixDir"
Write-Host "[debug-light] light.exe      = $light"
Write-Host "[debug-light] 日志           = $LogPath"
Write-Host ""

# 前置检查
if (-not (Test-Path $light)) {
    Write-Host "[FAIL] 找不到 light.exe: $light" -ForegroundColor Red
    Write-Host "       先跑一次 .\build-desktop.cmd -IntegratedOnly (可以失败),Tauri 会下载 WixTools314 到 %LOCALAPPDATA%\tauri\WixTools314"
    exit 1
}
if (-not (Test-Path $wixDir)) {
    Write-Host "[FAIL] 找不到 WiX 中间目录: $wixDir" -ForegroundColor Red
    Write-Host "       先跑一次 .\build-desktop.cmd -IntegratedOnly,Tauri 会 candle 出 main.wixobj"
    exit 1
}

Push-Location $wixDir
try {
    $wixobj = Join-Path $wixDir 'main.wixobj'
    if (-not (Test-Path $wixobj)) {
        Write-Host "[FAIL] 找不到 main.wixobj: $wixobj" -ForegroundColor Red
        Write-Host "       上次构建 candle 都没跑成。重跑 build 入口生成 .wixobj"
        exit 1
    }

    # 自动收集所有 .wxl 本地化文件,避免 LGHT0102 locale 变量未定义误报
    $wxlFiles = Get-ChildItem -Recurse -Filter '*.wxl' -File
    if ($wxlFiles.Count -eq 0) {
        Write-Host "[WARN] 没找到任何 .wxl,light.exe 可能会因 LGHT0102 失败"
    } else {
        Write-Host "[debug-light] 找到 $($wxlFiles.Count) 个 .wxl:"
        $wxlFiles | ForEach-Object { Write-Host "  - $($_.FullName)" }
    }
    Write-Host ""

    # 拼参数:每个 .wxl 前面要一条 -loc
    $args = @('-nologo', '-ext', 'WixUIExtension', '-ext', 'WixUtilExtension', '-spdb')
    foreach ($f in $wxlFiles) {
        $args += '-loc'
        $args += $f.FullName
    }
    $args += '-out'
    $args += (Join-Path $wixDir 'debug-test.msi')
    $args += 'main.wixobj'

    Write-Host "[debug-light] 调用: $light $($args -join ' ')"
    Write-Host "[debug-light] (这一步可能要 5-15 分钟,大 cabinet 压缩慢)"
    Write-Host ""

    # 完整捕获 stdout/stderr 到日志
    & $light @args *>&1 | Tee-Object -FilePath $LogPath
    $exit = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "[debug-light] light.exe 退出码 = $exit"
Write-Host "[debug-light] 完整日志: $LogPath"
Write-Host ""
Write-Host "──────────── 日志最后 80 行 (LGHT0xxx 在这里) ────────────"
Get-Content $LogPath -Tail 80 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
Write-Host "──────────────────────────────────────────────────────────"

exit $exit
