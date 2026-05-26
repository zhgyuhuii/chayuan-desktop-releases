<#
.SYNOPSIS
  体检 Windows 装大型 MSI 的环境前置条件,排查 1310 / 110 / 1603 类失败。

.DESCRIPTION
  覆盖:
    1. Windows 版本 / 架构 / Windows Installer 引擎版本
    2. C: 盘空闲空间 (装 3.5 GB MSI 需 ≥ 8 GB free)
    3. %TEMP% 路径 + 空间
    4. C:\Windows\Installer\ 目录权限 / 当前用户能否写
    5. msiserver 服务状态(必须 Running)
    6. Windows Defender 实时保护是否开
    7. 系统里安装的所有 AV/EDR(WMI AntiVirusProduct + 进程扫描)
    8. UAC 等级

  输出同时打印控制台 + 落到 dist-integrated\diagnose-env-output.txt。
#>
[CmdletBinding()]
param()

try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    chcp 65001 > $null 2>&1
} catch {}

$ErrorActionPreference = 'Continue'
$WarningPreference = 'SilentlyContinue'

$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$outDir = Join-Path $WorkspaceRoot 'dist-integrated'
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
$outPath = Join-Path $outDir 'diagnose-env-output.txt'

$sb = New-Object System.Text.StringBuilder
function W($txt) {
    Write-Host $txt
    [void]$sb.AppendLine($txt)
}

W "=== chayuan MSI 安装环境体检 ==="
W "时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
W "用户: $env:USERNAME (PC: $env:COMPUTERNAME)"
W ""

# ─────────── 1. Windows / Installer 版本 ───────────
W "── 1. Windows / 安装引擎 ─────────────────────"
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    W "  OS:           $($os.Caption) ($($os.Version), build $($os.BuildNumber))"
    W "  架构:         $($os.OSArchitecture)"
} catch { W "  OS:           (查询失败: $_)" }

try {
    $msi = Get-Item "$env:SystemRoot\System32\msiexec.exe" -ErrorAction Stop
    W "  msiexec:      $($msi.VersionInfo.ProductVersion)  ($($msi.FullName))"
} catch { W "  msiexec:      (找不到 $env:SystemRoot\System32\msiexec.exe!)" }

try {
    $uac = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -ErrorAction Stop)
    W "  UAC EnableLUA: $($uac.EnableLUA)  ConsentPromptBehaviorAdmin: $($uac.ConsentPromptBehaviorAdmin)"
} catch { W "  UAC:          (查询失败)" }

# 当前 PS 是否是管理员
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
W "  当前 PS 进程是管理员: $isAdmin"
W ""

# ─────────── 2. C: 盘空间 ───────────
W "── 2. 磁盘空间 ───────────────────────────────"
try {
    $cDrive = Get-PSDrive C -ErrorAction Stop
    $freeGB = [math]::Round($cDrive.Free / 1GB, 1)
    $usedGB = [math]::Round($cDrive.Used / 1GB, 1)
    $totalGB = [math]::Round(($cDrive.Used + $cDrive.Free) / 1GB, 1)
    W "  C:\          总 $totalGB GB  已用 $usedGB GB  剩 $freeGB GB"
    if ($freeGB -lt 8) {
        W "  ⚠ 剩余空间 < 8 GB,装 3.5 GB MSI 大概率卡死"
    } else {
        W "  ✓ 剩余空间充足"
    }
} catch { W "  C:\          (查询失败: $_)" }

# %TEMP%
W "  TEMP 路径:    $env:TEMP"
try {
    $tempDrive = (Split-Path -Qualifier $env:TEMP).TrimEnd(':')
    $td = Get-PSDrive $tempDrive -ErrorAction Stop
    W "  TEMP 盘:      $tempDrive`:  剩 $([math]::Round($td.Free/1GB,1)) GB"
} catch { W "  TEMP 盘:      (查询失败)" }
W ""

# ─────────── 3. C:\Windows\Installer\ 缓存目录 ───────────
W "── 3. C:\Windows\Installer\ 缓存目录 ─────────"
$instDir = "$env:SystemRoot\Installer"
if (Test-Path $instDir) {
    try {
        $instItems = Get-ChildItem $instDir -ErrorAction SilentlyContinue
        $instSize = ($instItems | Measure-Object -Property Length -Sum).Sum / 1GB
        W "  目录存在,~$([math]::Round($instSize,1)) GB,~$($instItems.Count) 项"
    } catch {
        W "  目录存在但无法 ls (权限不够)"
    }
    # ACL 检查
    try {
        $acl = Get-Acl $instDir
        $owner = $acl.Owner
        W "  Owner:        $owner"
        W "  ACE 数:       $($acl.Access.Count)"
        # 写权限测试:试写一个 0 字节探针
        $probe = Join-Path $instDir "chayuan-probe-$([guid]::NewGuid().ToString('N').Substring(0,8)).tmp"
        try {
            New-Item -ItemType File -Path $probe -Force -ErrorAction Stop | Out-Null
            Remove-Item $probe -Force -ErrorAction SilentlyContinue
            W "  ✓ 当前用户能直接写(罕见,通常只有 SYSTEM 能写)"
        } catch {
            W "  写探针失败(正常,只有 SYSTEM 可写):$($_.Exception.Message)"
        }
    } catch {
        W "  ACL 读取失败:$_"
    }
} else {
    W "  ⚠ $instDir 不存在!这是 MSI 装包必需的目录,可能 Windows 系统损坏"
}
W ""

