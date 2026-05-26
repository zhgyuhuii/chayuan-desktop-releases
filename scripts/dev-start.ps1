<#
.SYNOPSIS
  一键启动 chayuan-server 开发环境(Windows)。

.DESCRIPTION
  做什么:
    1. 检测 host 架构(x86_64 / ARM64)→ 用对的 vendor/services/<engine>/<platform>/
    2. 检查 Python(poetry / venv) + chayuan-server 已 editable install
    3. 检查 vendor binary 就位,缺时给指引
    4. 检查 CHAYUAN_ROOT 数据目录可写(默认 $env:USERPROFILE\.chayuan-dev)
    5. 起 chayuan start -a --single-machine

  ⚠ 编码:
    .ps1 文件**必须 UTF-8 BOM**,否则 PowerShell 5.x 按 ANSI/GBK 解,中文乱码。
    本脚本顶部已带 BOM(看不见的 EF BB BF)。

.PARAMETER Bg
  后台运行,PID 写到 %TEMP%\chayuan-dev.pid

.PARAMETER CheckOnly
  只跑 preflight,不启动 chayuan-server

.PARAMETER VendorPlatform
  强制 vendor 子目录(win-x64 / win-x64-noavx / win-x64-avx512 / win-arm64);
  不传 = 按 PROCESSOR_ARCHITECTURE 推

.PARAMETER ChayuanRoot
  数据目录;不传 = $env:USERPROFILE\.chayuan-dev

.EXAMPLE
  .\scripts\dev-start.ps1
  .\scripts\dev-start.ps1 -CheckOnly
  .\scripts\dev-start.ps1 -VendorPlatform win-x64-noavx
  .\scripts\dev-start.ps1 -Bg -ChayuanRoot D:\chayuan-data
#>
[CmdletBinding()]
param(
    [switch]$Bg,
    [switch]$CheckOnly,
    [string]$VendorPlatform = '',
    [string]$ChayuanRoot = '',
    [int]$Port = 62581
)

# ────────────────── 编码:终端 + Python 全程 UTF-8 ──────────────────
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
    chcp 65001 > $null 2>&1
} catch {}
$env:PYTHONIOENCODING = 'utf-8'
$env:LANG = 'en_US.UTF-8'

$ErrorActionPreference = 'Stop'
# PS 7+ 默认把 native exe 的非零退出码 + stderr 当 terminating error,这里关掉
# 让我们自己用 $LASTEXITCODE 检查
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

# ────────────────── 颜色辅助 ──────────────────
function Write-Ok    { param($m) Write-Host "✓ $m" -ForegroundColor Green }
function Write-Warn  { param($m) Write-Host "⚠ $m" -ForegroundColor Yellow }
function Write-Err   { param($m) Write-Host "✗ $m" -ForegroundColor Red }
function Write-Step  { param($m) Write-Host "→ $m" -ForegroundColor Cyan }

# ────────────────── 定位仓库 ──────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $ScriptDir
$Server = Join-Path $Repo 'chayuan-server'
Set-Location $Repo
Write-Step "工作目录:$Repo"

# ────────────────── 1. host 平台检测 ──────────────────
if (-not $VendorPlatform) {
    $cpuArch = ($env:PROCESSOR_ARCHITECTURE + '').ToUpperInvariant()
    if ($cpuArch -eq 'ARM64') {
        $VendorPlatform = 'win-arm64'
    } else {
        # AMD64 / EM64T → AVX2 默认
        $VendorPlatform = 'win-x64'
    }
}
Write-Ok "host: Windows $cpuArch → vendor 子目录 = $VendorPlatform"

# ────────────────── 2. Python + chayuan 包 ──────────────────
Write-Step 'Step 1/4: 检查 Python 环境'

