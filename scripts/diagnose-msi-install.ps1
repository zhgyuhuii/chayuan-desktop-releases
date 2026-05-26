<#
.SYNOPSIS
  跑 dist-integrated\*.msi 的安装诊断:启 msiexec + verbose log,
  装完(或失败)后自动 grep 关键错误段落,同时打印控制台 + 落到
  dist-integrated\diagnose-output.txt 方便复制粘贴。

.DESCRIPTION
  用法:双击 scripts\diagnose-msi-install.cmd 即可。脚本会:
    1. 自动在 dist-integrated\ 下找最新的 .msi
    2. 用 msiexec /i <msi> /l*v <log> 启交互式安装
    3. 弹出系统安装 UI,你正常点"下一步""安装"——错误对话框出现时点关闭
    4. 等 msiexec 退出
    5. 解析日志,把 1305 / file open / cabinet 等关键错误及上下文摘出来
    6. 在控制台打印 + 落到 dist-integrated\diagnose-output.txt
#>
[CmdletBinding()]
param(
    [string]$MsiPath,
    [string]$LogPath
)

try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    chcp 65001 > $null 2>&1
} catch {}

$ErrorActionPreference = 'Stop'

$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$distDir       = Join-Path $WorkspaceRoot 'dist-integrated'

# 自动找 .msi
if (-not $MsiPath) {
    if (-not (Test-Path $distDir)) {
        Write-Host "[FAIL] 找不到 dist-integrated 目录: $distDir" -ForegroundColor Red
        Write-Host "       先跑 .\build-desktop.cmd -IntegratedOnly 出 .msi 再来"
        exit 1
    }
    $msi = Get-ChildItem -Path $distDir -Filter '*.msi' -File -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending |
           Select-Object -First 1
    if (-not $msi) {
        Write-Host "[FAIL] dist-integrated\ 下没有 .msi 文件" -ForegroundColor Red
        Write-Host "       先跑 .\build-desktop.cmd -IntegratedOnly 出 .msi 再来"
        exit 1
    }
    $MsiPath = $msi.FullName
}

if (-not (Test-Path $MsiPath)) {
    Write-Host "[FAIL] MSI 文件不存在: $MsiPath" -ForegroundColor Red
    exit 1
}

if (-not $LogPath) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $LogPath = Join-Path $distDir "msi-install-$stamp.log"
}

$summaryPath = Join-Path $distDir 'diagnose-output.txt'

$msiSize = (Get-Item $MsiPath).Length / 1MB
Write-Host "[diagnose] MSI:    $MsiPath  ($([math]::Round($msiSize, 1)) MB)"
Write-Host "[diagnose] 日志:   $LogPath"
Write-Host "[diagnose] 摘要:   $summaryPath"
Write-Host ""
Write-Host "正在启动 Windows Installer。完成以下任一情况后,关掉那个对话框,"
Write-Host "本脚本会自动继续解析日志:"
Write-Host "  - 正常装完点 '完成'"
Write-Host "  - 报错(像'系统无法打开指定的设备或文件')点 '关闭'"
Write-Host "  - 用户取消点 '取消'"
Write-Host ""
Write-Host "(msiexec 启动中,可能需要几秒响应 UAC 提示...)"
Write-Host ""

# /i      安装
# /l*v    verbose 日志(* = 全级别;v = verbose 包括 OutputDebugString)
# 启动后阻塞等 msiexec 退出
$msiexecArgs = @('/i', "`"$MsiPath`"", '/l*v', "`"$LogPath`"")
$proc = Start-Process -FilePath 'msiexec.exe' -ArgumentList $msiexecArgs -PassThru -Wait
$exitCode = $proc.ExitCode

Write-Host ""
Write-Host "[diagnose] msiexec 退出码 = $exitCode"
$exitMsg = switch ($exitCode) {
    0    { '成功 (但请仍看下面摘要)' }
    1602 { '用户取消' }
    1603 { '通用致命错误 (安装中止)' }
    1605 { '本次操作只对当前安装的产品有效' }
    1618 { '另一个安装在进行中' }
    1619 { 'MSI 文件打不开 (可能签名 / 路径 / 权限问题)' }
    1620 { 'MSI 文件无效' }
    1638 { '同版本已安装' }
    1641 { '安装成功,需要重启' }
    3010 { '安装成功,需要重启' }
    default { '其它,见日志' }
}
Write-Host "[diagnose] 含义:  $exitMsg"
Write-Host ""

# 解析日志:抓关键错误段
if (-not (Test-Path $LogPath)) {
    Write-Host "[FAIL] 日志文件没生成: $LogPath" -ForegroundColor Red
    Write-Host "       msiexec 可能压根没启动。看看是不是被杀软拦了。"
    exit 1
}

$logSize = (Get-Item $LogPath).Length / 1MB
Write-Host "[diagnose] 日志大小: $([math]::Round($logSize, 1)) MB"
Write-Host ""

# 多个 pattern 抓错误行 + 前后上下文
$patterns = @(
    'Error 1305', 'Error 2350', 'Error 2755', 'Error 1603',
    'Error 1605', 'Error 1618', 'Error 1619', 'Error 1620',
    'cannot open', 'unable to open',
    '无法打开', '无法读取', '指定的设备',
    'CustomAction.*returned actual error',
    'returning 16\d\d',
    'Action ended.*Return value 3',
    'MainEngineThread is returning',
    'Disallowed character.*in path',
    'Cabinet:.*invalid', 'CreateFile.*failed',
    'access is denied', '拒绝访问'
)

$matches = Select-String -Path $LogPath -Pattern $patterns -Context 3,3 -ErrorAction SilentlyContinue

$out = New-Object System.Text.StringBuilder
[void]$out.AppendLine("=== chayuan MSI 安装诊断 ===")
[void]$out.AppendLine("时间:      $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
[void]$out.AppendLine("MSI 文件:  $MsiPath")
[void]$out.AppendLine("MSI 大小:  $([math]::Round($msiSize, 1)) MB")
[void]$out.AppendLine("退出码:    $exitCode  ($exitMsg)")
[void]$out.AppendLine("日志:      $LogPath ($([math]::Round($logSize, 1)) MB)")
[void]$out.AppendLine("")

if ($matches -and $matches.Count -gt 0) {
    [void]$out.AppendLine("──── 命中 $($matches.Count) 条疑似错误,按时间序 ────")
    [void]$out.AppendLine("")
    foreach ($m in $matches) {
        [void]$out.AppendLine("┌─ 行 $($m.LineNumber)  match: $($m.Pattern)")
        foreach ($pre in $m.Context.PreContext) {
            [void]$out.AppendLine("│  $pre")
        }
        [void]$out.AppendLine("│> $($m.Line)")
        foreach ($post in $m.Context.PostContext) {
            [void]$out.AppendLine("│  $post")
        }
        [void]$out.AppendLine("└─")
        [void]$out.AppendLine("")
    }
} else {
    [void]$out.AppendLine("──── 没命中常见错误 pattern ────")
    [void]$out.AppendLine("贴日志最后 80 行供分析:")
    [void]$out.AppendLine("")
    Get-Content $LogPath -Tail 80 | ForEach-Object {
        [void]$out.AppendLine("  $_")
    }
}

# 落盘 + 打印
$out.ToString() | Set-Content -Path $summaryPath -Encoding UTF8
Write-Host "────────── 诊断摘要 (同时写入 $summaryPath) ──────────"
Write-Host $out.ToString()
Write-Host "──────────────────────────────────────────────────────"
Write-Host ""
Write-Host "把上面这段(或 $summaryPath 整个文件内容)贴回给我。"
