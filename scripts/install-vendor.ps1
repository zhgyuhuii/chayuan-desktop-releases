#requires -Version 5.1
<#
.SYNOPSIS
    一站式装齐 chayuan-server/vendor/ 下的所有打包资源 — 模型、服务 binary、torch wheels。

.DESCRIPTION
    打包前的 prerequisite。按 -Flavor(lite / full)+ -Components 决定要装哪些:

      模型:scripts\install-bundled-models.py (--lite / 全量)
          → 装到 vendor\bundled_models\<cap>\<repo>\
          LITE_CAPS  = embedding / rerank / asr / ocr / tts (~1.4 GB)
          FULL_CAPS  = LITE_CAPS + chat / image (~3.5 GB)

      服务 binary:install-llama-server.ps1 + install-whisper-server.ps1
          → 装到 vendor\services\<engine>\<platform>\<exe>
          (lite/full 都要,没 launcher binary sidecar 起不来)

      torch wheels:chayuan-server\packaging\preflight_torch.py
          → 装到 vendor\torch_wheels\<platform_py_var>\
          (chayuan-server.exe 启动时离线 pip install torch + torchvision 兜底)

    所有子步骤都幂等:已下载过的会跳过,失败的局部重试。

.PARAMETER Flavor
    模型范围:lite(default,~1.4 GB)或 full(~3.5 GB,含 chat 主力 + image)。

.PARAMETER Components
    要装哪些组件,逗号分隔。默认 'models,services,torch'。
    可选:models / services / torch / all / none。
    例:仅装 services 用 ``-Components services``。

.PARAMETER Source
    模型下载源,透传给 install-bundled-models.py:
      auto(default)= HF (hf-mirror) 优先 → 失败降级 ModelScope
      hf           = 强制 HF
      modelscope   = MS 优先(已知 hf-mirror 卡 Xet 时切这个)

.PARAMETER LlamaVersion / WhisperVersion
    指定 vendor binary 版本,透传给对应 install-*.ps1。默认值同子脚本。

.PARAMETER Targets
    llama-server 要下哪些平台,逗号分隔。默认
    'win-x64,win-arm64,linux-x64,macos-x64,macos-arm64' —— 这 5 个都是
    GitHub release zip,能从 Windows 一台机器全下齐。
    linux-arm64 upstream 没发 zip,不在默认里(需 ARM Linux 上自己处理)。
    whisper-server 不受此参数影响:它没有预编译,只能装本机(Windows)平台,
    其它平台需到对应机器跑 install-whisper-server.sh。

.PARAMETER GithubMirror
    GitHub 镜像前缀。墙内 github.com 不通时用 — services(llama-server /
    whisper-server)的二进制只在 GitHub release 发,没别的源。
    传 -GithubMirror 'https://gh-proxy.com/' 后,下载 URL 会拼成
    https://gh-proxy.com/https://github.com/...。会 export 成 GITHUB_MIRROR
    环境变量,子脚本自动识别。
    **不传也行**:脚本检测到 github.com 不通时会自动从内置清单
    (gh-proxy.com / ghfast.top,2026-05 实测可用)探一个能用的。
    只在自动探测也失败、或想强制用某个镜像时才需手动传。

.PARAMETER SkipNetTest
    Switch:跳过开头的网络可达性检测。CI / 已知联网 OK 时用。

.PARAMETER DryRun
    Switch:只打印将要执行的命令,不真跑。验证逻辑用。

.EXAMPLE
    # 装齐 lite 打包所需(模型 lite 子集 + services + torch CPU)
    .\install-vendor.ps1

.EXAMPLE
    # full 装齐(chat 大模型 + image-embedding 全套)
    .\install-vendor.ps1 -Flavor full

.EXAMPLE
    # 墙内:github.com 不通时脚本自动选可用镜像,直接跑即可
    .\install-vendor.ps1 -Flavor full
    # 想强制指定镜像
    .\install-vendor.ps1 -Flavor full -GithubMirror https://gh-proxy.com/