# 实测 Python 3.13 上 chayuan-server multiprocessing 子 worker 100% SIGSEGV
# (C 扩展不兼容)。优先 3.12 / 3.11 / 3.10;3.13 退化为最后兜底并 warn。
#
# 返回 hashtable @{ ver = '3.12' / $null; err = '原因' }。
# 设计点:
#   1. 用 --version 不用 `-c '...'`,前者输出格式稳定 + 不触发 Microsoft Store
#      的 python3.exe stub 假阳性(Store stub 跑 `-c '...'` 返 exit 0 但 stdout 空)
#   2. PS 7+ 默认 PSNativeCommandErrorActionPreference=true,会把 stderr 当
#      terminating error 抛出 try/catch;这里临时关掉 + 用 *>&1 合并捕获
#   3. WindowsApps python.exe 大小通常 0,提前剔除
function Get-PyVersion($exePath) {
    if (-not (Test-Path $exePath)) { return @{ ver=$null; err='文件不存在' } }
    try {
        $fi = Get-Item $exePath -ErrorAction Stop
        # Microsoft Store stub:全是 0 字节 reparse alias
        if ($fi.Length -eq 0) { return @{ ver=$null; err='零字节 alias (Microsoft Store stub)' } }
    } catch {
        return @{ ver=$null; err="stat 失败: $_" }
    }

    # 用 cmd.exe 包一层规避 PS 7 NativeCommandErrorActionPreference 把 stderr
    # 当 fatal,以及解决 conda env python.exe 偶尔需要 cmd 子 shell DLL 解析
    # 路径的怪事。`2>&1` 把 stderr 合到 stdout 一起 capture。
    try {
        $out = & cmd.exe /c "`"$exePath`" --version 2>&1"
        $exit = $LASTEXITCODE
    } catch {
        return @{ ver=$null; err="cmd.exe 调用抛: $($_.Exception.Message)" }
    }
    $text = if ($out) { ($out | Out-String).Trim() } else { '' }
    if ($text -match 'Python\s+(\d+)\.(\d+)') {
        return @{ ver = "$($matches[1]).$($matches[2])"; err = $null }
    }
    # 没匹配:把 exit + 头 100 字符吐出来排错
    $snippet = if ($text.Length -gt 100) { $text.Substring(0,100) + '...' } else { $text }
    return @{ ver=$null; err="exit=$exit out=[$snippet]" }
}

$candidates = @()
if ($env:CHAYUAN_PYTHON) { $candidates += $env:CHAYUAN_PYTHON }

# 1) 用 conda 自身列出所有 env(最可靠;env 名 / 路径 / 是否在 USERPROFILE 都不依赖)
$condaExe = Get-Command conda -ErrorAction SilentlyContinue
if (-not $condaExe) {
    # 用户可能装了 conda 但没 PATH;扫常见路径
    foreach ($p in @(
        "$env:USERPROFILE\miniconda3\Scripts\conda.exe"
        "$env:USERPROFILE\Anaconda3\Scripts\conda.exe"
        "$env:USERPROFILE\miniforge3\Scripts\conda.exe"
        "$env:USERPROFILE\mambaforge\Scripts\conda.exe"
        "$env:LOCALAPPDATA\miniconda3\Scripts\conda.exe"
        "$env:LOCALAPPDATA\Anaconda3\Scripts\conda.exe"
        "C:\ProgramData\miniconda3\Scripts\conda.exe"
        "C:\ProgramData\Anaconda3\Scripts\conda.exe"
        'C:\Miniconda3\Scripts\conda.exe'
        'C:\Anaconda3\Scripts\conda.exe'
    )) {
        if (Test-Path $p) { $condaExe = $p; break }
    }
}
if ($condaExe) {
    try {
        $envListRaw = & $condaExe info --json 2>$null
        if ($LASTEXITCODE -eq 0 -and $envListRaw) {
            $envInfo = ($envListRaw -join "`n") | ConvertFrom-Json
            foreach ($envPath in @($envInfo.envs)) {
                $py = Join-Path $envPath 'python.exe'
                if (-not (Test-Path $py)) { $py = Join-Path $envPath 'bin\python.exe' }
                if (Test-Path $py) { $candidates += $py }
            }
            # base env 也算
            if ($envInfo.root_prefix) {
                $rootPy = Join-Path $envInfo.root_prefix 'python.exe'
                if (Test-Path $rootPy) { $candidates += $rootPy }
            }
        }
    } catch { }
}

# 2) 通用安装位置(以防 conda 没列全 / 用户用了别的发行版)
$candidates += @(
    "$env:USERPROFILE\miniconda3\envs\py312\python.exe"
    "$env:USERPROFILE\Anaconda3\envs\py312\python.exe"
    "$env:USERPROFILE\miniforge3\envs\py312\python.exe"
    "$env:USERPROFILE\mambaforge\envs\py312\python.exe"
    "$env:LOCALAPPDATA\miniconda3\envs\py312\python.exe"
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
)

