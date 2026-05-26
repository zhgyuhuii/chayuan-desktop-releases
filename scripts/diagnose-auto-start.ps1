#requires -Version 5.1
<#
.SYNOPSIS
    察元桌面端"装机即用本地模型服务自启"链路诊断。

.DESCRIPTION
    自动发现安装目录 / 数据目录,把以下信息收齐成 markdown,直接复制粘贴
    给 Claude 排查:

      1. 安装包版本(chayuan-server.exe mtime / 大小)
      2. bundled_models 源(install dir) + 目标(data dir)布局
      3. sidecar_settings.json + local_runtime.yaml 内容
      4. /runtime/llama/registry + /runtime/diagnose HTTP 探针
      5. chayuan-server 进程 + 端口 + tail 200 行日志
      6. (可选)直接命令行重启 chayuan-server,抓 [bootstrap-preload] /
         [auto-start] 完整 stdout 链路(最关键的一步)

.PARAMETER InstallDir
    显式指定 chayuan-desktop 安装目录(含 chayuan-server.exe)。
    省略时按常见路径自动探测。

.PARAMETER DataDir
    显式指定 CHAYUAN_ROOT 数据目录。省略时读 desktop.json,再退默认。

.PARAMETER Port
    chayuan-server API 端口,默认 62581。

.PARAMETER CaptureStdout
    Switch:跑这步会先 kill 现有 chayuan-server.exe,然后命令行重启它,
    抓 30 秒 stdout 看 [bootstrap-preload] / [auto-start] 链路 ——
    会临时打断 app 内的服务。默认 off。

.PARAMETER OutFile
    把诊断结果同时写到这个文件。默认只打到终端。

.EXAMPLE
    .\diagnose-auto-start.ps1

.EXAMPLE
    .\diagnose-auto-start.ps1 -CaptureStdout -OutFile diag.md
#>
[CmdletBinding()]
param(
    [string]$InstallDir = '',
    [string]$DataDir    = '',
    [int]$Port          = 62581,
    [switch]$CaptureStdout,
    [string]$OutFile    = ''
)

$ErrorActionPreference = 'Continue'

# 输出走 StringBuilder,最后一次性打出来 + (可选)落盘
$sb = [System.Text.StringBuilder]::new()
function Out-Section { param([string]$Title) [void]$sb.AppendLine(""); [void]$sb.AppendLine("## $Title"); [void]$sb.AppendLine("") }
function Out-Code { param([string]$Lang = '', [string]$Text)
    [void]$sb.AppendLine("``````$Lang")
    [void]$sb.AppendLine($Text)
    [void]$sb.AppendLine("``````")
}
function Out-Line { param([string]$Text) [void]$sb.AppendLine($Text) }
function Out-KV   { param([string]$K, $V) [void]$sb.AppendLine("- **$K**: $V") }

# 截断超长输出,避免粘贴给 Claude 时炸窗口
function Limit-Text {
    param([string]$Text, [int]$MaxLines = 200)
    if (-not $Text) { return '(空)' }
    $lines = $Text -split "`r?`n"
    if ($lines.Count -le $MaxLines) { return $Text }
    $head = $lines[0..[int]([math]::Min(50, $lines.Count - 1))]
    $tail = $lines[($lines.Count - $MaxLines + $head.Count)..($lines.Count - 1)]
    return (($head -join "`n") + "`n... (中间 $($lines.Count - $MaxLines) 行省略) ...`n" + ($tail -join "`n"))
}

# ───────── 0. 头部 + 环境 ─────────
$now = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
Out-Line "# 察元自启诊断报告"
Out-Line ""
Out-KV '生成时间' $now
Out-KV 'Host'      $env:COMPUTERNAME
Out-KV 'User'      $env:USERNAME
Out-KV 'OS'        ([System.Environment]::OSVersion.VersionString)
Out-KV 'PSVersion' $PSVersionTable.PSVersion.ToString()

# ───────── 1. 探测 InstallDir ─────────
Out-Section '1. 探测安装目录'