.EXAMPLE
    # 只补 tts(piper)
    .\install-vendor.ps1 -Components models  # 之后跑 install-bundled-models.py --only tts

.EXAMPLE
    # MS 优先(内网 hf-mirror 不通时)
    .\install-vendor.ps1 -Source modelscope

.EXAMPLE
    # 装 services + torch wheels(模型已经装过)
    .\install-vendor.ps1 -Components services,torch
#>
[CmdletBinding()]
param(
    [ValidateSet('lite','full')]
    [string]$Flavor = 'lite',

    [string]$Components = 'models,services,torch',

    [ValidateSet('auto','hf','modelscope')]
    [string]$Source = 'auto',

    [string]$LlamaVersion   = '',
    [string]$WhisperVersion = '',

    # llama-server 要下哪些平台,逗号分隔。默认把 5 个能从 Windows 跨平台
    # 下的全下(linux-arm64 upstream 没 zip,不在默认里)。
    [string]$Targets = 'win-x64,win-arm64,linux-x64,macos-x64,macos-arm64',

    # GitHub 镜像:墙内 github.com 不通时用。传 -GithubMirror 不带值默认
    # https://ghproxy.com/;也可显式给别的代理。会 export 成 GITHUB_MIRROR
    # 环境变量,install-llama-server.ps1 / install-whisper-server.sh 自动识别。
    # 不传则读已有的 $env:GITHUB_MIRROR;都没有就直连 github.com。
    [string]$GithubMirror = '',

    [switch]$SkipNetTest,
    [switch]$DryRun,
    # 默认并行启动 models / llama-server / whisper-server / torch 4 个阶段
    # (各自独立网络 + 写不同 vendor 子目录,无共享状态);加这个 switch 走老
    # 串行模式,便于 debug 卡哪一步。
    [switch]$Sequential
)

$ErrorActionPreference = 'Stop'

# ───────── 路径 ─────────
$here          = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $here
$ServerDir     = Join-Path $WorkspaceRoot 'chayuan-server'
$VendorDir     = Join-Path $ServerDir 'vendor'

function Write-Step  { param([string]$Msg) Write-Host ''; Write-Host ('=== ' + $Msg + ' ===') -ForegroundColor Cyan }
function Write-Note  { param([string]$Msg) Write-Host ('  ' + $Msg) -ForegroundColor DarkCyan }
function Write-Ok    { param([string]$Msg) Write-Host ('  [OK] ' + $Msg) -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host ('  [WARN] ' + $Msg) -ForegroundColor Yellow }
function Write-Fail  { param([string]$Msg) Write-Host ('  [FAIL] ' + $Msg) -ForegroundColor Red }

Write-Host ('install-vendor.ps1 — Flavor={0}  Components={1}  Source={2}' -f $Flavor, $Components, $Source)
Write-Host ('  Workspace: ' + $WorkspaceRoot)
Write-Host ('  Vendor:    ' + $VendorDir)

# ───────── GitHub 镜像 ─────────
# 优先级:-GithubMirror 显式 > 已有的 $env:GITHUB_MIRROR > 不用镜像。
# -GithubMirror 不带值(传了但空)时,PowerShell 会把它当 ''。这里约定:
# 想用默认 ghproxy 就显式 -GithubMirror 'https://ghproxy.com/'。
if ($GithubMirror) {
    $env:GITHUB_MIRROR = $GithubMirror
}
$effectiveMirror = $env:GITHUB_MIRROR
if ($effectiveMirror) {
    Write-Host ('  GitHubMirror: ' + $effectiveMirror + '  (services 下载走镜像)') -ForegroundColor DarkCyan
}

