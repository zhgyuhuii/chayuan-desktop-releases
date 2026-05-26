<#
.SYNOPSIS
  一键拉起察元开发环境:chayuan-server + 6 个本地模型服务 + 桌面 Tauri app。

.DESCRIPTION
  做的事(顺序):
    1. 调 dev-start.ps1 -Bg 拉起 chayuan-server(后台,PID 写 %TEMP%\chayuan-dev.pid)
    2. 轮询 /healthz,最多 60s
    3. 对 chat / embedding / rerank / asr / image-embedding 5 个 cap
       POST /runtime/llama/<cap>/start(默认模型从后端 yaml 取)
    4. GET /runtime/ocr/health 探测 OCR(进程内,不需要 start)
    5. cd chayuan-client && pnpm --filter @chayuan/desktop dev(前台,Ctrl+C 只关桌面)

  停止环境:.\scripts\dev-all.ps1 -Stop
    → kill chayuan-dev.pid + 各 capability 的 sidecar(POST stop)

  注意:
    - chayuan-server 没起 / 老版本没 /runtime/ocr/health 端点时,OCR 探测会
      跳过并 warn,不视为失败。
    - 桌面 app 启动慢(Rust 首次编译几分钟),不要中途按 Ctrl+C。
    - 若桌面 app 不需要(只跑服务测试),加 -NoDesktop。

.PARAMETER NoDesktop
  只拉服务,不起桌面(适合后端联调 / CI)

.PARAMETER NoCapabilities
  起 server 但不自动拉 5 个 cap(用户自己在设置页点启动)

.PARAMETER Stop
  停掉环境:server + 所有 cap。

.PARAMETER Restart
  先 -Stop 再起。用在 server 跑着但代码有更新的场景
  (典型症状:OCR /runtime/ocr/health 返 404)。

.PARAMETER Port
  chayuan-server API 端口(默认 62581,可被 basic_settings.yaml 覆盖)

.EXAMPLE
  .\scripts\dev-all.ps1                       # 全套
  .\scripts\dev-all.ps1 -NoDesktop            # 只服务
  .\scripts\dev-all.ps1 -NoCapabilities       # server only
  .\scripts\dev-all.ps1 -Restart              # 拉新代码后重启全套
  .\scripts\dev-all.ps1 -Stop                 # 关
#>
[CmdletBinding()]
param(
    [switch]$NoDesktop,
    [switch]$NoCapabilities,
    [switch]$Stop,
    [switch]$Restart,
    [int]$Port = 62581
)

try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    chcp 65001 > $null 2>&1
} catch {}
$env:PYTHONIOENCODING = 'utf-8'

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Write-Ok    { param($m) Write-Host "✓ $m" -ForegroundColor Green }
function Write-Warn  { param($m) Write-Host "⚠ $m" -ForegroundColor Yellow }
function Write-Err   { param($m) Write-Host "✗ $m" -ForegroundColor Red }
function Write-Step  { param($m) Write-Host "→ $m" -ForegroundColor Cyan }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo      = Split-Path -Parent $ScriptDir
$Client    = Join-Path $Repo 'chayuan-client'
$PidFile   = Join-Path $env:TEMP 'chayuan-dev.pid'
$LogFile   = Join-Path $env:TEMP 'chayuan-dev.log'

$Capabilities = @('chat', 'embedding', 'rerank', 'asr', 'image-embedding')