if ($InstallDir -and (Test-Path "$InstallDir\chayuan-server.exe")) {
    Out-Line ('用户显式指定:`{0}`' -f $InstallDir)
} else {
    # Tauri 2 WiX MSI / NSIS 装机后,Windows 安装目录的命名变体太多 ——
    # tauri.conf.json productName / publisher / wix.* 改动都影响最终目录名。
    # 这里穷举常见组合 + 用 Get-CimInstance 通过监听 62581 端口的进程反推
    # 真实 exe 路径(最稳的兜底)。
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\chayuan-desktop",
        "$env:LOCALAPPDATA\Programs\Chayuan",
        "$env:LOCALAPPDATA\Programs\Chayuan Desktop",
        "$env:ProgramFiles\chayuan-desktop",
        "$env:ProgramFiles\Chayuan",
        "$env:ProgramFiles\Chayuan Desktop",
        "${env:ProgramFiles(x86)}\chayuan-desktop",
        "${env:ProgramFiles(x86)}\Chayuan",
        "${env:ProgramFiles(x86)}\Chayuan Desktop"
    )
    $InstallDir = $null
    foreach ($c in $candidates) {
        if ($c -and (Test-Path "$c\chayuan-server.exe")) {
            $InstallDir = $c
            break
        }
    }
    # 兜底:从监听 62581 端口的进程拿真实 exe 路径
    if (-not $InstallDir) {
        try {
            $owner = (Get-NetTCPConnection -LocalPort 62581 -State Listen -ErrorAction Stop |
                      Select-Object -First 1).OwningProcess
            if ($owner) {
                $p = Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction Stop
                if ($p -and $p.ExecutablePath) {
                    $exeDir = Split-Path -Parent $p.ExecutablePath
                    if (Test-Path "$exeDir\chayuan-server.exe") {
                        $InstallDir = $exeDir
                        Out-Line ('从监听 62581 的进程反推到:`{0}`' -f $InstallDir)
                    }
                }
            }
        } catch {}
    }
    if (-not $InstallDir) {
        Out-Line "**自动探测失败** — 没在以下路径找到 chayuan-server.exe,62581 端口也没探到进程:"
        $candidates | ForEach-Object { Out-Line "  - $_" }
        Out-Line ""
        Out-Line '请重跑:`.\diagnose-auto-start.ps1 -InstallDir ''C:\path\to\chayuan-desktop''`'
    }
}
$installDirDisp = if ($InstallDir) { $InstallDir } else { '(未找到)' }
Out-KV 'InstallDir' $installDirDisp

if ($InstallDir) {
    $exe = Join-Path $InstallDir 'chayuan-server.exe'
    if (Test-Path $exe) {
        $fi = Get-Item $exe
        Out-KV 'chayuan-server.exe size'  ("{0:N0} bytes ({1:N1} MB)" -f $fi.Length, ($fi.Length / 1MB))
        Out-KV 'chayuan-server.exe mtime' ($fi.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
    }
    $appExe = Get-ChildItem -Path $InstallDir -Filter '*.exe' -ErrorAction SilentlyContinue |
              Where-Object { $_.Name -ne 'chayuan-server.exe' -and $_.Name -notlike 'uninstall*' } |
              Select-Object -First 1
    if ($appExe) { Out-KV '主程序 exe' "$($appExe.Name) ($($appExe.LastWriteTime))" }
}

# ───────── 2. 探测 DataDir(读 desktop.json) ─────────
Out-Section '2. 探测数据目录'

$desktopJson = Join-Path $env:APPDATA 'chayuan\desktop.json'
Out-KV 'desktop.json 路径' $desktopJson

if ($DataDir -and (Test-Path $DataDir)) {
    Out-Line ('用户显式指定 `{0}`' -f $DataDir)
} elseif (Test-Path $desktopJson) {
    try {
        $cfg = Get-Content $desktopJson -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.data_dir) {
            $DataDir = $cfg.data_dir
            Out-KV 'desktop.json data_dir'         $cfg.data_dir
            Out-KV 'desktop.json version'          $cfg.version
            $linkedId = if ($cfg.linked_install_id) { $cfg.linked_install_id } else { '(空)' }
            Out-KV 'desktop.json linked_install_id' $linkedId
        }
    } catch {
        Out-Line "**读 desktop.json 失败**:$_"
    }
}
if (-not $DataDir) {
    $DataDir = Join-Path $env:APPDATA 'chayuan'
    Out-Line "回退到默认数据目录"
}
Out-KV 'DataDir(实际生效)' $DataDir
$envRoot     = if ($env:CHAYUAN_ROOT)                { $env:CHAYUAN_ROOT }                else { '(未设)' }
$envBundled  = if ($env:CHAYUAN_BUNDLED_MODELS_DIR)   { $env:CHAYUAN_BUNDLED_MODELS_DIR }   else { '(未设)' }
Out-KV 'CHAYUAN_ROOT env'                $envRoot
Out-KV 'CHAYUAN_BUNDLED_MODELS_DIR env'  $envBundled