# 解析 -Components
$wantedComponents = @($Components -split ',' | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
if ($wantedComponents -contains 'all')  { $wantedComponents = @('models','services','torch') }
if ($wantedComponents -contains 'none') { $wantedComponents = @() }
$doModels   = $wantedComponents -contains 'models'
$doServices = $wantedComponents -contains 'services'
$doTorch    = $wantedComponents -contains 'torch'

if (-not ($doModels -or $doServices -or $doTorch)) {
    Write-Warn '-Components 没启用任何阶段,直接退出'
    exit 0
}

# ───────── 0. 网络可达性 ─────────
function Test-Net {
    param([string]$Url, [int]$TimeoutSec = 5)
    try {
        $resp = Invoke-WebRequest -Uri $Url -Method Head -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        return $true
    } catch [System.Net.WebException] {
        # 4xx/5xx 也算"网络通了,只是 HEAD 不支持"
        $code = $null
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        if ($code -and $code -ge 400 -and $code -lt 600) { return $true }
        return $false
    } catch { return $false }
}

# GitHub 镜像自动探测:services 要从 GitHub release 下二进制,但 github.com
# 常被墙。优先级:-GithubMirror / $env:GITHUB_MIRROR(显式)> github.com 直连
# > 自动探内置镜像清单。内置清单是 2026-05 实测可下的(ghproxy.com 已废弃)。
$AUTO_GH_MIRRORS = @('https://gh-proxy.com/', 'https://ghfast.top/')
function Resolve-AutoMirror {
    if ($effectiveMirror) { return $effectiveMirror }       # 已显式配置
    if (Test-Net 'https://github.com/' 6) { return '' }      # 直连通,不用镜像
    Write-Warn 'github.com 直连不可达,自动探测可用 GitHub 镜像...'
    foreach ($cand in $AUTO_GH_MIRRORS) {
        if (Test-Net $cand 8) {
            Write-Ok ('自动选用 GitHub 镜像: ' + $cand)
            return $cand
        }
    }
    Write-Warn ('内置镜像都不可达(' + ($AUTO_GH_MIRRORS -join ', ') + ')')
    return ''
}

if (-not $SkipNetTest) {
    Write-Step '0. 网络可达性检测'

    # services 阶段:先解析(可能自动选)GitHub 镜像,再据此构造探测列表
    if ($doServices -and -not $effectiveMirror) {
        $auto = Resolve-AutoMirror
        if ($auto) {
            $effectiveMirror = $auto
            $env:GITHUB_MIRROR = $auto   # export 给 install-llama-server.ps1 等子脚本
        }
    }

    # 按"哪个 component 用哪个域名"列表
    $hosts = New-Object System.Collections.Generic.List[hashtable]
    if ($doModels) {
        $hosts.Add(@{ Url='https://hf-mirror.com/';     Use='models (HF 镜像 — 主路径)';     Critical=$true })
        $hosts.Add(@{ Url='https://huggingface.co/';     Use='models (HF 直连 — 海外兜底)';    Critical=$false })
        $hosts.Add(@{ Url='https://modelscope.cn/';      Use='models (ModelScope — 内网兜底)'; Critical=$false })
    }
    if ($doServices) {
        if ($effectiveMirror) {
            # 配了镜像 — 探镜像本身;github.com 直连降级为非关键(经镜像绕过)
            $hosts.Add(@{ Url=$effectiveMirror;                         Use='services (GitHub 镜像 — 主路径)';        Critical=$true })
            $hosts.Add(@{ Url='https://github.com/';                    Use='services (GitHub 直连 — 已配镜像,非关键)'; Critical=$false })
        } else {
            $hosts.Add(@{ Url='https://github.com/';                    Use='services (GitHub release API)';         Critical=$true })
        }
        $hosts.Add(@{ Url='https://objects.githubusercontent.com/';    Use='services (GitHub asset CDN)';     Critical=$false })
    }
    if ($doTorch) {
        $hosts.Add(@{ Url='https://download.pytorch.org/';   Use='torch wheels (PyTorch 官方索引)'; Critical=$true })
    }

    $netOk = $true
    foreach ($h in $hosts) {
        $ok = Test-Net -Url $h.Url -TimeoutSec 8
        if ($ok) {
            Write-Ok ('{0,-45} ← {1}' -f $h.Url, $h.Use)
        } else {
            if ($h.Critical) {
                Write-Fail ('{0,-45} ← {1} (CRITICAL)' -f $h.Url, $h.Use)
                $netOk = $false
            } else {
                Write-Warn ('{0,-45} ← {1} (备用,不致命)' -f $h.Url, $h.Use)
            }
        }
    }
    if (-not $netOk) {
        Write-Host ''
        Write-Host '[FAIL] 关键域名不可达,继续装会卡在下载阶段。处理:' -ForegroundColor Red
        Write-Host '  · 检查公司代理 / VPN 是否生效'                       -ForegroundColor DarkYellow
        Write-Host '  · 若只是 hf-mirror 不通,加 -Source modelscope 走 MS' -ForegroundColor DarkYellow
        Write-Host '  · github.com 不通且自动镜像也失败 → 手动指定可用镜像:' -ForegroundColor DarkYellow
        Write-Host '      -GithubMirror https://gh-proxy.com/   (或 https://ghfast.top/)' -ForegroundColor DarkYellow
        Write-Host '  · 内网完全无外网 → 拿外网机器跑此脚本,把 vendor\ 整目录拷过来' -ForegroundColor DarkYellow
        Write-Host '  · 想强行继续(自担风险),加 -SkipNetTest'              -ForegroundColor DarkYellow
        exit 2
    }
}

# ───────── 1. Python 解释器 ─────────
# PS 5.1 没 ?? 空值合并;显式 if 兼容
$pyExe = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyExe) { $pyExe = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $pyExe -and ($doModels -or $doTorch)) {
    Write-Fail 'PATH 找不到 python / python3 — models / torch 阶段需要。先装 Python 3.10+ 再重跑。'
    exit 3
}