# ────────────────── Stop 分支 ──────────────────
if ($Stop) {
    Write-Step '停止 chayuan 开发环境'

    # 1) 让 server 自己关掉所有 cap(更干净:释放端口 + cleanup tmp 文件)
    foreach ($cap in $Capabilities) {
        try {
            $null = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/runtime/llama/$cap/stop" -TimeoutSec 5 -ErrorAction Stop
            Write-Ok "stop sidecar: $cap"
        } catch {
            # server 已挂 / cap 没起,忽略
        }
    }

    # 2) kill server 主进程 + 整个进程树(/T 关键!)
    # 之前用 Stop-Process -Force 只杀单进程,留下子孙进程占着 8509 配置面板 + sidecar
    # 端口(62582/62583/...),下次启动 preflight 探到端口被占直接 abort。
    if (Test-Path $PidFile) {
        $svrPid = Get-Content $PidFile -ErrorAction SilentlyContinue
        if ($svrPid -and (Get-Process -Id $svrPid -ErrorAction SilentlyContinue)) {
            & taskkill /F /T /PID $svrPid 2>$null | Out-Null
            Write-Ok "kill chayuan-server tree (pid=$svrPid + 子进程)"
        }
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    } else {
        Write-Warn "$PidFile 不存在,server 可能不是本脚本起的"
    }

    # 3) 兜底:按端口反查孤儿进程并清掉(server 主进程之前被 -Force 强杀过、
    #    子进程脱离父子关系变孤儿;或 server 不是本脚本起的)
    foreach ($p in @($Port, 8509, 62582, 62583, 62584, 62585, 62586)) {
        try {
            $line = (netstat -ano | Select-String -Pattern ":$p\s+.*LISTENING" | Select-Object -First 1).ToString()
            if ($line -match '\s(\d+)\s*$') {
                $orphan = [int]$matches[1]
                & taskkill /F /T /PID $orphan 2>$null | Out-Null
                Write-Ok "kill orphan pid=$orphan (端口 :$p)"
            }
        } catch {}
    }

    Write-Ok '环境已停止'
    exit 0
}

# ────────────────── 1. server ──────────────────
Write-Step 'Step 1/3: 拉起 chayuan-server (后台)'

# 已经在跑就跳过(避免端口冲突 / 双开)
$alreadyUp = $false
try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2 -ErrorAction Stop
    if ($r.StatusCode -eq 200) { $alreadyUp = $true }
} catch {}

# -Restart:server 跑着也强制重启(代码有更新时用)
if ($Restart -and $alreadyUp) {
    Write-Step "-Restart: 先 stop server + 所有 cap"
    foreach ($cap in $Capabilities) {
        try {
            $null = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/runtime/llama/$cap/stop" -TimeoutSec 5 -ErrorAction Stop
        } catch {}
    }
    if (Test-Path $PidFile) {
        $svrPid = Get-Content $PidFile -ErrorAction SilentlyContinue
        if ($svrPid -and (Get-Process -Id $svrPid -ErrorAction SilentlyContinue)) {
            Stop-Process -Id $svrPid -Force -ErrorAction SilentlyContinue
            Write-Ok "kill chayuan-server pid=$svrPid"
        }
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    } else {
        Write-Warn "$PidFile 不存在,server 不是本脚本起的;尝试按端口找进程"
        # 用 netstat 反查 PID 兜底
        try {
            $line = (netstat -ano | Select-String -Pattern ":$Port\s+.*LISTENING").ToString()
            if ($line -match '\s(\d+)\s*$') {
                $foundPid = [int]$matches[1]
                Stop-Process -Id $foundPid -Force -ErrorAction SilentlyContinue
                Write-Ok "kill pid=$foundPid (从 netstat 反查 :$Port)"
            }
        } catch {}
    }
    Start-Sleep -Seconds 2
    $alreadyUp = $false
}

if ($alreadyUp) {
    # 顺手探测 /runtime/ocr/health 检测代码新鲜度,旧 server 没此端点会 404
    try {
        $null = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/runtime/ocr/health" -TimeoutSec 2 -ErrorAction Stop
    } catch {
        if ($_.Exception.Response.StatusCode.value__ -eq 404) {
            Write-Warn "server 在跑但缺 /runtime/ocr/health(旧代码),建议:.\scripts\dev-all.ps1 -Restart"
        }
    }
    Write-Ok "chayuan-server 已在 :$Port 跑着,复用"
} else {
    & "$ScriptDir\dev-start.ps1" -Bg -Port $Port
    if ($LASTEXITCODE -ne 0) {
        Write-Err "dev-start.ps1 失败 (exit=$LASTEXITCODE),看 $LogFile"
        exit 2
    }
}

# 再次确认 health(dev-start.ps1 内部也会探,这里是兜底)
$ok = $false
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 500
}
if (-not $ok) {
    Write-Err "/healthz 不通,放弃。日志:$LogFile"
    exit 3
}
Write-Ok "chayuan-server ready @ http://127.0.0.1:$Port"