# ───────── 2b. API 端口自动校正(避免探针打错门) ─────────
# 用户不带 -Port 时,从 $DataDir/runtime.json `services.api.port` 取真实端口。
# 历史 bug:默认 62581,但如果服务跑在别的端口(例如运维改过 chayuan.yaml),
# 探针全部 404,被误判成"服务挂了"。用 runtime.json 作真源比 hardcode 准。
$portExplicit = $PSBoundParameters.ContainsKey('Port')
$runtimeJson  = Join-Path $DataDir 'runtime.json'
if (Test-Path $runtimeJson) {
    try {
        $rt = Get-Content $runtimeJson -Raw -Encoding UTF8 | ConvertFrom-Json
        $apiPort = [int]$rt.services.api.port
        if ($apiPort -gt 0) {
            if ($portExplicit) {
                if ($Port -ne $apiPort) {
                    Out-Line ('**warn**:用户传 -Port {0},但 runtime.json 实际 API 端口是 {1};仍用用户值,如要校正请去掉 -Port' -f $Port, $apiPort)
                }
            } else {
                if ($Port -ne $apiPort) {
                    Out-Line ('runtime.json 中 API 端口 = {0}(默认值 {1} 被自动校正)' -f $apiPort, $Port)
                    $Port = $apiPort
                } else {
                    Out-Line ('runtime.json 中 API 端口 = {0} ✓' -f $apiPort)
                }
            }
        }
    } catch {
        Out-Line ('读 runtime.json 失败,沿用 -Port={0}:{1}' -f $Port, $_)
    }
} else {
    Out-Line ('runtime.json 不存在,沿用 -Port={0}' -f $Port)
}

# ───────── 3. bundled_models 源 / 目标布局 ─────────
Out-Section '3. bundled_models 布局'

function Dump-BundledTree {
    param([string]$Root, [string]$Label)
    if (-not (Test-Path $Root)) {
        Out-Line ('**{0}**:不存在 `{1}`' -f $Label, $Root)
        return
    }
    Out-Line ('**{0}**(`{1}`):' -f $Label, $Root)
    $caps = Get-ChildItem -Path $Root -Directory -ErrorAction SilentlyContinue
    if (-not $caps) {
        Out-Line '  (空目录)'
        return
    }
    $lines = foreach ($cap in $caps) {
        $files = Get-ChildItem -Path $cap.FullName -File -Recurse -ErrorAction SilentlyContinue
        $nonHidden = $files | Where-Object { -not $_.Name.StartsWith('.') }
        $total = ($nonHidden | Measure-Object -Property Length -Sum).Sum
        $totalMB = if ($total) { '{0:N1} MB' -f ($total / 1MB) } else { '0 MB' }
        "  - $($cap.Name)/  files=$($nonHidden.Count)  size=$totalMB"
    }
    Out-Line ($lines -join "`n")
}

if ($InstallDir) { Dump-BundledTree (Join-Path $InstallDir 'bundled_models') '源(install dir)' }
if ($DataDir)    { Dump-BundledTree (Join-Path $DataDir    'models\bundled') '目标(data dir 已 seed)' }

# ───────── 4. 关键配置文件 ─────────
Out-Section '4. 关键配置文件'