# ───────── 2. 收集要跑的 stage spec ─────────
# 每条 spec = @{ Stage='...'; Cmd='...'; Args=@(...); Cwd='...' }
# 4 个阶段写不同的 vendor 子目录,无共享状态 → 默认并行;-Sequential 走老串行。
$stageSpecs = New-Object System.Collections.Generic.List[hashtable]

if ($doModels) {
    # 默认带 --clean-stale:打包前清掉 cap_dir 里非 canonical 的旧子目录
    # (例:embedding/gte-multilingual-base 是 HF transformers,canonical 已换成
    # bge-m3 GGUF,留着会被 build.py 误打进 installer + 运行时被 llama-server 拒绝)。
    # canonical 名单真源:chayuan-server/packaging/bundle_manifest.py CANONICAL_MODEL_SUBDIRS。
    $modelArgs = @((Join-Path $here 'install-bundled-models.py'),
                   '--source', $Source, '--clean-stale')
    if ($Flavor -eq 'lite') { $modelArgs += '--lite' }
    $stageSpecs.Add(@{
        Stage = 'models'
        Cmd   = $pyExe.Source
        Args  = $modelArgs
        Cwd   = $WorkspaceRoot
    })
}

if ($doServices) {
    $llamaPs1   = Join-Path $here 'install-llama-server.ps1'
    $whisperPs1 = Join-Path $here 'install-whisper-server.ps1'

    if (Test-Path $llamaPs1) {
        # 按 -Targets 逐个平台下 llama-server。llama.cpp release 的 win/linux/
        # macos 二进制都是 GitHub zip,可从 Windows 跨平台下;每个平台一个 stage。
        $targetList = @($Targets -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        foreach ($tgt in $targetList) {
            $llArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $llamaPs1, '-Target', $tgt)
            if ($LlamaVersion) { $llArgs += @('-Version', $LlamaVersion) }
            $stageSpecs.Add(@{
                Stage = "llama-$tgt"
                Cmd   = 'powershell.exe'
                Args  = $llArgs
                Cwd   = $WorkspaceRoot
            })
        }
    } else {
        Write-Fail "install-llama-server.ps1 不存在: $llamaPs1"
    }

    # whisper-server:upstream 不发预编译,install-whisper-server.ps1 只能装
    # 本机(Windows)平台。其它平台的 whisper-server 需到对应机器上跑
    # install-whisper-server.sh(docker/brew/源码),无法从 Windows 跨平台下。
    if (Test-Path $whisperPs1) {
        $wsArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $whisperPs1)
        if ($WhisperVersion) { $wsArgs += @('-Version', $WhisperVersion) }
        $stageSpecs.Add(@{
            Stage = 'whisper-server(win-host)'
            Cmd   = 'powershell.exe'
            Args  = $wsArgs
            Cwd   = $WorkspaceRoot
        })
    } else {
        Write-Fail "install-whisper-server.ps1 不存在: $whisperPs1"
    }
}