# ────────────────── 2. 5 个 cap + OCR ──────────────────
if ($NoCapabilities) {
    Write-Warn 'Step 2/3: -NoCapabilities 跳过模型服务自动启动'
} else {
    Write-Step 'Step 2/3: 启动 5 个本地模型服务 (chat / embedding / rerank / asr / image-embedding)'

    foreach ($cap in $Capabilities) {
        # 已 ready 跳过(避免重启浪费 30s 加载时间)
        try {
            $st = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/runtime/llama/$cap/status" -TimeoutSec 3 -ErrorAction Stop
            $state = if ($st.data) { $st.data.state } else { $st.state }
            if ($state -eq 'ready') {
                Write-Ok "${cap}: 已 ready,跳过"
                continue
            }
        } catch {
            # status endpoint 404 / 500 → 服务不支持此 cap(老 server),继续 try start
        }

        try {
            Write-Host "  $cap ... " -NoNewline -ForegroundColor DarkGray
            $r = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/runtime/llama/$cap/start" -Body '{}' -ContentType 'application/json' -TimeoutSec 90 -ErrorAction Stop
            $newState = if ($r.data) { $r.data.state } else { $r.state }
            if ($newState -eq 'ready') {
                Write-Host "ready" -ForegroundColor Green
            } elseif ($newState -eq 'starting') {
                Write-Host "starting (后台继续加载,看 footer 状态点)" -ForegroundColor Yellow
            } else {
                Write-Host "state=$newState" -ForegroundColor Yellow
            }
        } catch {
            $msg = $_.Exception.Message
            Write-Host "fail: $msg" -ForegroundColor Red
            # 单个 cap 失败不阻断其它(比如 lite 版没嵌 chat,继续起 embedding)
        }
    }

    # OCR 探测(进程内,不需要 start)
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/runtime/ocr/health" -TimeoutSec 5 -ErrorAction Stop
        $ocrState = if ($h.data) { $h.data.state } else { $h.state }
        if ($ocrState -eq 'ready') {
            Write-Ok 'ocr: ready (进程内 rapidocr)'
        } else {
            $reason = if ($h.data) { $h.data.reason } else { $h.reason }
            Write-Warn "ocr: $ocrState ($reason)"
        }
    } catch {
        Write-Warn 'ocr: /runtime/ocr/health 不可达 (旧版 server 没此端点,可忽略)'
    }
}

# ────────────────── 3. desktop ──────────────────
if ($NoDesktop) {
    Write-Step 'Step 3/3: -NoDesktop 跳过桌面 app'
    Write-Host ''
    Write-Ok '环境就绪,服务在后台跑'
    Write-Host "    日志:Get-Content -Wait $LogFile" -ForegroundColor DarkGray
    Write-Host "    停:  .\scripts\dev-all.ps1 -Stop" -ForegroundColor DarkGray
    exit 0
}

Write-Step 'Step 3/3: 启动桌面 Tauri app (前台,Ctrl+C 只关桌面、server 留着)'

if (-not (Test-Path $Client)) {
    Write-Err "chayuan-client 目录不存在:$Client"
    exit 4
}

$pnpmExe = Get-Command pnpm -ErrorAction SilentlyContinue
if (-not $pnpmExe) {
    Write-Err 'pnpm 不在 PATH,先装:npm install -g pnpm'
    exit 5
}

Set-Location $Client
Write-Host ''
Write-Host '────── Tauri dev 启动 (首次编译 Rust 几分钟,耐心) ──────' -ForegroundColor DarkGray
& pnpm --filter '@chayuan/desktop' dev
$tauriExit = $LASTEXITCODE

Write-Host ''
if ($tauriExit -eq 0) {
    Write-Ok '桌面 app 已退出'
} else {
    Write-Warn "桌面 app 退出码 $tauriExit"
}
Write-Host "    server 仍在跑 (pid 文件:$PidFile),停掉:.\scripts\dev-all.ps1 -Stop" -ForegroundColor DarkGray
exit $tauriExit