$cfgFiles = @(
    @{ Path = (Join-Path $DataDir 'data\sidecar_settings.json'); Title = 'sidecar_settings.json'; Lang = 'json' }
    @{ Path = (Join-Path $DataDir 'model_registry\local_runtime.yaml'); Title = 'local_runtime.yaml'; Lang = 'yaml' }
    @{ Path = (Join-Path $DataDir 'runtime.json'); Title = 'runtime.json'; Lang = 'json' }
    @{ Path = (Join-Path $DataDir 'model_registry\local_models.json'); Title = 'local_models.json (前 80 行)'; Lang = 'json' }
)
foreach ($f in $cfgFiles) {
    Out-Line ""
    Out-Line "### $($f.Title)"
    Out-Line "**path**: $($f.Path)"
    if (-not (Test-Path $f.Path)) {
        Out-Line '**(文件不存在)**'
        continue
    }
    $raw = Get-Content $f.Path -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    # local_models.json 可能很大,只取头
    if ($f.Title -like '*local_models.json*') {
        $raw = ($raw -split "`r?`n")[0..80] -join "`n"
    }
    Out-Code -Lang $f.Lang -Text (Limit-Text $raw 120)
}

# ───────── 5. 进程 / 端口 ─────────
Out-Section '5. 进程 / 端口'

# 1) 按进程名找(installer 装机后的常态);dev 模式跑的是 python.exe,这种命名匹配不到
$nameProcs = Get-Process -ErrorAction SilentlyContinue |
             Where-Object { $_.ProcessName -in @('chayuan-server','chayuan-desktop','llama-server','whisper-server','rapidocr_server','piper') }

# 2) 按命令行找(dev 模式 python.exe 跑 -m chayuan / poetry run chayuan-server)
#    Win32_Process.CommandLine 含 chayuan / chayuan-server / chayuan_runtime 字样的进程
$cmdProcs = @()
try {
    $cmdProcs = Get-CimInstance -ClassName Win32_Process -ErrorAction Stop |
                Where-Object {
                    $_.CommandLine -and
                    $_.CommandLine -match '(chayuan-server|chayuan_runtime|\bchayuan\b|llama-server|whisper-server|rapidocr|piper)' -and
                    $_.CommandLine -notmatch 'diagnose-auto-start'
                }
} catch {
    # PS / WMI 取不到 cmdline 时(用户权限/旧 Win)就忍着,nameProcs 兜底
}

$seen = @{}
$rows = @()
foreach ($p in $nameProcs) {
    if ($seen.ContainsKey([int]$p.Id)) { continue }
    $seen[[int]$p.Id] = $true
    $startTime = try { $p.StartTime.ToString('HH:mm:ss') } catch { '?' }
    $rows += ('  - {0,-22} pid={1,-6} RSS={2,7:N1} MB  started={3}' -f $p.ProcessName, $p.Id, ($p.WorkingSet64 / 1MB), $startTime)
}
foreach ($p in $cmdProcs) {
    if ($seen.ContainsKey([int]$p.ProcessId)) { continue }
    $seen[[int]$p.ProcessId] = $true
    $cmd = $p.CommandLine
    if ($cmd.Length -gt 110) { $cmd = $cmd.Substring(0, 107) + '...' }
    $rows += ('  - {0,-22} pid={1,-6} cmd={2}' -f $p.Name, $p.ProcessId, $cmd)
}

if ($rows.Count -gt 0) {
    Out-Line '相关进程(进程名 + 命令行匹配):'
    Out-Line ($rows -join "`n")
} else {
    Out-Line '**没有任何察元相关进程在跑(进程名 / 命令行都没匹配到)**'
}