if ($doTorch) {
    $preflight = Join-Path $ServerDir 'packaging\preflight_torch.py'
    if (-not (Test-Path $preflight)) {
        Write-Fail "preflight_torch.py 不存在: $preflight"
    } else {
        # poetry run 优先(确保跟 PyInstaller spec 用同一个 Python ABI)。
        # 并行模式下 poetry 起动多了 ~3s,跟模型下载比可忽略。
        $poetryExe = Get-Command poetry -ErrorAction SilentlyContinue
        if ($poetryExe) {
            $stageSpecs.Add(@{
                Stage = 'torch'
                Cmd   = 'poetry'
                Args  = @('run', 'python', 'packaging\preflight_torch.py', '--variants', 'cpu', '--py-versions', '312')
                Cwd   = $ServerDir
            })
        } else {
            $stageSpecs.Add(@{
                Stage = 'torch'
                Cmd   = $pyExe.Source
                Args  = @($preflight, '--variants', 'cpu', '--py-versions', '312')
                Cwd   = $WorkspaceRoot
            })
        }
    }
}

# ───────── 3. 执行 stage(串行 / 并行) ─────────
$results = New-Object System.Collections.Generic.List[hashtable]

function Invoke-Stage-Sequential {
    param([hashtable]$Spec)
    Write-Step ('运行 ' + $Spec.Stage)
    Write-Host ('  $ ' + $Spec.Cmd + ' ' + ($Spec.Args -join ' ')) -ForegroundColor DarkGray
    if ($DryRun) {
        Write-Warn ('[DRY-RUN] 跳过 ' + $Spec.Stage)
        return $true
    }
    $cwd0 = Get-Location
    Set-Location $Spec.Cwd
    try {
        & $Spec.Cmd @($Spec.Args)
        $rc = $LASTEXITCODE
    } finally {
        Set-Location $cwd0
    }
    return ($rc -eq 0)
}