# 3) PATH 搜索
foreach ($exe in @('python3.12.exe','python3.11.exe','python3.10.exe','python.exe','python3.exe')) {
    $cmd = Get-Command $exe -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
}

# 4) poetry venv
$poetryExe = Get-Command poetry -ErrorAction SilentlyContinue
if ($poetryExe) {
    Push-Location $Server
    try {
        $venvPath = & poetry env info --path 2>$null
        if ($LASTEXITCODE -eq 0 -and $venvPath) {
            $poetryPy = Join-Path $venvPath 'Scripts\python.exe'
            if (-not (Test-Path $poetryPy)) { $poetryPy = Join-Path $venvPath 'bin\python.exe' }
            if (Test-Path $poetryPy) { $candidates += $poetryPy }
        }
    } finally { Pop-Location }
}

# 去重
$candidates = $candidates | Where-Object { $_ } | Select-Object -Unique

$pythonBin = $null
$pythonVersion = $null
$fallback313 = $null
$probed = @()   # 记录探测过的 (path, 版本/err) 给失败时排错用
foreach ($cand in $candidates) {
    if (-not $cand) { continue }
    $result = Get-PyVersion $cand
    if (-not $result.ver) {
        $probed += "[$cand]  $($result.err)"
        continue
    }
    $probed += "[$cand]  Python $($result.ver)"
    if ($result.ver -in @('3.10','3.11','3.12')) {
        $pythonBin = $cand
        $pythonVersion = $result.ver
        break
    }
    if ($result.ver -eq '3.13' -and -not $fallback313) {
        $fallback313 = $cand
    }
}
if (-not $pythonBin -and $fallback313) {
    Write-Warn "只找到 Python 3.13 ($fallback313);chayuan-server 在 3.13 上"
    Write-Warn '  multiprocessing 子 worker 100% SIGSEGV(C 扩展不兼容)。装 3.12:'
    Write-Host '    winget install Python.Python.3.12' -ForegroundColor DarkGray
    Write-Host '    或:conda create -n py312 python=3.12 -y' -ForegroundColor DarkGray
    Write-Host '    然后:$env:CHAYUAN_PYTHON = "C:\path\to\python.exe"; .\scripts\dev-start.ps1' -ForegroundColor DarkGray
    $pythonBin = $fallback313
    $pythonVersion = '3.13(危险)'
}
if (-not $pythonBin) {
    Write-Err '找不到 Python 3.10/3.11/3.12;chayuan-server 不支持 3.13(C 扩展 SIGSEGV)'
    if ($probed.Count -gt 0) {
        Write-Host '    探测过(都不是 3.10/3.11/3.12):' -ForegroundColor DarkGray
        foreach ($p in ($probed | Select-Object -First 20)) {
            Write-Host "      $p" -ForegroundColor DarkGray
        }
    } else {
        Write-Host '    一个 Python 候选都没找到(conda + PATH 都空)' -ForegroundColor DarkGray
    }
    Write-Host ''
    Write-Host '  解法:' -ForegroundColor Yellow
    Write-Host '    1) 已装 conda 但 env 名字不是 py312 → 直接指定:' -ForegroundColor DarkGray
    Write-Host '       $env:CHAYUAN_PYTHON = "$(conda env list | findstr 3.12 | %% { ($_ -split ''\s+'')[-1] })\python.exe"' -ForegroundColor DarkGray
    Write-Host '       或手动:$env:CHAYUAN_PYTHON = "C:\Users\<you>\miniconda3\envs\<envname>\python.exe"' -ForegroundColor DarkGray
    Write-Host '    2) 没装 3.12:' -ForegroundColor DarkGray
    Write-Host '       winget install Python.Python.3.12' -ForegroundColor DarkGray
    Write-Host '       或 conda create -n py312 python=3.12 -y' -ForegroundColor DarkGray
    exit 1
}