Out-Line ""
$listen = & netstat -ano 2>$null | Where-Object { $_ -match ':(62581|62582|62583|62584|62585|62586|18380)\s' -and $_ -match 'LISTENING' }
if ($listen) {
    Out-Line '监听端口(自启相关):'
    Out-Code -Lang '' -Text ($listen -join "`n")
    # 再 resolve 每个端口的进程名(对照 Section 5 的 pid)
    $listenRows = @()
    foreach ($line in $listen) {
        if ($line -match ':(\d+)\s.*LISTENING\s+(\d+)') {
            # 用 $listenPort 而不是 $port:PowerShell 变量名大小写不敏感,
            # $port 会覆盖 script param $Port,导致 section 6 探针打到错端口。
            $listenPort = $matches[1]
            $owner = [int]$matches[2]
            $proc  = Get-Process -Id $owner -ErrorAction SilentlyContinue
            $pname = if ($proc) { $proc.ProcessName } else { '(进程已退)' }
            $listenRows += ('  - :{0,-5} pid={1,-6} name={2}' -f $listenPort, $owner, $pname)
        }
    }
    if ($listenRows.Count -gt 0) {
        Out-Line ''
        Out-Line '端口 → 进程映射:'
        Out-Line ($listenRows -join "`n")
    }
} else {
    Out-Line '**没有 62581-62586 / 18380 任一端口在 LISTENING** — server 没起或自启失败'
}

# ───────── 6. HTTP 探针 ─────────
Out-Section "6. HTTP 探针 (port=$Port)"

$base = "http://127.0.0.1:$Port"

function Probe-Endpoint {
    param([string]$Path, [string]$Method = 'GET', [hashtable]$Body)
    $url = "$base$Path"
    try {
        if ($Method -eq 'POST') {
            $json = if ($Body) { $Body | ConvertTo-Json -Compress } else { '{}' }
            $r = Invoke-WebRequest -Uri $url -Method POST -Body $json -ContentType 'application/json' -TimeoutSec 8 -ErrorAction Stop
        } else {
            $r = Invoke-WebRequest -Uri $url -Method GET -TimeoutSec 8 -ErrorAction Stop
        }
        return @{ ok = $true; status = $r.StatusCode; body = $r.Content }
    } catch {
        return @{ ok = $false; error = $_.Exception.Message }
    }
}

$endpoints = @(
    @{ P = '/runtime/llama/registry'; T = '5 capability registry 状态' }
    @{ P = '/runtime/diagnose';       T = 'runtime/diagnose 综合健康' }
    @{ P = "/runtime/llama/chat/status";            T = 'chat status' }
    @{ P = "/runtime/llama/embedding/status";       T = 'embedding status' }
    @{ P = "/runtime/llama/rerank/status";          T = 'rerank status' }
    @{ P = "/runtime/llama/asr/status";             T = 'asr status' }
    @{ P = "/runtime/llama/image-embedding/status"; T = 'image-embedding status' }
    @{ P = '/modality/sidecar/ocr/status'; T = 'RapidOCR sidecar status' }
)
foreach ($e in $endpoints) {
    Out-Line ""
    Out-Line "### GET $($e.P) — $($e.T)"
    $r = Probe-Endpoint -Path $e.P
    if ($r.ok) {
        Out-Code -Lang 'json' -Text (Limit-Text $r.body 60)
    } else {
        Out-Line "**失败**:$($r.error)"
    }
}

# ───────── 6b. ONNX 本地 fallback 探测 ─────────
# 跟 chayuan/server/embeddings/onnx_local.py:_auto_discover_onnx_embed_dir() 和
# chayuan/server/reranker/onnx_local.py:_auto_discover_onnx_rerank_dir() 同逻辑:
# 扫 <DataDir>/models/bundled/{embedding,rerank}/<repo>/,看是否有 tokenizer.json
# + onnx 文件 (5 个候选位置)。命中 → ONNX in-process 路径会激活,绕开 llama-server
# 的 GGUF 要求。这里只做静态预判,运行时实际命中要看 server log 里
# "[OnnxEmbeddings] auto-discovered" / "[OnnxReranker] auto-discovered" 行。
Out-Section '6b. ONNX 本地 fallback 探测(embedding / rerank)'

$onnxCandidates = @(
    'model.onnx',
    'onnx\model_quantized.onnx',
    'onnx\model_int8.onnx',
    'onnx\model.onnx',
    'onnx\model_fp16.onnx'
)