function Invoke-Stages-Parallel {
    param([hashtable[]]$Specs)

    # 各 stage 独立 log file,Start-Process 写入,主循环 tail 出进度面板。
    $logRoot = Join-Path $env:TEMP ('chayuan-install-vendor-' + (Get-Date -Format 'HHmmss') + '-' + ([guid]::NewGuid().ToString('N').Substring(0,6)))
    New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
    Write-Note ('并行 log 目录: ' + $logRoot + ' (出错时 tail 这里看完整 stdout)')

    $running = @()
    foreach ($s in $Specs) {
        $log = Join-Path $logRoot ($s.Stage + '.log')
        $err = Join-Path $logRoot ($s.Stage + '.err')
        # 先 touch 空文件,后面 tail 才不会报"路径不存在"
        '' | Out-File -FilePath $log -Encoding UTF8
        '' | Out-File -FilePath $err -Encoding UTF8
        if ($DryRun) {
            Write-Warn ('[DRY-RUN] 跳过 spawn ' + $s.Stage + ' ($' + $s.Cmd + ' ' + ($s.Args -join ' ') + ')')
            $running += @{ Stage=$s.Stage; Proc=$null; Log=$log; Err=$err; Start=Get-Date; DryRun=$true }
            continue
        }
        $proc = Start-Process -FilePath $s.Cmd `
                              -ArgumentList $s.Args `
                              -WorkingDirectory $s.Cwd `
                              -RedirectStandardOutput $log `
                              -RedirectStandardError  $err `
                              -PassThru `
                              -NoNewWindow
        $running += @{ Stage=$s.Stage; Proc=$proc; Log=$log; Err=$err; Start=Get-Date; DryRun=$false }
        Write-Host ('  [spawn] {0,-16} pid={1,-6}  log={2}' -f $s.Stage, $proc.Id, $log) -ForegroundColor Cyan
    }

    if ($DryRun) {
        foreach ($r in $running) { $results.Add(@{ Stage=$r.Stage; Ok=$true; DurationSec=0 }) }
        return
    }

    # 进度面板:每 5 秒 poll 一次,打一行紧凑 status
    $startAll = Get-Date
    $finished = @{}
    while ($finished.Count -lt $running.Count) {
        Start-Sleep -Seconds 5
        $elapsed = [int]((Get-Date) - $startAll).TotalSeconds
        $parts = @()
        foreach ($r in $running) {
            if ($finished.ContainsKey($r.Stage)) {
                $mark = if ($finished[$r.Stage].Ok) { 'OK' } else { 'FAIL' }
                $parts += ('{0}:{1}({2}s)' -f $r.Stage, $mark, $finished[$r.Stage].DurationSec)
                continue
            }
            if ($r.Proc.HasExited) {
                $dur = [int]((Get-Date) - $r.Start).TotalSeconds
                $ok = ($r.Proc.ExitCode -eq 0)
                $finished[$r.Stage] = @{ Ok=$ok; DurationSec=$dur; ExitCode=$r.Proc.ExitCode }
                $mark = if ($ok) { 'OK' } else { 'FAIL' }
                $color = if ($ok) { 'Green' } else { 'Red' }
                Write-Host ('  [done] {0,-16} exit={1} in {2}s' -f $r.Stage, $r.Proc.ExitCode, $dur) -ForegroundColor $color
                $parts += ('{0}:{1}({2}s)' -f $r.Stage, $mark, $dur)
            } else {
                # 跑着呢 — 读 log 最后一行做进度提示(截 ≤ 24 字符)
                $tail = ''
                try {
                    $last = Get-Content -LiteralPath $r.Log -Tail 1 -ErrorAction SilentlyContinue
                    if ($last) {
                        $tail = ($last -replace '\s+', ' ').Trim()
                        if ($tail.Length -gt 24) { $tail = $tail.Substring(0, 21) + '...' }
                    }
                } catch {}
                if ($tail) {
                    $parts += ('{0}:[{1}]' -f $r.Stage, $tail)
                } else {
                    $parts += ('{0}:running' -f $r.Stage)
                }
            }
        }
        Write-Host (('  [T+{0,4}s] ' -f $elapsed) + ($parts -join '   '))
    }

    # 把每条 stage 的 log 摘要(末 30 行)dump 出来,失败的全文 dump
    Write-Host ''
    foreach ($r in $running) {
        $info = $finished[$r.Stage]
        $headerColor = if ($info.Ok) { 'Cyan' } else { 'Red' }
        Write-Host ('━━━ ' + $r.Stage + ' log (exit=' + $info.ExitCode + ', ' + $info.DurationSec + 's) ━━━') -ForegroundColor $headerColor
        $tailN = if ($info.Ok) { 30 } else { 200 }
        try {
            $out = Get-Content -LiteralPath $r.Log -Tail $tailN -ErrorAction SilentlyContinue
            if ($out) { $out | ForEach-Object { Write-Host ('  ' + $_) } }
        } catch {}
        try {
            $errs = Get-Content -LiteralPath $r.Err -Tail $tailN -ErrorAction SilentlyContinue
            if ($errs) {
                Write-Host '  --- stderr ---' -ForegroundColor Yellow
                $errs | ForEach-Object { Write-Host ('  ' + $_) -ForegroundColor Yellow }
            }
        } catch {}
        $results.Add(@{ Stage=$r.Stage; Ok=$info.Ok; DurationSec=$info.DurationSec })
    }
}

