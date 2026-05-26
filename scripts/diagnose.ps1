<#
.SYNOPSIS
  本地 LLM runtime 诊断。装机后跑 / 用户报 bug 贴日志。

.DESCRIPTION
  步骤:
    1) 探 sidecar 进程:Get-Process chayuan-server
       不在 → 友好提示退出 (exit 2)
    2) 在 → curl GET /runtime/diagnose 拿 JSON
    3) JSON → markdown,打印 stdout + 写 %TEMP%\chayuan-diagnose-<ts>.md

.PARAMETER SidecarBase
  默认 http://127.0.0.1:62581;改成其它 base 可对接非默认端口。
#>
[CmdletBinding()]
param(
    [string]$SidecarBase = 'http://127.0.0.1:62581'
)

try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); chcp 65001 > $null 2>&1 } catch {}
$ErrorActionPreference = 'Continue'

$ts = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$logFile = Join-Path $env:TEMP "chayuan-diagnose-$ts.md"
$out = [System.Text.StringBuilder]::new()
function W($t) {
    Write-Host $t
    [void]$out.AppendLine($t)
}

W "# Chayuan 本地 Runtime 诊断报告"
W ""
W "- 时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
W "- 系统: Windows ($([System.Environment]::OSVersion.VersionString))"
W "- sidecar base: $SidecarBase"
W ""

# 1) 探 sidecar 进程
$proc = Get-Process chayuan-server -ErrorAction SilentlyContinue
if (-not $proc) {
    W "## ✗ sidecar 进程未发现"
    W ""
    W "Get-Process chayuan-server 没找到进程,说明 chayuan-server 没在跑。"
    W "请先启动 Chayuan 桌面应用,或检查装机日志:"
    W "  %LOCALAPPDATA%\chayuan\logs\sidecar.log"
    W ""
    $out.ToString() | Set-Content -Path $logFile -Encoding UTF8
    Write-Host ""
    Write-Host "日志写到: $logFile"
    exit 2
}

W "## ✓ sidecar 进程在跑"
W ""
W "- pid: $($proc.Id)"
W "- 启动: $($proc.StartTime)"
W ""

# 2) curl /runtime/diagnose
try {
    $resp = Invoke-RestMethod -Uri "$SidecarBase/runtime/diagnose" -TimeoutSec 15
    $report = $resp.data
} catch {
    W "## ✗ /runtime/diagnose 调用失败"
    W ""
    W "错误: $_"
    W ""
    $out.ToString() | Set-Content -Path $logFile -Encoding UTF8
    Write-Host ""
    Write-Host "日志写到: $logFile"
    exit 2
}

W "## 结果: $($report.summary.ok) ✓ / $($report.summary.warn) ⚠ / $($report.summary.fail) ✗"
W ""
W "- chayuan-server: $($report.chayuan_server_version) (Python $($report.python_version), $($report.platform))"
W "- chayuan_root: $($report.chayuan_root)"
W ""
W "| 检查项 | 状态 | 说明 |"
W "|---|---|---|"
foreach ($c in $report.checks) {
    $icon = switch ($c.severity) { 'ok' { '✓' } 'warn' { '⚠' } 'fail' { '✗' } default { '?' } }
    $detail = $c.detail -replace '\|', '\|'
    W "| $($c.name) | $icon | $detail |"
}
W ""

$out.ToString() | Set-Content -Path $logFile -Encoding UTF8

Write-Host ""
Write-Host "日志已写到: $logFile"

# exit code:有 fail → 1,全 ok/warn → 0
if ($report.summary.fail -gt 0) { exit 1 } else { exit 0 }