foreach ($cap in @('embedding', 'rerank')) {
    $capRoot = Join-Path $DataDir "models\bundled\$cap"
    Out-Line ""
    Out-Line "### $cap → $capRoot"
    if (-not (Test-Path $capRoot)) {
        Out-Line "**目录不存在** — ONNX fallback 不会命中,$cap 仅能走 llama-server(要 GGUF)"
        continue
    }
    $subdirs = @(Get-ChildItem -Path $capRoot -Directory -ErrorAction SilentlyContinue)
    if ($subdirs.Count -eq 0) {
        Out-Line "目录空 — ONNX fallback 不会命中"
        continue
    }
    $anyHit = $false
    foreach ($sub in $subdirs) {
        $tok = Join-Path $sub.FullName 'tokenizer.json'
        $hasTok = Test-Path $tok
        $hitOnnx = $null
        foreach ($rel in $onnxCandidates) {
            $p = Join-Path $sub.FullName $rel
            if (Test-Path $p) { $hitOnnx = $rel; break }
        }
        if ($hasTok -and $hitOnnx) {
            Out-Line "- **$($sub.Name)**:tokenizer.json ✓ + $hitOnnx ✓ → **ONNX fallback 会命中**"
            $anyHit = $true
        } elseif ($hasTok) {
            Out-Line "- $($sub.Name):tokenizer.json ✓ 但 5 个候选 onnx 文件都缺;ONNX fallback 不会命中"
        } else {
            Out-Line "- $($sub.Name):缺 tokenizer.json;ONNX fallback 不会命中"
        }
    }
    if (-not $anyHit) {
        Out-Line ""
        Out-Line "提示:$cap 当前没有可用 ONNX。业务跑到 $cap 时:"
        Out-Line "  1. 先试 llama-server(需要 GGUF 量化版,如 gpustack/bge-m3-GGUF / *.gguf 单文件)"
        Out-Line "  2. llama-server 起不来 → langchain LocalAIEmbeddings → 缺 API key → 报错"
        Out-Line "  3. 修法:拷一个含 model.onnx + tokenizer.json 的 ONNX 整仓到 $capRoot\<repo>\,"
        Out-Line "     或者拷 GGUF 量化版给 llama-server 用"
    }
}

# ───────── 7. 日志 tail ─────────
Out-Section '7. chayuan-server 日志 tail'