if ($stageSpecs.Count -eq 0) {
    Write-Warn '没有 stage 要跑'
} elseif ($Sequential) {
    Write-Step ('串行模式:运行 ' + $stageSpecs.Count + ' 个 stage')
    $idx = 0
    foreach ($s in $stageSpecs) {
        $idx++
        Write-Host ('--- [' + $idx + '/' + $stageSpecs.Count + '] ' + $s.Stage + ' ---') -ForegroundColor Cyan
        $t0 = Get-Date
        $ok = Invoke-Stage-Sequential -Spec $s
        $dur = [int]((Get-Date) - $t0).TotalSeconds
        $results.Add(@{ Stage=$s.Stage; Ok=$ok; DurationSec=$dur })
    }
} else {
    Write-Step ('并行启动 ' + $stageSpecs.Count + ' 个 stage(每 5 秒打印一次状态;失败时 dump 末 200 行 log)')
    Invoke-Stages-Parallel -Specs $stageSpecs
}

# ───────── 5. 最终体检 ─────────
Write-Step '4. 最终体检 (vendor/ 完整性)'
$checkM = Join-Path $here 'check-bundled-models.py'
$checkS = Join-Path $here 'check-services.py'
$arch = $env:PROCESSOR_ARCHITECTURE
if ($arch -eq 'ARM64') { $hostVendorPlat = 'win-arm64' } else { $hostVendorPlat = 'win-x64' }

if ($pyExe -and (Test-Path $checkM)) {
    & $pyExe.Source $checkM
}
if ($pyExe -and (Test-Path $checkS)) {
    & $pyExe.Source $checkS --target $hostVendorPlat
}

# ───────── 6. 总结 ─────────
Write-Step '总结'
$anyFail = $false
$totalSec = 0
foreach ($r in $results) {
    $dur = if ($r.ContainsKey('DurationSec')) { $r.DurationSec } else { 0 }
    if ($r.Ok) {
        Write-Ok ('{0,-16} OK     {1,5}s' -f $r.Stage, $dur)
    } else {
        Write-Fail ('{0,-16} FAILED {1,5}s' -f $r.Stage, $dur)
        $anyFail = $true
    }
    if (-not $Sequential -and $dur -gt $totalSec) { $totalSec = $dur }   # 并行:取最长
    elseif ($Sequential) { $totalSec += $dur }                            # 串行:累加
}
if ($results.Count -gt 0) {
    $mode = if ($Sequential) { '串行' } else { '并行' }
    Write-Host ('  墙钟耗时(' + $mode + '): ' + $totalSec + 's') -ForegroundColor DarkCyan
}

if ($anyFail) {
    Write-Host ''
    Write-Host '[FAIL] 至少一个阶段失败。常见处理:' -ForegroundColor Red
    Write-Host '  models 失败  → 加 -Source modelscope 切镜像;或在外网机器跑 install-bundled-models.py 拷 vendor\bundled_models\ 过来' -ForegroundColor DarkYellow
    Write-Host '  services 失败 → 看子脚本 stdout (GitHub release 限速 / 网线);或外网机器跑 install-llama-server.ps1 拷 vendor\services\ 过来' -ForegroundColor DarkYellow
    Write-Host '  torch 失败   → 加 --skip-torch-preflight 跳过整个 torch wheel preflight(代价:装机用户得在线装 torch)' -ForegroundColor DarkYellow
    exit 1
}

Write-Host ''
Write-Host '[OK] vendor/ 完整,可以跑 .\build-desktop.ps1 -LiteOnly' -ForegroundColor Green
exit 0