# 验证 import chayuan(pip install -e 或 PYTHONPATH 都可)
$libRoot = Join-Path $Server 'libs\chayuan-server'
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$libRoot;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $libRoot
}
& $pythonBin -c 'import chayuan' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "$pythonBin 但 import chayuan 失败,要先装运行依赖"
    Write-Host "    cd chayuan-server; $pythonBin -m pip install -e libs\chayuan-server" -ForegroundColor DarkGray
    if (-not $CheckOnly) { exit 2 }
} else {
    Write-Ok "Python: $pythonBin (Python $pythonVersion,可 import chayuan)"
}

# ────────────────── 3. vendor binary ──────────────────
Write-Step 'Step 2/4: 检查 vendor 二进制'
$llamaDir   = Join-Path $Server "vendor\services\llama-server\$VendorPlatform"
$whisperDir = Join-Path $Server "vendor\services\whisper-server\$VendorPlatform"

$llamaExe = Join-Path $llamaDir 'llama-server.exe'
if (Test-Path $llamaExe) {
    Write-Ok "llama-server: $llamaExe"
} else {
    Write-Warn "llama-server.exe 不在 $llamaDir\"
    Write-Host "    解法:.\scripts\install-llama-server.ps1 -Target $VendorPlatform" -ForegroundColor DarkGray
    if (-not $CheckOnly) { exit 2 }
}

$whisperExe = Join-Path $whisperDir 'whisper-server.exe'
if (Test-Path $whisperExe) {
    Write-Ok "whisper-server: $whisperExe"
} else {
    Write-Warn "whisper-server.exe 不在 $whisperDir\(ASR 会 fallback Python faster-whisper)"
    Write-Host '    解法:.\scripts\install-whisper-server.ps1' -ForegroundColor DarkGray
}

# ────────────────── 4. CHAYUAN_ROOT ──────────────────
Write-Step 'Step 3/4: CHAYUAN_ROOT 数据目录'
if (-not $ChayuanRoot) {
    if ($env:CHAYUAN_ROOT) {
        $ChayuanRoot = $env:CHAYUAN_ROOT
    } else {
        $ChayuanRoot = Join-Path $env:USERPROFILE '.chayuan-dev'
    }
}
if (-not (Test-Path $ChayuanRoot)) {
    New-Item -ItemType Directory -Force -Path $ChayuanRoot | Out-Null
}
$env:CHAYUAN_ROOT = $ChayuanRoot
$env:CHAYUAN_VENDOR_PLATFORM = $VendorPlatform
Write-Ok "CHAYUAN_ROOT=$ChayuanRoot  (env CHAYUAN_VENDOR_PLATFORM=$VendorPlatform)"

# 首次需 chayuan init -q
$baseYaml = Join-Path $ChayuanRoot 'basic_settings.yaml'
if (-not (Test-Path $baseYaml)) {
    Write-Step '首次启动:跑 chayuan init -q 初始化数据目录'
    Push-Location $Server
    try {
        if ($poetryExe) {
            & poetry run python -m chayuan init -q --profile local
        } else {
            & $pythonBin -m chayuan init -q --profile local
        }
    } finally { Pop-Location }
    Write-Ok "基础 yaml 已生成 → $ChayuanRoot\"
}

if ($CheckOnly) {
    Write-Ok 'preflight 全部通过 (-CheckOnly)'
    exit 0
}

# 从 chayuan_root/basic_settings.yaml 读真实 API 端口(优先 -Port 显式)
if ($Port -eq 62581 -and (Test-Path $baseYaml)) {
    $yamlPort = & $pythonBin -c "
import yaml
try:
    with open(r'$baseYaml') as f: d = yaml.safe_load(f) or {}
    p = ((d.get('API_SERVER') or {}).get('public_port')
         or (d.get('API_SERVER') or {}).get('port')
         or (d.get('VENDOR_PREFERRED_PORTS') or {}).get('api'))
    if isinstance(p, int): print(p)
except Exception: pass
" 2>$null
    if ($yamlPort -and ([int]$yamlPort) -ne 62581) {
        $Port = [int]$yamlPort
        Write-Ok "从 basic_settings.yaml 读到 API 端口: $Port"
    }
}