# ─────────── 4. msiserver 服务 ───────────
W "── 4. Windows Installer 服务 (msiserver) ──────"
try {
    $svc = Get-Service msiserver -ErrorAction Stop
    W "  状态:         $($svc.Status)"
    W "  启动类型:     $($svc.StartType)"
    if ($svc.Status -ne 'Running') {
        W "  ⚠ 服务没在 Running,管理员 PS 跑:  Start-Service msiserver"
    } else {
        W "  ✓ 服务在跑"
    }
} catch { W "  msiserver:    (查询失败: $_)" }
W ""

# ─────────── 5. Windows Defender ───────────
W "── 5. Windows Defender 实时保护 ───────────────"
try {
    $mp = Get-MpPreference -ErrorAction Stop
    W "  实时监控:     $(if ($mp.DisableRealtimeMonitoring) {'已禁用'} else {'**开启**'})"
    W "  IO 保护:      $(if ($mp.DisableIOAVProtection) {'已禁用'} else {'**开启**'})"
    W "  脚本扫描:     $(if ($mp.DisableScriptScanning) {'已禁用'} else {'开启'})"
    W "  排除路径数:   $(($mp.ExclusionPath | Measure-Object).Count)"
    if ($mp.ExclusionPath) {
        foreach ($p in ($mp.ExclusionPath | Select-Object -First 10)) {
            W "    - $p"
        }
    }
    if (-not $mp.DisableRealtimeMonitoring) {
        W "  ⚠ 实时监控开着,大 MSI 写 C:\Windows\Installer\ 时会被 hook"
        W "    一次性临时关掉:  Set-MpPreference -DisableRealtimeMonitoring `$true"
    }
} catch {
    W "  Get-MpPreference 调失败: $_"
    W "  (可能 Defender 被第三方杀软接管了)"
}
W ""

# ─────────── 6. 第三方杀软扫描 ───────────
W "── 6. 已注册的 AV / EDR 产品 (WMI) ────────────"
try {
    $avs = Get-CimInstance -Namespace root\SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction Stop
    if ($avs) {
        foreach ($av in $avs) {
            # productState 解码:bit 12 = 启用,bit 4 = 实时
            $state = "{0:X}" -f $av.productState
            W "  - $($av.displayName)  (state 0x$state, path $($av.pathToSignedProductExe))"
        }
    } else {
        W "  (WMI SecurityCenter2 没列出任何 AV)"
    }
} catch {
    W "  WMI 查询失败:$_"
}
W ""

# ─────────── 7. 进程扫描:已知国产 / 海外 AV/EDR 进程 ───────────
W "── 7. 正在跑的杀软 / EDR 进程 ─────────────────"
$avProcNames = @(
    # Windows Defender
    'MsMpEng', 'NisSrv', 'SecurityHealthService', 'WdNisSvc',
    # 360
    '360tray', '360sd', 'ZhuDongFangYu', '360Safe', 'QQPCRTP',
    # 腾讯
    'QQPCTray', 'QQPCMgr', 'QQPCMain', 'TXMGR_Main',
    # 火绒
    'HipsTray', 'HipsDaemon', 'wsctrl',
    # 海外
    'avp', 'avgnt', 'mcshield', 'McTray', 'symantec', 'kavsvc',
    'avgwd', 'avgrsx', 'avast', 'avastsvc', 'bdwtxag', 'bdagent',
    'eset', 'ekrn', 'msmpeng', 'sentinel', 'cylance',
    # EDR
    'CSFalconService', 'CrowdStrike', 'CarbonBlackK', 'cb', 'SentinelAgent',
    'CylanceSvc', 'TaniumClient', 'huntress'
)
$running = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $avProcNames -contains $_.ProcessName
}
if ($running) {
    foreach ($p in $running) {
        $path = try { $p.MainModule.FileName } catch { '<拒绝访问,几乎肯定就是它>' }
        W "  - $($p.ProcessName) (PID $($p.Id))  $path"
    }
} else {
    W "  (没扫到常见的杀软/EDR 进程,但不代表没装 — WMI 没列的看那里)"
}
W ""

# ─────────── 8. 总结建议 ───────────
W "── 总结 ──────────────────────────────────────"
$verdict = @()
if ($freeGB -lt 8) {
    $verdict += "⚠ C: 盘空闲 $freeGB GB < 8 GB,装 3.5 GB MSI 高风险"
}
if (-not $mp.DisableRealtimeMonitoring) {
    $verdict += "⚠ Defender 实时监控开着 — 装包前临时关:Set-MpPreference -DisableRealtimeMonitoring `$true"
}
if ($running) {
    $verdict += "⚠ 还有以下杀软/EDR 进程在跑:$(($running.ProcessName -join ', ')) — 装包前在系统托盘上右键退出"
}
if (-not $svc -or $svc.Status -ne 'Running') {
    $verdict += "⚠ msiserver 服务没跑 — 管理员 PS:Start-Service msiserver"
}
if ($verdict.Count -eq 0) {
    W "  ✓ 环境检查没看到明显问题。如果还装不上,基本上 MSI > 2.5 GB 撞了"
    W "    Windows Installer 内部隐性上限,需切外置 CAB (EmbedCab=no)"
} else {
    foreach ($v in $verdict) { W "  $v" }
}
W ""

$sb.ToString() | Set-Content -Path $outPath -Encoding UTF8
Write-Host ""
Write-Host "──────────── 输出已落到: $outPath ────────────"
Write-Host "把上面整段(或 $outPath 文件内容)贴回给我"
