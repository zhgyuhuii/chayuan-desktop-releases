<#
.SYNOPSIS
  装机后手测本地 LLM runtime 是否跑通,输出可粘贴日志。

.DESCRIPTION
  顺序:
    1. 拉 /runtime/llama/install-info 看路径
    2. 拉 /runtime/llama/status 看当前状态
    3. 如果 stopped,POST /runtime/llama/start 等就绪
    4. POST /v1/chat/completions 直打 llama-server 验证 OpenAI-compat
    5. /runtime/llama/status 收尾确认
#>
[CmdletBinding()]
param(
    [string]$SidecarBase = 'http://127.0.0.1:62581',
    [string]$Question = '中国首都是?'
)

try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); chcp 65001 > $null 2>&1 } catch {}
$ErrorActionPreference = 'Stop'

$logFile = Join-Path $env:TEMP "chayuan-local-runtime-test.log"
$out = [System.Text.StringBuilder]::new()

function W($t) {
    Write-Host $t
    [void]$out.AppendLine($t)
}

W "=== 本地 LLM runtime 装机手测 ==="
W "时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
W "sidecar: $SidecarBase"
W ""

W "── 1. /runtime/llama/install-info ──"
$info = Invoke-RestMethod -Uri "$SidecarBase/runtime/llama/install-info" -TimeoutSec 10
W ($info.data | ConvertTo-Json -Depth 5)
W ""

W "── 2. /runtime/llama/status (当前) ──"
$status = Invoke-RestMethod -Uri "$SidecarBase/runtime/llama/status" -TimeoutSec 10
W ($status.data | ConvertTo-Json -Depth 5)
W ""

if ($status.data.state -ne 'ready') {
    W "── 3. /runtime/llama/start ──"
    $started = Invoke-RestMethod -Uri "$SidecarBase/runtime/llama/start" -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 90
    W ($started.data | ConvertTo-Json -Depth 5)
    if ($started.data.state -ne 'ready') {
        W "[FAIL] 启动失败:$($started.data.last_error)"
        $out.ToString() | Set-Content -Path $logFile -Encoding UTF8
        Write-Host ""
        Write-Host "日志已写到: $logFile"
        exit 1
    }
}

$endpoint = $status.data.endpoint
if (-not $endpoint) { $endpoint = (Invoke-RestMethod -Uri "$SidecarBase/runtime/llama/status").data.endpoint }
W ""
W "── 4. POST $endpoint/v1/chat/completions ──"
$payload = @{
    model = 'auto'
    messages = @(@{ role = 'user'; content = $Question })
    max_tokens = 64
    stream = $false
} | ConvertTo-Json -Depth 5

try {
    $resp = Invoke-RestMethod -Uri "$endpoint/v1/chat/completions" -Method Post -ContentType 'application/json' -Body $payload -TimeoutSec 30
    W "Q: $Question"
    W "A: $($resp.choices[0].message.content)"
} catch {
    W "[FAIL] OpenAI-compat 调用失败: $_"
}
W ""

W "── 5. /runtime/llama/status (收尾) ──"
W (Invoke-RestMethod -Uri "$SidecarBase/runtime/llama/status").data | ConvertTo-Json -Depth 5
W ""

$out.ToString() | Set-Content -Path $logFile -Encoding UTF8
Write-Host ""
Write-Host "日志已写到: $logFile"