# chayuan-server 实际把日志写到 <CHAYUAN_ROOT>/data/logs/(因为 LOG_PATH = DATA_PATH/logs
# 而 DATA_PATH = CHAYUAN_ROOT/data,不是 CHAYUAN_ROOT/logs 直接)。
#
# 关键陷阱:chayuan/startup.py 在启动 uvicorn 前 dictConfig 把日志 redirect 到
#   <logs>/run_api_server_<ts>/...
# 子目录,所以所有 FastAPI startup hook 的日志(包括 [bootstrap-preload] /
# [auto-start-rapidocr] / [auto-start])都在子目录里。顶层 chayuan.log 只有
# uvicorn 之前的 bootstrap 输出。两层都扫,优先取子目录最新一份。
$logDirCandidates = @(
    (Join-Path $DataDir 'data\logs'),
    (Join-Path $DataDir 'logs')
)
$logDir = $null
foreach ($c in $logDirCandidates) {
    if (Test-Path $c) { $logDir = $c; break }
}
if ($logDir) {
    Out-KV '日志目录' $logDir
    # tail 选择策略:
    #   - lifespan startup hook 的日志(`[bootstrap-preload]` / `[auto-start]` /
    #     `[auto-start-rapidocr]`)写在 `run_api_server_<ts>/run_api_server_<ts>.log`
    #     子文件;顶层 chayuan.log 只有父进程 bootstrap 输出 + 部分子进程透传。
    #   - 父进程在 lifespan 起来之后仍会继续写 chayuan.log(例如 "配置面板启动
    #     事件 ..s 未收到信号"),导致按 mtime 排 chayuan.log 抢前,tail 拿不
    #     到子进程的关键 hook 行。
    #   → 只要存在 run_api_server_* 子目录,就**优先 tail 子目录的最新 .log**,
    #     顶层 chayuan.log 作 fallback。
    $apiSubs = Get-ChildItem -Path $logDir -Directory -Filter 'run_api_server_*' -ErrorAction SilentlyContinue |
               Sort-Object LastWriteTime -Descending
    $apiSub  = if ($apiSubs) { $apiSubs[0] } else { $null }
    $topLogs = Get-ChildItem -Path $logDir -Filter '*.log' -File -ErrorAction SilentlyContinue

    # 给 grep 用的全部日志:最新子目录所有 .log + 顶层 .log
    $allLogs = @()
    if ($apiSub) {
        $allLogs += Get-ChildItem -Path $apiSub.FullName -Filter '*.log' -File -Recurse -ErrorAction SilentlyContinue
        Out-KV '最新 run_api_server 子目录' $apiSub.FullName
    }
    $allLogs += $topLogs

    # tail 目标:优先子目录最新 .log;再 fallback 顶层 chayuan.log。
    $tailTarget = $null
    if ($apiSub) {
        $tailTarget = Get-ChildItem -Path $apiSub.FullName -Filter '*.log' -File -Recurse -ErrorAction SilentlyContinue |
                      Sort-Object LastWriteTime -Descending | Select-Object -First 1
    }
    if (-not $tailTarget -and $topLogs) {
        $tailTarget = $topLogs | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    }

    if ($tailTarget) {
        Out-KV 'tail 目标' ('{0} (mtime={1})' -f $tailTarget.FullName, $tailTarget.LastWriteTime)
        $tail = Get-Content $tailTarget.FullName -Tail 200 -ErrorAction SilentlyContinue
        if ($tail) {
            # 简单脱敏:Bearer / api_key / secret 后跟 16+ 字符全部 *** 掉
            $masked = ($tail -join "`n") -replace '(?i)(bearer|api[_-]?key|secret|token)["= :]+[A-Za-z0-9._\-]{12,}', '$1=***MASKED***'
            Out-Code -Lang '' -Text (Limit-Text $masked 200)
        } else {
            Out-Line '(tail 目标为空文件 — server 可能还在 import 阶段)'
        }
    } else {
        Out-Line '日志目录为空'
    }

    # 同时把 [auto-start] / [bootstrap-preload] 关键字 grep 出来一并展示。
    # 扫所有 run_api_server_* 子目录(不只是最新一个),覆盖"之前跑过但当前
    # 又重启了"的场景;顶层 chayuan.log 也扫,父进程的 print 透传可能落在那。
    Out-Line ''
    Out-Line '### grep 关键字:[bootstrap-preload] / [auto-start] / [auto-start-rapidocr]'
    $grepLogs = @()
    if ($apiSubs) {
        foreach ($sub in $apiSubs) {
            $grepLogs += Get-ChildItem -Path $sub.FullName -Filter '*.log' -File -Recurse -ErrorAction SilentlyContinue
        }
    }
    $grepLogs += $topLogs
    $matches2 = @()
    foreach ($lf in $grepLogs) {
        try {
            $matches2 += Select-String -Path $lf.FullName -Pattern '\[bootstrap-preload\]|\[auto-start\]|\[auto-start-rapidocr\]' -ErrorAction SilentlyContinue |
                         Select-Object -Last 50 |
                         ForEach-Object {
                             $rel = $_.Path.Replace($logDir, '').TrimStart('\','/')
                             '{0}:{1}: {2}' -f $rel, $_.LineNumber, $_.Line
                         }
        } catch {}
    }
    if ($matches2.Count -gt 0) {
        Out-Code -Lang '' -Text (Limit-Text ($matches2 -join "`n") 80)
    } else {
        Out-Line '**未匹到任何 [bootstrap-preload] / [auto-start] / [auto-start-rapidocr] 行**。'
        Out-Line ''
        Out-Line '可能原因:'
        Out-Line '  - lifespan startup hook 还没跑到这步(server 仍在 import,等 30-60s 再来一次)。'
        Out-Line '  - hook 已跑完但 logger 路由把它写到了 stdout 被 Tauri sidecar 吞掉(看 runtime.json 的 `llama.<cap>.state` 验证 — 若是 `ready` 说明 hook 实际跑过)。'
        Out-Line '  - 用户改了 dictConfig 把 chayuan.startup logger 屏蔽了。'
        Out-Line ''
        Out-Line ('已扫日志文件数: {0}(若为 0,说明 run_api_server 子目录连 .log 都还没创建)' -f $grepLogs.Count)
    }
} else {
    Out-Line '**日志目录都不存在**,扫了以下路径:'
    $logDirCandidates | ForEach-Object { Out-Line ('  - `{0}`' -f $_) }
}

# ───────── 8. 可选:抓 sidecar 完整 stdout ─────────
if ($CaptureStdout) {
    Out-Section '8. sidecar 完整 stdout 抓取(30 秒)'

    if (-not $InstallDir) {
        Out-Line '**InstallDir 未知,跳过 stdout 抓取**'
    } else {
        Out-Line "Kill 现有 chayuan-server.exe..."
        try { Get-Process chayuan-server -ErrorAction SilentlyContinue | Stop-Process -Force } catch {}
        Start-Sleep -Seconds 2

        $serverExe = Join-Path $InstallDir 'chayuan-server.exe'
        $tmpStdout = New-TemporaryFile
        $tmpStderr = New-TemporaryFile
        $sidecarBundled = Join-Path $InstallDir 'bundled_models'

        # chayuan-server.exe 是 click group,真正启动子命令是 ``start``。
        # 历史 bug:旧诊断脚本用 ``--host/--port`` 直接传顶层,但 group 顶层不接,
        # 报 "No such option: --host"。host/port 由 env 或 yaml 配,不走 CLI flag。
        # 对齐 Tauri sidecar.rs 里的真实调用:``chayuan-server.exe start -a --single-machine``
        $sidecarArgs = @('start', '-a', '--single-machine')
        Out-Line ('spawn: `{0}` {1}' -f $serverExe, ($sidecarArgs -join ' '))
        Out-Line ('期望 API 端口:{0}(由 runtime.json / chayuan.yaml 配,本步不再 CLI 覆盖)' -f $Port)
        Out-Line "CHAYUAN_ROOT=$DataDir"
        Out-Line "CHAYUAN_BUNDLED_MODELS_DIR=$sidecarBundled"
        Out-Line ""

        $env:CHAYUAN_ROOT = $DataDir
        $env:CHAYUAN_BUNDLED_MODELS_DIR = $sidecarBundled
        $p = Start-Process -FilePath $serverExe `
            -ArgumentList $sidecarArgs `
            -RedirectStandardOutput $tmpStdout.FullName `
            -RedirectStandardError  $tmpStderr.FullName `
            -PassThru -NoNewWindow

        Start-Sleep -Seconds 30

        try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
        # 给子进程留点时间退,把 buffer flush 完
        Start-Sleep -Seconds 1

        $outText = if (Test-Path $tmpStdout) { Get-Content $tmpStdout.FullName -Raw } else { '' }
        $errText = if (Test-Path $tmpStderr) { Get-Content $tmpStderr.FullName -Raw } else { '' }

        Out-Line '### stdout (前 30 秒)'
        Out-Code -Lang '' -Text (Limit-Text $outText 250)
        Out-Line ''
        Out-Line '### stderr (前 30 秒)'
        Out-Code -Lang '' -Text (Limit-Text $errText 200)

        @($tmpStdout, $tmpStderr) | Where-Object { $_ } | Remove-Item -ErrorAction SilentlyContinue

        Out-Line ''
        Out-Line '**注意**:这步杀掉了 app 当前用的 chayuan-server 实例。重新打开 app 让它自动重启,或手动 `Start-Process $serverExe`。'
    }
} else {
    Out-Section '8. sidecar stdout 抓取(已跳过)'
    Out-Line '若 Step 6 的 status 都是 stopped/failed,建议重跑加 `-CaptureStdout` 看完整链路:'
    Out-Code -Lang 'powershell' -Text ".\diagnose-auto-start.ps1 -CaptureStdout"
}

# ───────── 落盘 + 输出 ─────────
$final = $sb.ToString()
Write-Host $final
if ($OutFile) {
    [System.IO.File]::WriteAllText($OutFile, $final, [System.Text.UTF8Encoding]::new($false))
    Write-Host ""
    Write-Host "[ok] 报告已写到:$OutFile"
}