# ────────────────── 5. 启 chayuan-server ──────────────────
Write-Step "Step 4/4: 启 chayuan-server (port=$Port)"
# PowerShell Start-Process 禁止 stdout/stderr 重定向到同一文件,拆成两个
$logFile    = Join-Path $env:TEMP 'chayuan-dev.log'      # stdout
$errLogFile = Join-Path $env:TEMP 'chayuan-dev.err.log'  # stderr
$pidFile    = Join-Path $env:TEMP 'chayuan-dev.pid'

# 清理已有进程(用 taskkill /T 把整个进程树杀掉,避免子进程占着 8509 配置面板
# 端口或 62582+ sidecar 端口,导致新 server 起来 preflight 失败)
if (Test-Path $pidFile) {
    $oldPid = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Warn "已有 dev sidecar pid=$oldPid,先停掉(整棵进程树)"
        & taskkill /F /T /PID $oldPid 2>$null | Out-Null
        Start-Sleep -Seconds 1
    }
}

# 兜底:即使 pid 文件丢了,也按端口反查残留 server 主进程或 sidecar 孤儿
foreach ($p in @($Port, 8509, 62582, 62583, 62584, 62585, 62586)) {
    try {
        $line = (netstat -ano | Select-String -Pattern ":$p\s+.*LISTENING" | Select-Object -First 1).ToString()
        if ($line -match '\s(\d+)\s*$') {
            $orphan = [int]$matches[1]
            & taskkill /F /T /PID $orphan 2>$null | Out-Null
            Write-Warn "清理孤儿 pid=$orphan (占着 :$p)"
        }
    } catch {}
}
Start-Sleep -Milliseconds 500

Set-Location $Server
if ($poetryExe -and $venvPath) {
    $cmdExe = 'poetry'
    $cmdArgs = @('run', 'python', '-m', 'chayuan', 'start', '-a', '--single-machine')
} else {
    $cmdExe = $pythonBin
    $cmdArgs = @('-m', 'chayuan', 'start', '-a', '--single-machine')
}

if ($Bg) {
    $p = Start-Process -FilePath $cmdExe -ArgumentList $cmdArgs -RedirectStandardOutput $logFile -RedirectStandardError $errLogFile -PassThru -WindowStyle Hidden
    $p.Id | Set-Content $pidFile
    Write-Ok "spawned pid=$($p.Id),stdout=$logFile  stderr=$errLogFile"

    # health probe:60s 内每 2s curl /healthz,任一成功就退;期间检测进程还活着
    Write-Step "等 health(最多 60s):http://127.0.0.1:$Port/healthz"
    $deadline = (Get-Date).AddSeconds(60)
    $healthy = $false
    while ((Get-Date) -lt $deadline) {
        if ($p.HasExited) {
            Write-Err "chayuan-server 已退出(pid=$($p.Id), exitcode=$($p.ExitCode)),启动失败"
            Write-Host '    最后 20 行 stdout:' -ForegroundColor DarkGray
            if (Test-Path $logFile) {
                Get-Content $logFile -Tail 20 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
            }
            Write-Host '    最后 20 行 stderr:' -ForegroundColor DarkGray
            if (Test-Path $errLogFile) {
                Get-Content $errLogFile -Tail 20 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
            }
            Remove-Item $pidFile -ErrorAction SilentlyContinue
            exit 3
        }
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $healthy = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }

    if ($healthy) {
        Write-Ok "ready ✓ (pid=$($p.Id) listening on :$Port)"
        Write-Host "    Get-Content -Wait $logFile   # 看 stdout"
        Write-Host "    Get-Content -Wait $errLogFile   # 看 stderr"
        Write-Host "    Invoke-RestMethod http://127.0.0.1:$Port/healthz   # 再探活"
        Write-Host "    Stop-Process -Id (Get-Content $pidFile)   # 关掉"
    } else {
        Write-Err "60s 内 /healthz 没返 200,server 可能卡在启动中"
        Write-Host "    pid=$($p.Id) 还活着,看日志:Get-Content -Wait $logFile" -ForegroundColor DarkGray
        Write-Host "    错误日志:Get-Content -Wait $errLogFile" -ForegroundColor DarkGray
        Write-Host "    要强制关:Stop-Process -Id $($p.Id) -Force" -ForegroundColor DarkGray
        exit 4
    }
} else {
    Write-Ok '前台启动(Ctrl+C 退出)'
    & $cmdExe @cmdArgs
}
