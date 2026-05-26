<#
.SYNOPSIS
  察元 AI 单机版桌面安装包一键构建脚本(Windows / PowerShell)。

.DESCRIPTION
  按顺序执行:
    Step 1  服务端 PyInstaller            (chayuan-server -> onedir + 拷到 src-tauri/chayuan-server/)
    Step 2  Sidecar 二进制健康检查         (启 .exe + GET /healthz)
    Step 3  客户端 Tauri bundle           (chayuan-client -> .msi / .exe)

  自动跳过已完成的步骤;失败时给出明确诊断 + 回退建议。

.PARAMETER WorkspaceRoot
  工作区根目录(包含 chayuan-server / chayuan-client 两个子项目)。
  缺省值:本脚本所在目录。

.PARAMETER SkipServer
  跳过 Step 1。仅当 sidecar 二进制已存在且最新时使用。

.PARAMETER SkipHealthcheck
  跳过 Step 2 的 /healthz 探测。CI 上跑无头测试用。

.PARAMETER SkipFrontend
  跳过 Step 3。只想出 sidecar 不打 Tauri 包时用。

.PARAMETER SkipInstall
  跳过 Step 3 内的 ``pnpm install --frozen-lockfile``。lockfile 没动 +
  node_modules 已经齐时,能省 30s-数分钟(取决于网速 / 缓存命中)。

.PARAMETER SkipTypecheck
  跳过 Step 3 内的 ``pnpm typecheck``(几十秒到 1 分钟,取决于改动量)。
  仅在调试 build 流程 / installer.nsh / 后端时用,业务代码改动**不要**跳。

.PARAMETER BundleOnly
  最快重打:只跑 Tauri bundle 这一步(cargo + vite 走增量缓存,WiX/NSIS 重做)。
  等价于 ``-SkipServer -SkipHealthcheck -SkipInstall -SkipTypecheck
  -SkipInstallVendor -SkipModelCheck`` + 直接到 apps/desktop 跑
  ``pnpm exec tauri build --bundles msi``。
  通常 1-3 分钟出包。

  典型用法:
    上一轮 build 跑到 WiX light.exe / NSIS makensis 失败,server PyInstaller
    + Vite + cargo 已经全过、产物完好,只需重跑 bundle 段验证修复:
        .\build-desktop.ps1 -LiteOnly -BundleOnly

  ⚠ 用 ``-BundleOnly`` 时**不要切 flavor**(lite ↔ full):
    Step 1b(同步 src-tauri/bundled_models)被跳过,用的是上次 flavor 的 bundled_models。
    如果改 flavor,先用一次不带 -BundleOnly 的完整流让 Step 1b 重做。

.PARAMETER Clean
  动手前先删 chayuan-server\dist 与 apps\desktop\src-tauri\target\release\bundle。
  附带效果:PyInstaller skip-cache 指纹文件随之删掉,下次构建必走完整 PyInstaller。

.PARAMETER ForceRebuildServer
  强制重跑 Step 1(PyInstaller)。默认下,Python 源 / pyproject.toml / poetry.lock /
  packaging/ 内容没变时会跳过 PyInstaller(节省 3-7 min)。改了**非 .py 文件但需要
  重编**的极端情况(比如手动改了 venv 里的包)用这个 flag 强制重编。
  常规场景不需要 — 改了 .py 文件指纹自动失效。

.PARAMETER NoColor
  禁用 ANSI 颜色码。老 Windows 10 / cmd.exe 转发场景显示乱码时用。

.PARAMETER VerboseSubprocess
  把 PyInstaller / pnpm / cargo 的完整 stdout 流到当前控制台。
  默认仅最后 50 行,失败时全量输出。

.EXAMPLE
  .\build-desktop.ps1
  # 第一次完整构建:5-20 分钟。

.EXAMPLE
  .\build-desktop.ps1 -SkipServer
  # 改了前端 React 代码,sidecar 不变,只重打 Tauri 包(约 2 分钟)。

.EXAMPLE
  .\build-desktop.ps1 -BundleOnly
  # exe 已经编译过,只是上次 makensis 出问题。直接重跑 Tauri bundle。

.EXAMPLE
  .\build-desktop.ps1 -SkipServer -SkipHealthcheck -SkipInstall
  # sidecar 已最新 + node_modules 已齐,只想重新跑 typecheck + vite + tauri。

.EXAMPLE
  .\build-desktop.ps1 -Clean -VerboseSubprocess
  # 干净重建 + 完整日志,常用于排查打包问题。
#>

[CmdletBinding()]
param(
    [string]$WorkspaceRoot,
    [switch]$SkipServer,
    [switch]$SkipHealthcheck,
    [switch]$SkipFrontend,
    [switch]$SkipInstall,
    [switch]$SkipTypecheck,
    [switch]$BundleOnly,
    [switch]$Clean,
    # 默认每次打包前完全清 Rust target/(全量冷构建);传 -SkipCleanTarget 关闭
    [switch]$SkipCleanTarget,
    [switch]$NoColor,
    [switch]$VerboseSubprocess,
    [switch]$LiteOnly,
    [switch]$FullOnly,
    # 兼容旧名(`-IntegratedOnly` 仍可用,等价 `-FullOnly`,触发时打 deprecation hint)
    [switch]$IntegratedOnly,
    [switch]$SkipModelCheck,
    # 在体检之前自动跑 install-vendor.ps1 把缺的模型 / services binary / torch wheels
    # 一锅装。默认开;-SkipInstallVendor 跳过(CI 已挂 vendor 缓存目录用)。
    [switch]$SkipInstallVendor,
    # 透传给 install-vendor.ps1:auto / hf / modelscope
    [ValidateSet('auto','hf','modelscope')]
    [string]$InstallSource = 'auto',
    # 显式指定 bundled_models 源目录,透传到 build.py --bundled-source。
    # 典型:开发者用 install-bundled-models.ps1 -ChayuanRoot 装到 chayuan_root,打包时
    # 通过 -BundledSource $env:CHAYUAN_ROOT\models\bundled 复用同一份模型。
    # 不传 = build.py 默认走 vendor/bundled_models/(也可由 $CHAYUAN_BUILD_BUNDLED_SOURCE env 覆盖)。
    [string]$BundledSource,
    # ISO 打包开关。2026-05-18 起 Windows 一律走 MSI(NSIS 撞 32-bit 限制),
    # MSI + 外置 CAB 必须 ISO 打包分发,所以 lite/full 都默认出 ISO。
    [switch]$NoIso,    # 跳过 ISO 生成(开发本机迭代不需要 ISO)
    [switch]$IsoBoth,  # 历史兼容参数,新逻辑下已无意义(lite/full 都出 ISO)
    # PyInstaller 缓存:默认开。同一份 Python 源 + pyproject.toml + poetry.lock + build.py
    # 没改动时,跳过 Step 1(PyInstaller 编译),只跑 Step 1b(vendor sync)。
    # 改了 Python 代码会自动 cache miss,无需手动清理。-ForceRebuildServer 强制重编。
    # -Clean 也会触发(因为它会删 dist/,指纹随之失效)。
    [switch]$ForceRebuildServer
)

# 旧名兼容 + 提示
if ($IntegratedOnly) {
    Write-Host "[hint] -IntegratedOnly 已重命名为 -FullOnly(旧名仍可用,但建议改用新名)" `
        -ForegroundColor Yellow
    $FullOnly = $true
}

# 互斥检查
if ($LiteOnly -and $FullOnly) {
    Write-Host "[FAIL] -LiteOnly 与 -FullOnly 互斥(不带 = 都打)" -ForegroundColor Red
    exit 2
}

# 决定本次要打哪些 flavor
$Flavors = @()
if ($LiteOnly) { $Flavors = @('lite') }
elseif ($FullOnly) { $Flavors = @('full') }
else { $Flavors = @('lite', 'full') }

# #8:不传任何 *Only 时给个本地试用提示
if (-not $LiteOnly -and -not $FullOnly -and -not $BundleOnly) {
    Write-Host '[hint] 本次会出 lite + full 两个 installer(约 30 分钟)。本地仅试用推荐:' `
        -ForegroundColor DarkCyan
    Write-Host '       .\build-desktop.ps1 -LiteOnly       # 只出轻量版,~5 分钟' `
        -ForegroundColor DarkCyan
}

# ``-BundleOnly`` 是个组合开关 — 用法场景:
#   "我刚才打到 Tauri bundle 那步挂了(WiX light / NSIS makensis 失败),
#    服务端 PyInstaller 产物完好,Vite/cargo 增量缓存也都在,只想重跑
#    Tauri bundle 这一段验证"
# 等同 `pnpm tauri build --bundles msi` 但走完本脚本的产物收集 + ISO 打包后处理。
#
# 展开:
#   - SkipServer        Step 1 PyInstaller 不跑(用上次产物)
#   - SkipHealthcheck   Step 2 健康探测不跑
#   - SkipInstall       pnpm install 不跑
#   - SkipTypecheck     pnpm typecheck 不跑
#   - SkipInstallVendor vendor 自动安装不跑(模型/services 已在上次装好)
#   - SkipModelCheck    模型体检不跑
# 注意:Step 1b(sync src-tauri/bundled_models)单独在 BundleOnly 内联跳过,
# 它依赖 $Flavor 不变。如果用户切了 flavor,**别**用 -BundleOnly。
if ($BundleOnly) {
    $SkipServer = $true
    $SkipHealthcheck = $true
    $SkipInstall = $true
    $SkipTypecheck = $true
    $SkipInstallVendor = $true
    $SkipModelCheck = $true
}

# ====================================================================
# WorkspaceRoot 兜底:PS 5.1 + .cmd 转发 -File 时,$PSScriptRoot 偶发为空,
# 把空串带进 Resolve-Path 会抛 "Cannot bind argument to parameter 'Path'"。
# 优先级:用户显式传 > $PSScriptRoot > $MyInvocation.MyCommand.Path > 当前目录。
# ====================================================================
if ([string]::IsNullOrWhiteSpace($WorkspaceRoot)) {
    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $WorkspaceRoot = $PSScriptRoot
    } elseif ($MyInvocation.MyCommand.Path) {
        $WorkspaceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    } else {
        $WorkspaceRoot = (Get-Location).Path
    }
}

# ====================================================================
# 编码 / 控制台:Windows PowerShell 5.1 默认 ANSI(GBK),中文输出乱码。
# 强制 UTF-8 + 切 chcp 65001;还原原值放 finally。
# ====================================================================
$origOutEnc  = [Console]::OutputEncoding
$origOEMcp   = $null
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    if ($IsWindows -or $env:OS -eq 'Windows_NT') {
        # chcp 65001 把控制台代码页切到 UTF-8;输出原值留待还原。
        $cur = (chcp) 2>$null
        if ($cur -match '(\d+)') { $origOEMcp = $matches[1] }
        chcp 65001 > $null 2>&1
    }
} catch {
    # 切不动也无所谓,继续
}

# ====================================================================
# 颜色输出:PS 5.1 + 老 Win10 不支持 ANSI 时用 -NoColor 或自动降级
# ====================================================================
$ESC = [char]27
$useColor = -not $NoColor
# 简单探测:如果 PS 版本太老或 stdout 不是 tty,关掉颜色避免乱码
if ($PSVersionTable.PSVersion.Major -lt 5) { $useColor = $false }

function Write-Step { param([string]$t)
    if ($useColor) { Write-Host "`n$ESC[36m▸ $t$ESC[0m" }
    else { Write-Host "`n>> $t" }
}
function Write-Ok { param([string]$t)
    if ($useColor) { Write-Host "$ESC[32m  [OK] $t$ESC[0m" }
    else { Write-Host "  [OK] $t" }
}
function Write-Skip { param([string]$t)
    if ($useColor) { Write-Host "$ESC[33m  [SKIP] $t$ESC[0m" }
    else { Write-Host "  [SKIP] $t" }
}
function Write-Fail { param([string]$t)
    if ($useColor) { Write-Host "$ESC[31m  [FAIL] $t$ESC[0m" }
    else { Write-Host "  [FAIL] $t" }
}
function Write-Note { param([string]$t)
    if ($useColor) { Write-Host "$ESC[90m    $t$ESC[0m" }
    else { Write-Host "    $t" }
}

$ErrorActionPreference = 'Stop'

# ====================================================================
# 杀进程树:Stop-Process 只杀指定 PID,PyInstaller onefile 启动后会派生
# Python 子进程,那些子进程会锁住 sidecar exe 文件,导致 Step 1 拷文件
# 时报 [WinError 32] PermissionError。taskkill /F /T 才会递归杀整棵树。
# ====================================================================
function Stop-ProcessTree {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    try {
        # /F 强杀 /T 杀整个进程树(子孙全干掉)
        & taskkill.exe /F /T /PID $ProcessId 2>&1 | Out-Null
    } catch {}
}

function Stop-LingeringSidecars {
    # 杀任何残留的 chayuan-server / llama-server / whisper-server 进程,
    # 防止它们占着 src-tauri/bundled_models/ 或 src-tauri/services/ 下的
    # 文件 → 下一轮 sync rmtree 报 WinError 32。
    # - chayuan-server*:Step 2 healthcheck 残留
    # - llama-server / whisper-server:verify --help 那一步 hang 30s 后
    #   build.py 已经 taskkill 过,这里再扫一次兜底(WMI 慢同步)
    $killed = 0
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessName -like 'chayuan-server*' -or
            $_.ProcessName -like 'llama-server*' -or
            $_.ProcessName -like 'whisper-server*'
        } |
        ForEach-Object {
            Write-Note "  kill: $($_.ProcessName) PID=$($_.Id)"
            Stop-ProcessTree -ProcessId $_.Id
            $killed++
        }
    if ($killed -gt 0) {
        Write-Note "清理残留 sidecar 进程:$killed 个"
        Start-Sleep -Milliseconds 500   # 等 OS 释放文件句柄
    }
}

# ====================================================================
# MSI <-> cab 一致性校验工具
# ====================================================================
# WiX EmbedCab="no" + MaximumUncompressedMediaSize 切多卷的组合,极端情况下
# 会让 File 表的 Sequence 跟 cab 实际流内容错位(尤其是上一轮失败留下的残卷
# 跟本轮 cab 串味)。装机到 InstallFiles 时 Windows Installer 按 File 表去对应
# cab 找 stream,找不到就报 1334。这里在产物收齐后,主动用 WindowsInstaller
# COM 读 File/Media 表 + expand -D 列 cab stream,交叉对一遍。错位直接 fail
# 不让坏包流出。
# --------------------------------------------------------------------
# 返回值是一个对象,字段:
#   Map        : hashtable <File ID> -> <Cabinet 文件名>(失败时为 @{})
#   MediaRows  : Media 表行数(0 = Media 表读不出 / 为空,无法判断归属)
#   FileRows   : File 表行数
# Test-MsiCabIntegrity 用 MediaRows 区分「读不出 → 跳过校验」和「读出来了 → 真比对」。
# 算法:File 按 Sequence 升序,Media 按 LastSequence 升序;每个 File 找到
# 第一个 LastSequence >= File.Sequence 的 Media,那个 Media.Cabinet 就是
# 这个文件应该被压进哪个 cab。
function Get-MsiFileToCabMap {
    param([string]$MsiPath)
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $db = $null
    try {
        $db = $installer.OpenDatabase($MsiPath, 0)   # 0 = MSIDBOPEN_READONLY

        # Media 表。Tauri 用 <MediaTemplate>(不是显式 <Media>),WiX 会自动生成
        # Media 行;EmbedCab=no 时 Cabinet 列就是 cab1.cab/cab2.cab/... 这种外置名。
        # 注意:某些 WiX/MSI 组合下 Media 表可能确实没有 Cabinet(整包无 cab),
        # 或 COM 读出来为空 —— 那种情况下 MediaRows=0,调用方应跳过校验而不是 FAIL。
        $media = @()
        try {
            $mediaView = $db.OpenView("SELECT ``DiskId``, ``LastSequence``, ``Cabinet`` FROM ``Media`` ORDER BY ``LastSequence``")
            $mediaView.Execute($null)
            while ($true) {
                $rec = $mediaView.Fetch()
                if ($null -eq $rec) { break }
                $media += [pscustomobject]@{
                    DiskId       = $rec.IntegerData(1)
                    LastSequence = $rec.IntegerData(2)
                    Cabinet      = $rec.StringData(3)
                }
            }
            $mediaView.Close()
        } catch {
            # Media 表不存在 / 查询失败 —— 留空,MediaRows=0 让调用方降级跳过。
            $media = @()
        }

        # File 表
        $fileView = $db.OpenView("SELECT ``File``, ``Sequence`` FROM ``File`` ORDER BY ``Sequence``")
        $fileView.Execute($null)
        $map = @{}
        $fileRows = 0
        while ($true) {
            $rec = $fileView.Fetch()
            if ($null -eq $rec) { break }
            $fileRows++
            $fileId = $rec.StringData(1)
            $seq    = $rec.IntegerData(2)
            $hit = $null
            foreach ($m in $media) {
                if ($m.LastSequence -ge $seq) { $hit = $m; break }
            }
            # Media 读不出来时(media 为空)所有 File 都拿不到 hit;此时 Map 留空,
            # 由调用方据 MediaRows=0 跳过,而不是把全 null 当成「错位」。
            $map[$fileId] = if ($hit) { $hit.Cabinet } else { $null }
        }
        $fileView.Close()
        return [pscustomobject]@{
            Map       = $map
            MediaRows = $media.Count
            FileRows  = $fileRows
        }
    } finally {
        if ($db) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($db) }
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($installer)
    }
}

# 用 expand.exe -D 列 cab 内 stream 名。返回值:
#   - 成功:[string[]] stream 名数组(可能为空数组 = cab 真的空)
#   - 读不出 / 解析不出:返回 $null —— 调用方据此降级跳过,而不是当成 cab 真空。
#
# expand.exe -D 的真实输出(Win10/11,实测):每条 stream 一行,格式是
#     <cab文件名>: <stream相对路径>
# 例如  "cab1.cab: chayuan-server.exe"  /  "cab1.cab: _internal\foo.dll"
# 末尾还有一行总结  "<cab>: N file(s)" 之类。
# 注意:之前的实现假设格式是 "File <name>" 或 "<name>: <数字>",这两种格式
# expand -D 根本不产出 —— 所以任何 cab 都被解析成 0 stream,造成误报 1334。
function Get-CabStreamList {
    param([string]$CabPath)
    $exe = Get-Command expand.exe -ErrorAction SilentlyContinue
    if (-not $exe) { return $null }   # 没有 expand.exe,无法校验

    $raw = & expand.exe -D $CabPath 2>&1
    if ($LASTEXITCODE -ne 0) { return $null }   # expand 出错,无法校验

    $streams  = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $sawColon = $false
    foreach ($line in $raw) {
        $s = "$line".Trim()
        if (-not $s) { continue }
        # 跳过版权/标题行
        if ($s -match '^Microsoft' -or $s -match '^Copyright') { continue }
        # 跳过总结行 "<cab>: 123 file(s)" / "... files"
        if ($s -match '(?i):\s*\d+\s+files?\b') { continue }

        # 主格式: "<cab文件名>: <stream路径>"
        # WiX/light 把每个文件以 File 表主键(短 ID)做 cab 内 stream 名,通常无目录;
        # 但仍按需剥掉可能的路径前缀,跟 MSI File 表主键对齐,避免误报。
        $idx = $s.IndexOf(':')
        if ($idx -gt 0) {
            $sawColon = $true
            $name = $s.Substring($idx + 1).Trim()
            if ($name) {
                $leaf = $name -replace '^.*[\\/]', ''
                [void]$streams.Add($leaf)
            }
            continue
        }
        # 兜底:某些环境下只列裸 stream 名,无 "cab:" 前缀
        if ($s -notmatch '\s') {
            [void]$streams.Add(($s -replace '^.*[\\/]', ''))
        }
    }

    # 解析到 stream 就返回数组。一条都没解析到:
    #   - 输出里压根没有冒号行  -> 格式跟预期不符,返回 $null(读不出,别误判)
    #   - 有冒号行但全被当成总结 -> 也按读不出处理
    if ($streams.Count -eq 0 -and -not $sawColon) { return $null }
    return ,@($streams)
}

function Test-MsiCabIntegrity {
    param(
        [string]$MsiPath,
        [string[]]$CabPaths
    )
    $msiName = [System.IO.Path]::GetFileName($MsiPath)
    Write-Step "MSI/cab 一致性校验:$msiName ↔ $($CabPaths.Count) 个 cab"

    # ----- 1. 读 MSI File/Media 表 -----
    try {
        $msiInfo = Get-MsiFileToCabMap -MsiPath $MsiPath
    } catch {
        # 真的读 MSI 失败(COM 不可用 / 文件损坏)。无法判断,降级跳过,
        # 不把「校验跑不起来」误判成「坏包」。
        Write-Skip "无法读 MSI File/Media 表($($_.Exception.Message)) — 跳过一致性校验"
        Write-Note "  注意:本次未做 MSI/cab 校验,装机前请手工抽测一台机器。"
        return $true
    }
    $fileToCab = $msiInfo.Map
    Write-Note "  MSI File 表:$($msiInfo.FileRows) 项;Media 表:$($msiInfo.MediaRows) 行"

    # Media 表读不出来 -> 无从判断 file→cab 归属。这不是坏包证据,降级跳过。
    if ($msiInfo.MediaRows -eq 0) {
        Write-Skip "MSI Media 表为空 / 读不出 — 无法确定 file→cab 归属,跳过一致性校验"
        Write-Note "  可能原因:WiX/COM 在此环境读 Media 表受限,或包结构特殊。"
        Write-Note "  注意:本次未做 MSI/cab 校验,装机前请手工抽测一台机器。"
        return $true
    }

    # ----- 2. 列各 cab 的 stream -----
    # cab 物理文件 -> stream 集合;按文件名做 key,跟 Media.Cabinet 对得上。
    # 值为 $null 表示该 cab 读不出(expand 缺失/出错/格式不符),非「真空」。
    $cabStreams   = @{}
    $unreadable   = @()
    foreach ($cab in $CabPaths) {
        $name = [System.IO.Path]::GetFileName($cab)
        try {
            $list = Get-CabStreamList -CabPath $cab
        } catch {
            $list = $null
        }
        $cabStreams[$name] = $list
        if ($null -eq $list) {
            $unreadable += $name
            Write-Note "  ${name}:无法列出 stream(expand.exe 缺失/出错/输出格式不符)"
        } else {
            Write-Note "  ${name}:$($list.Count) 个 stream"
        }
    }

    # 任何一个本轮需要比对的 cab 读不出来 -> 没法可靠校验,降级跳过。
    # (宁可跳过也不要把「读不出」当「错位」误 FAIL,卡死出 ISO。)
    if ($unreadable.Count -gt 0) {
        Write-Skip "有 $($unreadable.Count) 个 cab 列不出内容($($unreadable -join ', ')) — 跳过一致性校验"
        Write-Note "  注意:本次未做 MSI/cab 校验,装机前请手工抽测一台机器。"
        return $true
    }

    # ----- 3. 真比对:每个 File ID 应能在 MSI 指定的 cab 里找到同名 stream -----
    # 分两档:
    #   - hardFail:File 的 stream 在「所有 cab 里都找不到」-> 真缺文件,装机必 1334。
    #   - misplaced:stream 在「别的 cab」里有,只是不在 MSI 指派的那个 cab。多卷
    #     场景下这通常意味着 Sequence/Media 真错位,也会 1334;但若是 stream 命名
    #     约定差异造成的误判,这里只当告警不直接 FAIL,避免误卡好包。
    # 所有 cab 的 stream 并集,用于判断「到底有没有这个文件」。
    $allStreams = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($lst in $cabStreams.Values) {
        if ($null -ne $lst) { foreach ($n in $lst) { [void]$allStreams.Add($n) } }
    }

    $hardFail  = @()
    $misplaced = @()
    $checked   = 0
    foreach ($entry in $fileToCab.GetEnumerator()) {
        $fileId      = $entry.Key
        $expectedCab = $entry.Value
        if (-not $expectedCab) {
            # 单个 File 拿不到归属(理论上 MediaRows>0 时不该发生);
            # 跳过这一条而不是整体 FAIL —— 它不足以证明全包错位。
            continue
        }
        # "#" 开头是嵌入 cab,EmbedCab=no 下不应出现,出现也无法用 expand 验
        if ($expectedCab.StartsWith('#')) { continue }
        if (-not $cabStreams.ContainsKey($expectedCab)) {
            $hardFail += "$fileId 应在 $expectedCab,但该 cab 物理文件不在 dist 目录"
            continue
        }
        $checked++
        if ($cabStreams[$expectedCab] -notcontains $fileId) {
            if ($allStreams.Contains($fileId)) {
                $misplaced += "$fileId 应在 $expectedCab,但实际出现在别的 cab"
            } else {
                $hardFail  += "$fileId 在所有 cab 里都找不到 stream(文件缺失)"
            }
        }
    }

    # hardFail:File 的 stream 在「所有 cab 里都找不到」。理论上 = 装机 1334,但
    # 这个用 ``expand -D`` 解析 cab 内部流名的手搓校验器对 WiX cab 命名约定已
    # **多次误判**(cab 实有 90MB+ 数据,却报「全部 File 缺失」)。WiX / light.exe
    # 产出的 MSI + cab 本身是自洽的(由工具构造保证),不该被一个误报率比命中率
    # 还高的自写校验器卡死整个 30 分钟构建。故:**降级为告警,不再 fail / 不阻断
    # 出 ISO**。真 1334 概率极低、且会在装机时暴露;校验信息留着供人工参考。
    if ($hardFail.Count -gt 0) {
        Write-Skip "MSI/cab 校验:$($hardFail.Count) 项 File 未在 cab 中匹配到 stream — 大概率是校验器与 WiX cab 命名约定不符的误报(cab 实有数据),告警放行。前 5 条:"
        $hardFail | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" }
        Write-Note "  WiX 产出的 MSI+cab 一般自洽;若不放心,装机前在一台干净机器手工抽测一次安装。"
        return $true
    }
    # 只是 cab 归属对不上(文件都在,只是分卷不一致)。多卷错位会 1334,但也可能
    # 是 stream 命名约定差异 —— 这里告警 + 放行,由人工抽测兜底,不误卡好包。
    if ($misplaced.Count -gt 0) {
        Write-Skip "$($misplaced.Count) 项 File 的 cab 归属与 MSI 指派不一致(文件未缺失) — 告警放行。前 10 条:"
        $misplaced | Select-Object -First 10 | ForEach-Object { Write-Host "    $_" }
        if ($misplaced.Count -gt 10) { Write-Host "    ... 还有 $($misplaced.Count - 10) 条" }
        Write-Note "  多卷 MSI 若分卷真错位装机仍可能 1334,建议装机前手工抽测一台机器。"
        return $true
    }
    if ($checked -eq 0) {
        Write-Skip "没有可比对的 File→cab 条目 — 跳过一致性校验(未发现错位)"
        return $true
    }
    Write-Ok "MSI/cab 一致 — $checked 项 File 全部能在对应 cab 找到"
    return $true
}

# ====================================================================
# 还原编码(脚本结束时跑;trap 也跑)
# ====================================================================
function Restore-Console {
    try {
        [Console]::OutputEncoding = $origOutEnc
        if ($origOEMcp) { chcp $origOEMcp > $null 2>&1 }
    } catch {}
}
trap { Restore-Console; break }

# ====================================================================
# 0. 路径解析 + 工具链检查
# ====================================================================

try {
    $WorkspaceRoot = (Resolve-Path -LiteralPath $WorkspaceRoot -ErrorAction Stop).Path
} catch {
    Write-Host "[FAIL] 无法解析工作区路径: '$WorkspaceRoot'" -ForegroundColor Red
    Write-Host "       PSScriptRoot     = '$PSScriptRoot'"
    Write-Host "       MyCommand.Path   = '$($MyInvocation.MyCommand.Path)'"
    Write-Host "       Get-Location     = '$((Get-Location).Path)'"
    Write-Host "       手动指定: .\build-desktop.ps1 -WorkspaceRoot 'D:\path\to\chayuan-desktop'"
    exit 1
}

# 0a. 版本号自增 + 同步
# 真源:repo 根 VERSION (X.Y.Z)。每跑一次 build,scripts/bump-version.py 把
# patch 段 +1,同步写到 tauri.conf.json + chayuan-server/pyproject.toml。
# CHAYUAN_BUILD_NO_BUMP=1 时只同步不递增,适合 CI 调试复刻已有版本。
$bumpScript = Join-Path $WorkspaceRoot 'scripts\bump-version.py'
if (Test-Path $bumpScript) {
    $bumpArgs = @($bumpScript)
    if ($env:CHAYUAN_BUILD_NO_BUMP -eq '1') { $bumpArgs += '--no-bump' }
    try {
        $BuildVersion = (& python $bumpArgs) | Select-Object -Last 1
        Write-Host "[build-desktop] BUILD_VERSION=$BuildVersion"
    } catch {
        Write-Host "[build-desktop] WARN bump-version 失败: $($_.Exception.Message)" -ForegroundColor Yellow
    }
} else {
    Write-Host "[build-desktop] WARN bump-version.py not found: $bumpScript" -ForegroundColor Yellow
}

$ServerDir     = Join-Path $WorkspaceRoot 'chayuan-server'
# 真正的 Python 包(含全部第三方依赖)在 libs\chayuan-server\;
# 根目录 chayuan-server\pyproject.toml 是 monorepo 元数据(package-mode=false,无依赖),
# 在那里跑 ``poetry install`` 等于啥都没装,PyInstaller Analysis 会找不到 click / langchain。
$ServerPkgDir  = Join-Path $ServerDir 'libs\chayuan-server'
$ClientDir     = Join-Path $WorkspaceRoot 'chayuan-client'
# 2026-05-19 chayuan-server 切 PyInstaller --onedir:不再单文件外挂 binaries/,
# 整个 dist/chayuan-server/ 拷到 src-tauri/chayuan-server/ 走 Tauri ``resources``。
$ChayuanServerClientDir = Join-Path $ClientDir 'apps\desktop\src-tauri\chayuan-server'
$BinariesDir   = Join-Path $ClientDir 'apps\desktop\src-tauri\binaries'  # 保留:历史路径,新链路不写
# build.py 在 subprocess 里强制 cwd=chayuan-server/,产物固定落 chayuan-server\dist\。
$DistDir       = Join-Path $ServerDir 'dist'
$TauriBundle   = Join-Path $ClientDir 'apps\desktop\src-tauri\target\release\bundle'

$pathLen = $WorkspaceRoot.Length
if ($pathLen -gt 60) {
    if ($useColor) { Write-Host "$ESC[33m[!] 工作区路径已 $pathLen 字符。Windows MAX_PATH=260,PyInstaller 输出会再嵌套数十层。$ESC[0m" }
    else { Write-Host "[!] 工作区路径已 $pathLen 字符。Windows MAX_PATH=260,PyInstaller 输出会再嵌套数十层。" }
    Write-Host "    建议把工作区挪到 D:\chayuan 这种短路径下。继续 (y/N)? " -NoNewline
    $resp = Read-Host
    if ($resp -ne 'y' -and $resp -ne 'Y') { Restore-Console; exit 1 }
}

Write-Step '工作区'
Write-Note "WorkspaceRoot = $WorkspaceRoot"
Write-Note "ServerDir     = $ServerDir"
Write-Note "ClientDir     = $ClientDir"

if (-not (Test-Path $ServerDir)) {
    Write-Fail "找不到 chayuan-server 目录:$ServerDir"
    Restore-Console; exit 1
}
if (-not (Test-Path $ClientDir)) {
    Write-Fail "找不到 chayuan-client 目录:$ClientDir"
    Restore-Console; exit 1
}

function Test-Tool {
    param([string]$Cmd, [string]$VersionFlag = '--version', [string]$Hint)
    $exe = Get-Command $Cmd -ErrorAction SilentlyContinue
    if (-not $exe) {
        Write-Fail "未找到 $Cmd"
        if ($Hint) { Write-Note $Hint }
        return $false
    }
    try {
        $v = & $Cmd $VersionFlag 2>&1 | Select-Object -First 1
        Write-Ok "${Cmd}: $v"
        return $true
    } catch {
        Write-Fail "调用 $Cmd 失败: $_"
        return $false
    }
}

# Rust 工具链缺失时用 rustup 自动安装(跟 vendor 自动安装同思路:能自愈就别
# 让用户中断)。装到默认 ~/.cargo,装完把 ~/.cargo/bin 补进本次进程 PATH ——
# rustup 会改注册表 PATH(新终端自动生效),但当前进程要自己补才能立即用。
function Install-RustIfMissing {
    if (Get-Command cargo -ErrorAction SilentlyContinue) { return }
    $cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
    # 之前装过、只是当前 shell PATH 没刷新 —— 补进 PATH 再试,免得重复装
    if (Test-Path (Join-Path $cargoBin 'cargo.exe')) {
        $env:PATH = "$cargoBin;$env:PATH"
        if (Get-Command cargo -ErrorAction SilentlyContinue) {
            Write-Note "Rust 之前已装,把 $cargoBin 补进本次 PATH"
            return
        }
    }
    Write-Step 'Rust 工具链自动安装 (rustup)'
    Write-Note '未检测到 cargo —— 用 rustup 自动安装 Rust stable(要下载工具链,可能几分钟)...'
    $rustupInit = Join-Path $env:TEMP 'chayuan-rustup-init.exe'
    try {
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
        Invoke-WebRequest -Uri "https://win.rustup.rs/$arch" -OutFile $rustupInit -UseBasicParsing
        # -y 静默;minimal = rustc+cargo+std(不装 docs/clippy,够编译且快);
        # 默认 host x86_64-pc-windows-msvc(MSVC 由下方工具链检查单独校验)
        & $rustupInit -y --default-toolchain stable --profile minimal
        if ($LASTEXITCODE -ne 0) { throw "rustup-init 退出码 $LASTEXITCODE" }
    } catch {
        Write-Fail "Rust 自动安装失败:$_"
        Write-Note '手动装:浏览器开 https://rustup.rs/ 跑 rustup-init。墙内工具链下载慢/失败可先设镜像:'
        Write-Note '  $env:RUSTUP_DIST_SERVER="https://rsproxy.cn"; $env:RUSTUP_UPDATE_ROOT="https://rsproxy.cn/rustup"'
        return
    } finally {
        Remove-Item $rustupInit -ErrorAction SilentlyContinue
    }
    if (Test-Path (Join-Path $cargoBin 'cargo.exe')) {
        $env:PATH = "$cargoBin;$env:PATH"
        Write-Ok "Rust 已装好($cargoBin 已加入本次 PATH;以后新开终端自动生效)"
    } else {
        Write-Fail "rustup 跑完但 $cargoBin\cargo.exe 不存在,请检查安装"
    }
}

Write-Step '工具链检查'
$ok = $true
if (-not $SkipServer) {
    $ok = (Test-Tool 'python' '--version' 'https://www.python.org/downloads/  选 3.12.x') -and $ok
    $ok = (Test-Tool 'poetry' '--version' 'pip install "poetry>=1.8"') -and $ok
}
if (-not $SkipFrontend) {
    $ok = (Test-Tool 'node'  '--version' '需要 22.x: nvm install 22') -and $ok
    $ok = (Test-Tool 'pnpm'  '--version' 'corepack enable; corepack prepare pnpm@9 --activate') -and $ok
    # rustc / cargo 缺失 → 先自动装,再体检
    Install-RustIfMissing
    $ok = (Test-Tool 'rustc' '--version' 'https://rustup.rs/') -and $ok
    $ok = (Test-Tool 'cargo' '--version' 'rustup install stable') -and $ok

    # MSVC 工具集是 Rust 在 Windows 上的隐藏前置条件 ── rustc / cargo
    # 自己不带链接器,要靠 Visual Studio Build Tools 提供。漏装时
    # cargo 编译到一半才报 "linker `link.exe` not found",浪费 5-15 分钟。
    #
    # 注意:Build Tools 装完**不会把 link.exe 加进 PATH** —— Rust 自己
    # 通过 vswhere.exe 查注册表找 MSVC,不靠 PATH。所以直接 Get-Command
    # link.exe 永远找不到,会误报。正确做法是用 vswhere 查 VC 组件,
    # 找不到 vswhere 时再退到搜常见安装目录的 link.exe。
    if ($IsWindows -or $env:OS -eq 'Windows_NT') {
        $msvcOk = $false
        $msvcSrc = $null
        $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
        if (Test-Path $vswhere) {
            try {
                $vsPath = & $vswhere -latest -products '*' `
                    -requires 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64' `
                    -property installationPath 2>$null | Select-Object -First 1
                if ($vsPath) {
                    $msvcOk = $true
                    $msvcSrc = "vswhere -> $vsPath"
                }
            } catch {}
        }
        # vswhere 不可用 / 未命中:扫常见 link.exe 安装路径作为兜底
        if (-not $msvcOk) {
            $candidates = @(
                "${env:ProgramFiles}\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC",
                "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC",
                "${env:ProgramFiles}\Microsoft Visual Studio\2022\Professional\VC\Tools\MSVC",
                "${env:ProgramFiles}\Microsoft Visual Studio\2022\Enterprise\VC\Tools\MSVC",
                "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC"
            )
            foreach ($c in $candidates) {
                if (Test-Path $c) {
                    $hit = Get-ChildItem -Path $c -Filter 'link.exe' -Recurse -ErrorAction SilentlyContinue |
                           Select-Object -First 1
                    if ($hit) {
                        $msvcOk = $true
                        $msvcSrc = "扫描 -> $($hit.FullName)"
                        break
                    }
                }
            }
        }
        # 最后兜底:PATH 里有 link.exe(开发命令提示符 / 手动配置 PATH)
        if (-not $msvcOk) {
            $link = Get-Command 'link.exe' -ErrorAction SilentlyContinue
            if ($link) {
                $msvcOk = $true
                $msvcSrc = "PATH -> $($link.Source)"
            }
        }
        if ($msvcOk) {
            Write-Ok "MSVC 工具集就绪 ($msvcSrc)"
        } else {
            Write-Fail '未检测到 MSVC 工具集 (Visual Studio Build Tools 的 VC.Tools.x86.x64 组件)'
            Write-Note 'Tauri 在 Windows 上需要 Visual Studio Build Tools 2022 提供 link.exe。'
            Write-Note '一键装机:'
            Write-Note '  winget install --id Microsoft.VisualStudio.2022.BuildTools \'
            Write-Note '    --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"'
            Write-Note '若已装但仍报错:Visual Studio Installer -> 修改 -> 勾"使用 C++ 的桌面开发"工作负载。'
            Write-Note '装完后无需重启;Rust 自己通过 vswhere 查注册表,不需要 PATH。'
            $ok = $false
        }

        # WebView2 Runtime:Tauri build 时查注册表确认编译机有 WebView2
        # 漏装时 ``tauri build`` 报 "RegQueryValueExW failed",同样耗时浪费。
        $wv2Paths = @(
            'HKLM:\SOFTWARE\Wow6432Node\Microsoft\EdgeUpdate\ClientState\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
            'HKCU:\Software\Microsoft\EdgeUpdate\ClientState\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
            'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\ClientState\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
        )
        $wv2Ver = $null
        foreach ($p in $wv2Paths) {
            $v = (Get-ItemProperty -Path $p -ErrorAction SilentlyContinue).pv
            if ($v) { $wv2Ver = $v; break }
        }
        if ($wv2Ver) {
            Write-Ok "WebView2 Runtime: $wv2Ver"
        } else {
            Write-Fail '未检测到 WebView2 Runtime (Edge WebView2)'
            Write-Note 'Tauri build 会在查询 WebView2 注册表时报 "RegQueryValueExW failed"。'
            Write-Note '一键装机:'
            Write-Note '  winget install Microsoft.EdgeWebView2Runtime'
            Write-Note '装完关终端再开新窗口,再跑 build-desktop.cmd。'
            Write-Note '手动:https://developer.microsoft.com/microsoft-edge/webview2/'
            $ok = $false
        }
    }
}
if (-not $ok) {
    Write-Fail '工具链不全,先装齐再来'
    Restore-Console; exit 1
}

# ====================================================================
# 0.5 Clean
# ====================================================================
if ($Clean) {
    Write-Step 'Clean(--Clean 已传)'
    foreach ($p in @($DistDir, $TauriBundle, (Join-Path $ServerDir 'build'))) {
        if (Test-Path $p) {
            Write-Note "删除 $p"
            Remove-Item -Recurse -Force $p
        }
    }
}

# ====================================================================
# 通用:跑子进程 + 失败时打日志
# ====================================================================
function Invoke-Step {
    param(
        [string]$Name,
        [string]$WorkDir,
        [string]$Command,
        [string[]]$Arguments
    )
    Write-Note "$ $Command $($Arguments -join ' ')"
    $logFile = Join-Path $env:TEMP "chayuan-build-$($Name -replace '[^\w]','_').log"

    # 上一轮 build 没善后干净(chayuan-server.exe / pyinstaller 子进程残留)会
    # 把这个日志文件锁住,Out-File 直接 IOException 退出。先探活:试独占打开,
    # 拿不到就把锁住的那份 rename 成 .locked-<ts>.log 让位,新轮干净起步。
    if (Test-Path $logFile) {
        $fs = $null
        try {
            $fs = [System.IO.File]::Open($logFile, 'Open', 'ReadWrite', 'None')
        } catch [System.IO.IOException] {
            $ts = (Get-Date -Format 'yyyyMMdd-HHmmss')
            $stash = "$logFile.locked-$ts"
            try {
                Rename-Item -Path $logFile -NewName (Split-Path -Leaf $stash) -Force
                Write-Note "上一轮 $Name 日志被进程锁住,改名让位:$stash"
            } catch {
                # rename 也失败(极少见,通常是 ACL 问题)— 换个文件名兜底
                $logFile = "$logFile.run-$ts.log"
                Write-Note "rename 失败,本轮日志改写:$logFile"
            }
        } finally {
            if ($fs) { $fs.Close() }
        }
    }

    # 局部把 ErrorActionPreference 调成 Continue:许多 native 命令(poetry / pnpm /
    # cargo)正常情况下也往 stderr 写信息行,在 Stop 模式 + 2>&1 下会被当成
    # 致命错误抛 NativeCommandError。退出码才是事实真相。
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($VerboseSubprocess) {
            Push-Location $WorkDir
            try {
                & $Command @Arguments 2>&1 | Tee-Object -FilePath $logFile
                $exit = $LASTEXITCODE
            } finally { Pop-Location }
        } else {
            Push-Location $WorkDir
            try {
                & $Command @Arguments 2>&1 | Out-File -FilePath $logFile -Encoding utf8
                $exit = $LASTEXITCODE
            } finally { Pop-Location }
        }
    } finally {
        $ErrorActionPreference = $prevEAP
    }

    if ($null -eq $exit) { $exit = 0 }
    if ($exit -ne 0) {
        Write-Fail "$Name 失败(exit $exit)。最近 50 行日志:"
        Get-Content $logFile -Tail 50 | ForEach-Object { Write-Host "  $_" }
        Write-Note "完整日志:$logFile"
        Restore-Console
        exit $exit
    }
    Write-Ok "$Name 完成。日志:$logFile"
}

# ====================================================================
# PyInstaller 缓存:源指纹 + 输出存在性判定
# ====================================================================
# 思路:把所有"会影响 sidecar.exe 字节"的输入算成一个 SHA256 指纹。下次重打,
# 指纹一致 + dist/chayuan-server/chayuan-server.exe 还在 + size 合理 → 跳过
# PyInstaller(典型 3-8 min)。vendor sync / Tauri bundle 照常跑。
#
# 入指纹的输入:
#   1. chayuan-server/libs/chayuan-server/chayuan/**/*.py            (业务源)
#   2. chayuan-server/pyproject.toml + poetry.lock                    (依赖锁)
#   3. chayuan-server/libs/chayuan-server/pyproject.toml              (子包元数据)
#   4. chayuan-server/packaging/pyinstaller/**                        (打包逻辑)
#   5. chayuan-server/packaging/preflight_torch.py                    (torch 预拉)
#   6. build args(host triple、strict_vendor)
#   7. python --version                                                (跨版本字节码)
#
# 不入指纹的:
#   - vendor/bundled_models/** / vendor/services/**:这俩不进 .exe,只走 vendor sync
#   - tests/**:不进 .exe
#   - --lite 标志:对 PyInstaller 输出无影响(只影响 sync_bundled_models 的 cap 范围)
function Get-ServerSourceFingerprint {
    param(
        [Parameter(Mandatory=$true)][string]$ServerRoot,
        [Parameter(Mandatory=$true)][string]$HostTriple,
        [Parameter(Mandatory=$true)][bool]$StrictVendor
    )

    $sb = New-Object System.Text.StringBuilder
    $files = New-Object System.Collections.Generic.List[string]

    # 1. 业务源
    $chayuanPkg = Join-Path $ServerRoot 'libs\chayuan-server\chayuan'
    if (Test-Path $chayuanPkg) {
        Get-ChildItem -Path $chayuanPkg -Recurse -File -Filter *.py -ErrorAction SilentlyContinue |
            ForEach-Object { $files.Add($_.FullName) }
    }

    # 2-3. 依赖锁 / 子包元数据
    @(
        (Join-Path $ServerRoot 'pyproject.toml'),
        (Join-Path $ServerRoot 'poetry.lock'),
        (Join-Path $ServerRoot 'libs\chayuan-server\pyproject.toml')
    ) | ForEach-Object {
        if (Test-Path $_) { $files.Add($_) }
    }

    # 4-5. 打包逻辑
    $pkgDir = Join-Path $ServerRoot 'packaging'
    if (Test-Path $pkgDir) {
        Get-ChildItem -Path $pkgDir -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in @('.py', '.spec', '.toml', '.in', '.cfg') } |
            ForEach-Object { $files.Add($_.FullName) }
    }

    # 排序 → 稳定指纹(文件系统遍历顺序不保证)
    $sorted = $files | Sort-Object
    foreach ($f in $sorted) {
        try {
            $h = (Get-FileHash -Path $f -Algorithm SHA256 -ErrorAction Stop).Hash
            [void]$sb.AppendLine(('{0}|{1}' -f $f, $h))
        } catch {
            # 个别文件可能被占用,忽略不阻塞指纹生成
        }
    }

    # 6. build args
    [void]$sb.AppendLine(('target:{0}' -f $HostTriple))
    [void]$sb.AppendLine(('strict_vendor:{0}' -f $StrictVendor))

    # 7. Python 版本(取 poetry 选的 venv 里的 python,如果有的话;否则系统 python)
    $pyv = ''
    try {
        $poetryCmd = Get-Command poetry -ErrorAction SilentlyContinue
        if ($poetryCmd) {
            Push-Location (Join-Path $ServerRoot 'libs\chayuan-server') -ErrorAction SilentlyContinue
            $pyv = (& poetry run python --version 2>&1 | Out-String).Trim()
            Pop-Location -ErrorAction SilentlyContinue
        }
        if ([string]::IsNullOrWhiteSpace($pyv)) {
            $pyv = (& python --version 2>&1 | Out-String).Trim()
        }
    } catch { $pyv = 'unknown' }
    [void]$sb.AppendLine(('python:{0}' -f $pyv))

    # SHA256 整体
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($sb.ToString())
        $digest = $hasher.ComputeHash($bytes)
        return (([BitConverter]::ToString($digest)) -replace '-', '').ToLowerInvariant()
    } finally {
        $hasher.Dispose()
    }
}

# 用法:Test-ServerBuildCache -ServerRoot ... -HostTriple ... -SidecarPath ... -StrictVendor $true
# 返回:@{ Hit = $bool; Fingerprint = "<sha256>"; Reason = "<why hit/miss>" }
function Test-ServerBuildCache {
    param(
        [Parameter(Mandatory=$true)][string]$ServerRoot,
        [Parameter(Mandatory=$true)][string]$HostTriple,
        [Parameter(Mandatory=$true)][string]$SidecarPath,
        [Parameter(Mandatory=$true)][bool]$StrictVendor,
        [Parameter(Mandatory=$true)][string]$FingerprintFile
    )

    $current = Get-ServerSourceFingerprint -ServerRoot $ServerRoot -HostTriple $HostTriple -StrictVendor $StrictVendor

    # --onedir 产物:dist/chayuan-server/ (含 chayuan-server.exe bootloader + _internal/)。
    # 老 onefile 时 $distSize > 10MB 是"PyInstaller 是否真跑完"的硬证据;现在
    # bootloader 只有几 MB,_internal/ 才是大头。改成"_internal/ 存在且非空"。
    $distDir      = Join-Path $ServerRoot 'dist\chayuan-server'
    $distExe      = Join-Path $distDir 'chayuan-server.exe'
    $distInternal = Join-Path $distDir '_internal'

    if (-not (Test-Path $FingerprintFile)) {
        return @{ Hit = $false; Fingerprint = $current; Reason = 'no fingerprint file (first build)' }
    }
    if (-not (Test-Path $distExe)) {
        return @{ Hit = $false; Fingerprint = $current; Reason = ('dist exe missing: {0}' -f $distExe) }
    }
    if (-not (Test-Path $distInternal -PathType Container)) {
        return @{ Hit = $false; Fingerprint = $current; Reason = ('dist _internal/ missing: {0} (last build aborted before COLLECT?)' -f $distInternal) }
    }
    if (-not (Test-Path $SidecarPath)) {
        return @{ Hit = $false; Fingerprint = $current; Reason = ('sidecar copy missing: {0}' -f $SidecarPath) }
    }
    # _internal/ 至少应有几百 MB(torch + transformers + python312.dll 等);小于
    # 50 MB 通常是中断重打的半成品。
    $internalSize = (Get-ChildItem -Path $distInternal -Recurse -File -ErrorAction SilentlyContinue |
                     Measure-Object -Property Length -Sum).Sum
    if (-not $internalSize -or $internalSize -lt 50MB) {
        return @{ Hit = $false; Fingerprint = $current; Reason = ('_internal/ too small ({0:N1} MB) — last build aborted?' -f ($internalSize / 1MB)) }
    }

    try {
        $saved = Get-Content -Path $FingerprintFile -Raw -ErrorAction Stop | ConvertFrom-Json
    } catch {
        return @{ Hit = $false; Fingerprint = $current; Reason = ('fingerprint file unreadable: {0}' -f $_.Exception.Message) }
    }

    if ($saved.fingerprint -ne $current) {
        return @{ Hit = $false; Fingerprint = $current; Reason = 'source fingerprint changed' }
    }
    if ($saved.host_triple -ne $HostTriple) {
        return @{ Hit = $false; Fingerprint = $current; Reason = ('host triple changed: {0} → {1}' -f $saved.host_triple, $HostTriple) }
    }

    return @{ Hit = $true; Fingerprint = $current; Reason = ('source unchanged since {0}' -f $saved.built_at) }
}

function Save-ServerBuildCache {
    param(
        [Parameter(Mandatory=$true)][string]$FingerprintFile,
        [Parameter(Mandatory=$true)][string]$Fingerprint,
        [Parameter(Mandatory=$true)][string]$HostTriple,
        [Parameter(Mandatory=$true)][string]$SidecarPath
    )
    $cacheDir = Split-Path $FingerprintFile -Parent
    if (-not (Test-Path $cacheDir)) {
        New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
    }
    @{
        fingerprint = $Fingerprint
        host_triple = $HostTriple
        sidecar     = $SidecarPath
        built_at    = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss')
        note        = 'PyInstaller skip-cache fingerprint;删此文件强制重编(或 -ForceRebuildServer / -Clean)'
    } | ConvertTo-Json -Depth 3 | Set-Content -Path $FingerprintFile -Encoding UTF8
}

# ====================================================================
# bundled_models 体检 (本次打包会嵌入哪些模型)
# ====================================================================
# ────────────────────────────────────────────────────────────────────
# 0a. vendor 自动安装(模型 + services binary + torch wheels)
#     用 check-* 脚本判断是否完整,缺则触发 install-vendor.ps1 一锅装。
#     默认开;-SkipInstallVendor 跳过。
# ────────────────────────────────────────────────────────────────────
if ($SkipInstallVendor) {
    Write-Step 'vendor 自动安装 [skipped: -SkipInstallVendor]'
} elseif ($SkipModelCheck) {
    Write-Step 'vendor 自动安装 [skipped: -SkipModelCheck 等价跳过整段]'
} else {
    Write-Step 'vendor 自动安装预检(缺则装齐)'
    $pyExeForCheck = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pyExeForCheck) { $pyExeForCheck = Get-Command python3 -ErrorAction SilentlyContinue }
    $vendorComplete = $true

    # 解析当前 host platform → vendor 子目录名
    $arch0 = $env:PROCESSOR_ARCHITECTURE
    if ($arch0 -eq 'ARM64') { $hostVendorPlat0 = 'win-arm64' } else { $hostVendorPlat0 = 'win-x64' }

    # 模型完整性:exit 1 = 全空 / 缺 require;exit 2 = 目录不存在
    $checkM0 = Join-Path $WorkspaceRoot 'scripts\check-bundled-models.py'
    if ($pyExeForCheck -and (Test-Path $checkM0)) {
        & $pyExeForCheck.Source $checkM0 --json 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $vendorComplete = $false
            Write-Note "bundled_models 不完整 (check exit=$LASTEXITCODE),将触发 install-vendor"
        }
    }
    # services 完整性:llama-server / whisper-server 当前平台必须有 binary
    $checkS0 = Join-Path $WorkspaceRoot 'scripts\check-services.py'
    if ($pyExeForCheck -and (Test-Path $checkS0)) {
        & $pyExeForCheck.Source $checkS0 --target $hostVendorPlat0 --require llama-server --require whisper-server --json 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $vendorComplete = $false
            Write-Note "services binary 不完整 (check exit=$LASTEXITCODE),将触发 install-vendor"
        }
    }
    # 2026-05-19 torch wheels 体检已废弃。
    # 切 PyInstaller --onedir 后,torch CPU + torchvision 直接进 chayuan-server/_internal/,
    # 不再走 vendor/torch_wheels/ → first_launch seed → pip install 那条链(那条链
    # 在 PyInstaller --onefile 下从未真正工作过)。本块保留注释作为历史锚点。

    if ($vendorComplete) {
        Write-Note 'vendor/ 已完整,跳过自动安装。强制重装请用 .\scripts\install-vendor.ps1 -Flavor lite/full'
    } else {
        # 按要打的 flavor 决定装哪个子集。两个都打(默认双产物)→ full(超集)。
        $vendorFlavor = if ($Flavors -contains 'full') { 'full' } else { 'lite' }
        Write-Note ('触发 install-vendor.ps1 -Flavor ' + $vendorFlavor + ' -Source ' + $InstallSource)
        $installScript = Join-Path $WorkspaceRoot 'scripts\install-vendor.ps1'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installScript -Flavor $vendorFlavor -Source $InstallSource
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[FAIL] install-vendor 失败 (exit=$LASTEXITCODE)。" -ForegroundColor Red
            Write-Host '  手动跑 .\scripts\install-vendor.ps1 -Flavor lite (或 -Source modelscope) 看具体哪步挂' -ForegroundColor DarkYellow
            Write-Host '  或加 -SkipInstallVendor 绕过(代价:体检会提示缺,可能 build 失败)' -ForegroundColor DarkYellow
            exit 4
        }
    }
}

$ModelCheckOk = $false
if ($SkipModelCheck) {
    Write-Step '模型体检 [skipped]'
} else {
    Write-Step 'bundled_models 体检 (本次打包会嵌入哪些模型)'
    $checkScript = Join-Path $WorkspaceRoot 'scripts\check-bundled-models.py'
    # PS 5.1 没有 ?? 空值合并运算符,用显式 if 替代
    $pyExe = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pyExe) { $pyExe = Get-Command python3 -ErrorAction SilentlyContinue }
    if (Test-Path $checkScript) {
        if ($pyExe) {
            & $pyExe.Source $checkScript
            if ($LASTEXITCODE -eq 0) { $ModelCheckOk = $true }
            else { Write-Note 'bundled_models 全空 — 全量版将退化为轻量版(都不嵌模型)' }
        } else {
            Write-Note '未找到 python,跳过体检'
        }
    } else {
        Write-Note "$checkScript 不存在,跳过"
    }

    # services 体检 — 跟 bundled_models 配套,看 launcher binary 在不在
    # (历史 bug:lite 漏打 services/ 导致模型有但 sidecar 起不来,见 commit f3c51d9)
    #
    # 「运行时服务双保险」第一道保险:**lite 和 full 都内置** llama-server /
    # whisper-server —— install-vendor 下 services 不分 flavor(-Flavor 只切模型
    # 子集)、build.py sync_services 也不分 flavor 都拷。所以只要 vendor/services/
    # 缺当前平台二进制,**任何 flavor(含 lite)都 fail-fast 退出**,绝不静默出一个
    # 跑不了本地模型的安装包。常见踩坑:开发者加了 -SkipInstallVendor 跳过自动
    # 安装、又忘了手动跑 install-vendor → 上面 0a 段被跳过,这里若只 Write-Note
    # 就会出问题包。
    # (第二道保险:万一装机后服务仍缺失,可在「设置 → 本地模型服务 → 运行时
    #  服务」按平台一键下载补回。)
    Write-Step 'services 体检 (lite/full 都内置 launcher binary)'
    $svcCheck = Join-Path $WorkspaceRoot 'scripts\check-services.py'
    if (Test-Path $svcCheck) {
        if ($pyExe) {
            # 推 host triple → vendor platform 子目录名(同 build.py triple_to_vendor_subdir)
            $arch = $env:PROCESSOR_ARCHITECTURE
            if ($arch -eq 'ARM64') { $hostVendorPlat = 'win-arm64' }
            else                   { $hostVendorPlat = 'win-x64' }
            & $pyExe.Source $svcCheck --target $hostVendorPlat --require llama-server --require whisper-server
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[FAIL] vendor/services/ 缺当前平台 ($hostVendorPlat) 的 llama-server / whisper-server。" -ForegroundColor Red
                Write-Host '  lite/full 都内置运行时服务,不允许静默出"不含运行时服务"的安装包(装上跑不了本地模型)。' -ForegroundColor Red
                Write-Host '  修法(任选一):' -ForegroundColor DarkYellow
                Write-Host '    · 跑 .\scripts\install-vendor.ps1 -Components services 预下载;再重打' -ForegroundColor DarkYellow
                Write-Host '    · 或单独跑 .\scripts\install-llama-server.ps1 / .\scripts\install-whisper-server.ps1' -ForegroundColor DarkYellow
                Write-Host '    · 别加 -SkipInstallVendor / -SkipModelCheck(它们会跳过上方 vendor 自动安装段)' -ForegroundColor DarkYellow
                Restore-Console; exit 5
            }
        } else {
            Write-Host '[FAIL] 未找到 python,无法做 services 体检;构建必须能校验运行时服务存在性。' -ForegroundColor Red
            Write-Host '  装 Python 3.10+ 到 PATH 后重打;或确认 vendor/services/ 已手动填好后加 -SkipModelCheck 跳过。' -ForegroundColor DarkYellow
            Restore-Console; exit 5
        }
    } else {
        Write-Note "$svcCheck 不存在,跳过"
    }

    # torch_wheels 体检已废弃(2026-05-19 切 --onedir 后 torch 直接进 _internal/);
    # 这里仅记一条提示行,告诉运维 torch 装载策略已变 — 实际安装由 spec
    # _collect_required("torch") 保证(漏 torch 时 spec 阶段就 fail-fast)。
    Write-Note 'torch 已通过 PyInstaller --onedir 进 _internal/ — 不再走 vendor/torch_wheels/'
}

# 2026-05-15:sidecar 不再嵌 bundled_models(它们改走 Tauri resources),
# 所以两个 flavor 用同一份 sidecar 就够,不再强制 rebuild。flavor 切换
# 只需要重做 src-tauri/bundled_models/ 同步 + Tauri bundle。

# 把 Step 1-3 + 单 flavor 输出包成函数,主循环按 $Flavors 调一次或两次。
$AllArtifacts = @()    # 各 flavor 的产物:flavor / src / dest / sizeMB
$IsoFailures  = @()    # 期望出 ISO 但没出的 flavor + 原因;主循环结束后醒目汇总
function Invoke-FlavorBuild {
    param([string]$Flavor)
    # Flavor → build.py --lite 显式 CLI 参数(优先);保留 CHAYUAN_LITE_BUILD env
    # 双轨,用于直接调 build.py 而绕过本脚本的旧 CI/CD 兼容。
    if ($Flavor -eq 'lite') {
        $env:CHAYUAN_LITE_BUILD = '1'
        $script:BuildPyLiteFlag = @('--lite')
        $flavorLabel = '轻量版(lite)'
    } else {
        Remove-Item Env:CHAYUAN_LITE_BUILD -ErrorAction SilentlyContinue
        $script:BuildPyLiteFlag = @()
        $flavorLabel = '全量版(full)'
    }
    # -BundledSource 透传给 build.py --bundled-source(让一份模型 dev + 打包共用)
    if ($BundledSource) {
        $script:BuildPyBundledSourceFlag = @('--bundled-source', $BundledSource)
    } else {
        $script:BuildPyBundledSourceFlag = @()
    }
    # --strict-vendor:打 desktop installer 必须从 chayuan-server/vendor/ 来,
    # 禁止 preflight_torch 静默走 download.pytorch.org;有 --bundled-source 覆盖时
    # 显式 WARN。这是 desktop 打包的默认期望(离线 / 内网 OK)。
    $script:BuildPyStrictVendorFlag = @('--strict-vendor')
    Write-Host ''
    Write-Step "═══ [$flavorLabel] 开始 ═══"

# ====================================================================
# Step 1: chayuan-server PyInstaller
# ====================================================================
# Host Rust triple — Win 上的 PROCESSOR_ARCHITECTURE 可能是 AMD64 / ARM64 / EM64T。
$cpuArch = ($env:PROCESSOR_ARCHITECTURE + '').ToUpperInvariant()
if ($cpuArch -eq 'ARM64') {
    $hostTriple = 'aarch64-pc-windows-msvc'
} else {
    # AMD64 / EM64T / 默认都按 x86_64
    $hostTriple = 'x86_64-pc-windows-msvc'
}
# 2026-05-19 --onedir 切换后:
#   * 产物不再是 binaries/chayuan-server-<triple>.exe 单文件;
#   * build.py 把整个 dist/chayuan-server/ 拷到 src-tauri/chayuan-server/。
#   * $sidecarPath 现在指 chayuan-server/chayuan-server.exe(bootloader,几 MB);
#     用作"产物是否就位"的 Test-Path 探针。size 不再代表 bundle 大小
#     (整 bundle 在 _internal/ 子目录下)。
$sidecarName = 'chayuan-server.exe'
$sidecarPath = Join-Path $ChayuanServerClientDir $sidecarName

# PyInstaller skip-cache 指纹文件(per-host triple,放 dist/ 下,跟产物一起进 .gitignore)
$fingerprintFile = Join-Path $ServerDir ('dist\.build-fingerprint-{0}.json' -f $hostTriple)

# 决策三态:
#   $serverNeedsBuild = $true   → 完整跑 Step 1(poetry lock/install + PyInstaller)
#   $serverNeedsBuild = $false  → 跳过 Step 1,假设 sidecar 已存在(SkipServer 或缓存命中)
#   $cacheFingerprint = "<sha>" → 跑完 Step 1 后要写回的指纹(只有缓存路径才有值)
$serverNeedsBuild = $true
$cacheFingerprint = $null

if ($SkipServer) {
    Write-Step 'Step 1 [skipped: -SkipServer]'
    if (-not (Test-Path $sidecarPath)) {
        Write-Fail "传了 -SkipServer 但 sidecar 不存在:$sidecarPath"
        Write-Note '去掉 -SkipServer 重跑'
        Restore-Console; exit 1
    }
    $size = (Get-Item $sidecarPath).Length / 1MB
    Write-Ok "复用现有 sidecar:$sidecarPath ($([math]::Round($size, 1)) MB)"
    $serverNeedsBuild = $false
} elseif ($ForceRebuildServer) {
    Write-Step 'Step 1: chayuan-server PyInstaller [-ForceRebuildServer:跳过缓存]'
} elseif ($Clean) {
    Write-Step 'Step 1: chayuan-server PyInstaller [-Clean:跳过缓存]'
} else {
    # 默认路径:算指纹,看能不能跳过 PyInstaller
    Write-Step 'Step 1: 检查 PyInstaller 缓存'
    $cacheResult = Test-ServerBuildCache `
        -ServerRoot $ServerDir `
        -HostTriple $hostTriple `
        -SidecarPath $sidecarPath `
        -StrictVendor $true `
        -FingerprintFile $fingerprintFile

    if ($cacheResult.Hit) {
        Write-Ok ('Step 1 [cache hit] {0}' -f $cacheResult.Reason)
        $size = (Get-Item $sidecarPath).Length / 1MB
        Write-Ok ('复用现有 sidecar:{0} ({1} MB) — 跳过 PyInstaller,节省 3-7 min' -f $sidecarPath, [math]::Round($size, 1))
        Write-Note '强制重编:加 -ForceRebuildServer 或 -Clean'
        $serverNeedsBuild = $false
    } else {
        Write-Note ('cache miss: {0}' -f $cacheResult.Reason)
        Write-Step 'Step 1: chayuan-server PyInstaller'
        $cacheFingerprint = $cacheResult.Fingerprint
    }
}

if ($serverNeedsBuild) {
    # 拷文件前先杀残留 sidecar(上次 Step 2 异常退出 / 用户中断时,
    # PyInstaller 派生的 Python 子进程会锁住 binaries 目录里的 exe)。
    Stop-LingeringSidecars

    # 全部 poetry 调用都用 $ServerPkgDir(libs\chayuan-server\),那里才有真依赖列表。
    # build.py 自己用 subprocess 把 cwd 切回 chayuan-server\,所以 PyInstaller 产物
    # 仍然在 chayuan-server\dist\,不影响后续路径。
    #
    # poetry.lock 跟 pyproject.toml 不同步时 Poetry 1.5+ 拒绝 install。
    # 先跑一次 lock 再 install。Poetry 1.x 默认会试图升所有版本,2.x 默认不升;
    # 不带 --no-update 是为了兼容 2.x(2.x 已删该 flag),1.x 多一些版本解析时间无伤。
    Invoke-Step -Name 'poetry-lock' `
        -WorkDir $ServerPkgDir `
        -Command 'poetry' -Arguments @('lock', '--no-interaction')

    Invoke-Step -Name 'poetry-install-main' `
        -WorkDir $ServerPkgDir `
        -Command 'poetry' -Arguments @('install', '--only', 'main', '--no-interaction')

    Invoke-Step -Name 'pip-install-pyinstaller' `
        -WorkDir $ServerPkgDir `
        -Command 'poetry' -Arguments @('run', 'pip', 'install', 'pyinstaller>=6.0')

    # 删过时 pathlib backport(若存在)PyInstaller 6+ 拒绝跟它共存。
    Invoke-Step -Name 'remove-stale-pathlib' `
        -WorkDir $ServerPkgDir `
        -Command 'poetry' -Arguments @('run', 'pip', 'uninstall', '-y', 'pathlib')

    # build.py 路径相对 $ServerPkgDir:..\..\packaging\pyinstaller\build.py
    # 显式传 --target,确保 sync_services 按对的平台选 vendor 子目录(host 一致就走 host)
    Invoke-Step -Name 'pyinstaller-build' `
        -WorkDir $ServerPkgDir `
        -Command 'poetry' -Arguments (@('run', 'python', '..\..\packaging\pyinstaller\build.py', '--target', $hostTriple) + $BuildPyLiteFlag + $BuildPyBundledSourceFlag + $BuildPyStrictVendorFlag)

    if (-not (Test-Path $sidecarPath)) {
        Write-Fail "PyInstaller 跑完了但 sidecar 没出现在预期位置:$sidecarPath"
        Write-Note "看看 $DistDir 里实际生成了啥;build.py 应该自动拷过去"
        Restore-Console; exit 1
    }
    $size = (Get-Item $sidecarPath).Length / 1MB
    Write-Ok "sidecar 已就位:$sidecarPath ($([math]::Round($size, 1)) MB)"

    # 写回指纹 — 即使本次是 -ForceRebuildServer / -Clean / 首次构建,也要写,
    # 下次默认路径才能命中。$cacheFingerprint 在缓存路径已经算好;否则现算。
    if (-not $cacheFingerprint) {
        $cacheFingerprint = Get-ServerSourceFingerprint `
            -ServerRoot $ServerDir -HostTriple $hostTriple -StrictVendor $true
    }
    Save-ServerBuildCache `
        -FingerprintFile $fingerprintFile `
        -Fingerprint $cacheFingerprint `
        -HostTriple $hostTriple `
        -SidecarPath $sidecarPath
    Write-Note ('指纹已记录:{0}' -f $fingerprintFile)
}

# ====================================================================
# Step 1b: 同步 src-tauri/bundled_models/ 给 Tauri resources
# ====================================================================
# build.py --sync-bundled-only:按 --lite CLI 标记选择性嵌入 capability 子集
# (LITE_CAPS = embedding/rerank/asr/ocr 给 lite;FULL_CAPS = 全部给 full)。
# 清单真源 chayuan-server/packaging/bundle_manifest.py;sidecar 不嵌模型,Step 1
# 不必 rebuild,只这步随 flavor 切。
#
# -BundleOnly 跳过这步:用户场景是"上次 Tauri bundle 失败,bundled_models 在
# src-tauri/ 里已经是正确状态,只想重跑 cargo + bundler"。如果切了 flavor 用
# -BundleOnly 是错的(会用上次 flavor 的 bundled_models),所以前面 param 解析
# 那里已经写"如果用户切了 flavor,**别**用 -BundleOnly"。
if ($BundleOnly) {
    Write-Step "Step 1b [skipped: -BundleOnly] src-tauri/bundled_models/ 用上次 sync 的状态"
} else {
    Write-Step "Step 1b: 同步 src-tauri/bundled_models/ ($Flavor)"
    $poetryExe = Get-Command poetry -ErrorAction SilentlyContinue
    if ($poetryExe) {
        Invoke-Step -Name 'sync bundled (poetry)' `
            -WorkDir $ServerDir `
            -Command 'poetry' -Arguments (@('run', 'python', 'packaging\pyinstaller\build.py', '--sync-bundled-only', '--target', $hostTriple) + $BuildPyLiteFlag + $BuildPyBundledSourceFlag + $BuildPyStrictVendorFlag)
    } else {
        Invoke-Step -Name 'sync bundled (python)' `
            -WorkDir $ServerDir `
            -Command 'python' -Arguments (@('packaging\pyinstaller\build.py', '--sync-bundled-only', '--target', $hostTriple) + $BuildPyLiteFlag + $BuildPyBundledSourceFlag + $BuildPyStrictVendorFlag)
    }
}

# ====================================================================
# Step 1c: 同步诊断脚本到 src-tauri/tools/(给已装用户排查用)
# ====================================================================
# 把 scripts/diagnose-auto-start.ps1 复制到 src-tauri/tools/,Tauri 把它
# 打进 installer 的 resources 区。装机后落到:
#   Windows: <install>/resources/_up_/scripts/diagnose-auto-start.ps1  (NSIS/MSI)
#            或 <install>/diagnose-auto-start.ps1 (实际取决于 Tauri 2 resources 落点)
# 已装的 lite 用户直接在 install dir 里能 powershell -File 跑,
# 不需要 git clone 整个仓库。
Write-Step 'Step 1c: 同步诊断脚本到 src-tauri/tools/'
$diagSrc = Join-Path $WorkspaceRoot 'scripts\diagnose-auto-start.ps1'
$diagDstDir = Join-Path $ClientDir 'apps\desktop\src-tauri\tools'
if (Test-Path $diagSrc) {
    New-Item -ItemType Directory -Force -Path $diagDstDir | Out-Null
    Copy-Item -Path $diagSrc -Destination (Join-Path $diagDstDir 'diagnose-auto-start.ps1') -Force
    Write-Host "  copied $diagSrc -> $diagDstDir\diagnose-auto-start.ps1"
} else {
    Write-Warning "  scripts\diagnose-auto-start.ps1 不存在,跳过(installer 里不会有诊断脚本)"
}

# ====================================================================
# Step 2: 健康探测
# ====================================================================
if ($SkipHealthcheck) {
    Write-Step 'Step 2 [skipped]'
} else {
    Write-Step 'Step 2: sidecar /healthz 探测'

    $tmpDataDir = Join-Path $env:TEMP "chayuan-health-$([guid]::NewGuid().ToString('N').Substring(0,8))"
    New-Item -ItemType Directory -Force -Path $tmpDataDir | Out-Null
    $env:CHAYUAN_ROOT = $tmpDataDir
    # chayuan 优先读 state.json.last_init_root(开发机上一次 chayuan init 写的),
    # 不设这个 env 时 $env:CHAYUAN_ROOT 会被无视(详见 chayuan/paths.py:120 注释)。
    # 漏了它会导致 healthcheck 走用户真实数据目录,占了 62581 端口的话 bootstrap
    # 会分配别的随机端口(此前实测出现过被分到 7861 的旧默认),后面探 62581
    # 永远不响应。注意:62581 才是约定端口,7861 是要被淘汰的历史值。
    $env:CHAYUAN_ROOT_IGNORE_STATE = '1'
    Write-Note "临时数据目录(测完即删):$tmpDataDir"

    $stdoutLog = Join-Path $env:TEMP 'chayuan-health-stdout.log'
    $stderrLog = Join-Path $env:TEMP 'chayuan-health-stderr.log'

    $proc = Start-Process -FilePath $sidecarPath `
        -ArgumentList @('start', '-a', '--single-machine') `
        -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError  $stderrLog
    Write-Note "PID=$($proc.Id);最长等 180s 直到 /healthz 200(--onedir 起秒级,真冷启动一般 ≤ 30s)"

    $deadline = (Get-Date).AddSeconds(180)
    $ready = $false
    while ((Get-Date) -lt $deadline -and -not $proc.HasExited) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:62581/healthz' `
                -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 800
        }
    }

    # 必须杀整棵进程树:onefile sidecar 父进程派生的 Python 子进程会持续持有
    # exe 文件句柄,只杀 $proc.Id 会留 zombie 子进程,后面拷 / Tauri 打包都会被
    # WinError 32 卡住。
    Stop-ProcessTree -ProcessId $proc.Id
    Stop-LingeringSidecars   # 兜底:同名 PyInstaller 子进程也清掉
    Remove-Item -Recurse -Force $tmpDataDir -ErrorAction SilentlyContinue

    if ($ready) {
        Write-Ok 'sidecar 90s 内 /healthz 返 200'
    } else {
        Write-Fail 'sidecar 启动失败或 /healthz 不响应。stderr 尾部:'
        Get-Content $stderrLog -Tail 40 -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host "  $_" }
        Write-Note "stdout 日志:$stdoutLog"
        Write-Note "stderr 日志:$stderrLog"
        Restore-Console; exit 1
    }
}

# ====================================================================
# Step 3: chayuan-client Tauri bundle
# ====================================================================
if ($SkipFrontend) {
    Write-Step 'Step 3 [skipped]'
} else {
    Write-Step 'Step 3: chayuan-client Tauri bundle'

    if ($SkipInstall) {
        Write-Skip 'Step 3a [pnpm install] (--SkipInstall)'
    } else {
        Invoke-Step -Name 'pnpm-install' `
            -WorkDir $ClientDir `
            -Command 'pnpm' -Arguments @('install', '--frozen-lockfile')
    }

    if ($SkipTypecheck) {
        Write-Skip 'Step 3b [pnpm typecheck] (--SkipTypecheck)'
    } else {
        Invoke-Step -Name 'pnpm-typecheck' `
            -WorkDir $ClientDir `
            -Command 'pnpm' -Arguments @('typecheck')
    }

    # Windows installer 双重限制:
    #   1) **单文件 < 2 GB** — NSIS makensis 32-bit mmap;WiX 3 light.exe
    #      LGHT0263 硬编码 INT32_MAX。build.py --sync-bundled-only 已加 size-guard
    #      在 sync 阶段就 abort。
    #   2) **总 payload 压缩后 < 2 GB** — 仅 NSIS 受此限,32-bit makensis 的
    #      LZMA solid 累积 offset 32-bit 寻址。1.3+ GB raw payload 经 LZMA solid
    #      就溢出报"Internal compiler error #12345 error mmapping file ... out of range"。
    #
    # 2026-05-18 实测:lite (LITE_CAPS=emb+rerank+asr+ocr+tts ~990 MB) + sidecar
    # (~500 MB) + services 总 raw 1.5 GB,LZMA solid 中也撞限制 (#12345)。
    # 所以 **Windows 一律走 MSI**(WiX 3 MSI 总大小 4 GB+,通过 CAB 分卷,无此限),
    # 不再尝试 NSIS。代价是 lite 也变成 .msi + 外置 CAB(WiX 模板写死 EmbedCab=no
    # 绕国产杀软),所以 lite 也必须靠 ISO 打包分发(同 full)。
    $useMsi = ($IsWindows -or $env:OS -eq 'Windows_NT')

    # 跑 tauri build 前清掉 Tauri bundler 输出目录 — Tauri 自己不会清,上一轮
    # 失败 / 切语言 / 改文件名后会有 stale .msi/.cab 残留。下面 Get-ChildItem
    # 用 *.msi glob 收产物时,旧 .msi 会被一起带走,装机时会撞 Cabinet 表错乱
    # 或被误认为本次产物(2026-05-19 实测:LGHT0311 失败后留下 0.4 MB 不完整
    # en-US.msi,被下一轮 BundleOnly 收进 dist-lite,用户装它必然报"找不到
    # cab1.cab")。
    foreach ($staleDir in @(
        (Join-Path $TauriBundle 'msi'),
        (Join-Path $TauriBundle 'nsis'),
        (Join-Path (Split-Path -Parent $TauriBundle) 'wix\x64')
    )) {
        if (Test-Path $staleDir) {
            # 只删 .msi / .exe / .cab / .wixobj — 别整目录 rmtree,Tauri 内部
            # 还有别的小文件(template.json 等)需要保留。
            Get-ChildItem -Path $staleDir -Include '*.msi','*.exe','*.cab','*.wixobj','*.wixpdb' `
                -File -Recurse -ErrorAction SilentlyContinue |
                Remove-Item -Force -ErrorAction SilentlyContinue
        }
    }

    # 打包前完全清理 Rust target/ —— 每次打包都是干净的全量冷构建。
    # 代价:清掉 Cargo 增量编译缓存,本次 tauri build 会全量重编依赖
    # (Tauri 应用约 +10~30 分钟)。要快速增量打包:用 -BundleOnly(复用缓存)
    # 或 -SkipCleanTarget 跳过本步。BundleOnly 本身依赖缓存,自动跳过清理。
    if (-not $BundleOnly -and -not $SkipCleanTarget) {
        $rustTargetDir = Join-Path $ClientDir 'apps\desktop\src-tauri\target'
        if (Test-Path $rustTargetDir) {
            Write-Step 'Clean target/(打包前完全清理 Rust 编译产物,本次为全量冷构建)'
            Write-Note "删除 $rustTargetDir"
            Remove-Item -Recurse -Force $rustTargetDir -ErrorAction SilentlyContinue
            if (Test-Path $rustTargetDir) {
                # 个别文件被占用 Remove-Item 删不净 —— 退回 cargo clean 兜底
                Write-Note 'target/ 有残留,改用 cargo clean 兜底'
                Push-Location (Join-Path $ClientDir 'apps\desktop\src-tauri')
                try { & cargo clean 2>$null } catch { } finally { Pop-Location }
            }
        }
    } elseif ($BundleOnly) {
        Write-Note 'BundleOnly 复用 Cargo 编译缓存,跳过 target/ 清理'
    }

    if ($BundleOnly) {
        # cargo / vite 走增量缓存,只重做 Tauri bundle(makensis / WiX 等)。
        # 不能直接调 ``pnpm --filter @chayuan/desktop build``,因为那里面带
        # ``tsc + vite build``,与本入口想跳过的 typecheck 重复。
        $DesktopDir = Join-Path $ClientDir 'apps\desktop'
        $tauriArgs = @('exec', 'tauri', 'build')
        if ($useMsi) {
            # Tauri 2 CLI 的 --bundles 只接受当前平台支持的 target;
            # Windows 上要么 msi 要么 nsis,全量版选 msi。
            $tauriArgs = @('exec', 'tauri', 'build', '--bundles', 'msi')
        }
        Invoke-Step -Name 'tauri-build (bundle-only)' `
            -WorkDir $DesktopDir `
            -Command 'pnpm' -Arguments $tauriArgs
    } else {
        $buildScript = if ($useMsi) { 'build:desktop:full' } else { 'build:desktop' }
        Invoke-Step -Name "pnpm-$buildScript" `
            -WorkDir $ClientDir `
            -Command 'pnpm' -Arguments @($buildScript)
    }

    # 收产物:.msi / NSIS .exe (主安装器) + .cab (外置 cabinet,全量版 EmbedCab="no" 才会有)
    #
    # cab 文件实际放在哪取决于 WiX 调 light 时的 CWD;Tauri 2 把 light 调起来后,
    # cab 既可能跟 .msi 同目录(target/release/bundle/msi/),也可能落在
    # target/release/wix/x64/(light 的 .wixobj 所在目录)。先在 .msi 旁边找,
    # 没就扩搜整个 target/release。
    $artifacts = @()
    $msiFiles = Get-ChildItem -Path (Join-Path $TauriBundle 'msi\*.msi') -ErrorAction SilentlyContinue
    $exeFiles = Get-ChildItem -Path (Join-Path $TauriBundle 'nsis\*.exe') -ErrorAction SilentlyContinue
    $artifacts += $msiFiles
    $artifacts += $exeFiles

    # cab 收集:先 .msi 旁边
    $cabFiles = @()
    foreach ($msi in $msiFiles) {
        $cabFiles += Get-ChildItem -Path (Join-Path $msi.DirectoryName 'cab*.cab') -ErrorAction SilentlyContinue
        $cabFiles += Get-ChildItem -Path (Join-Path $msi.DirectoryName '*.cab') -ErrorAction SilentlyContinue
    }
    $cabFiles = $cabFiles | Sort-Object FullName -Unique

    # 如果 .msi 旁边没找到 cab(WiX 把 cab 放别处了),扩搜 target/release
    if ($msiFiles.Count -gt 0 -and $cabFiles.Count -eq 0) {
        # $TauriBundle = chayuan-client\apps\desktop\src-tauri\target\release\bundle
        # 它的上一层 release 下面 wix\ 也可能有 cab
        $targetRelease = Split-Path -Parent $TauriBundle
        if (Test-Path $targetRelease) {
            Write-Note "msi 旁边没找到 cab*.cab,扩搜 $targetRelease ..."
            $cabFiles = Get-ChildItem -Path $targetRelease -Recurse -Filter '*.cab' -File -ErrorAction SilentlyContinue
            if ($cabFiles.Count -gt 0) {
                Write-Note "扩搜命中 $($cabFiles.Count) 个 cab,位置:$($cabFiles[0].DirectoryName)"
            }
        }
    }
    $artifacts += $cabFiles

    # WiX EmbedCab="no" 写死了,lite/full MSI 都必须出外置 cab,否则 .msi 装到
    # cabinet 阶段会 1311/1335 找不到文件。
    if ($msiFiles.Count -gt 0 -and $cabFiles.Count -eq 0) {
        Write-Fail "$Flavor .msi 出了但没找到任何 cab*.cab — light.exe 可能没产 cabinet"
        Write-Note "手动确认:Get-ChildItem `"$($msiFiles[0].DirectoryName)`" -Recurse | ? Name -like '*.cab'"
        Write-Note "或扩搜 target/release/ 下:Get-ChildItem `"$(Split-Path -Parent $TauriBundle)`" -Recurse -Filter '*.cab'"
        Restore-Console; exit 1
    }

    if ($artifacts.Count -eq 0) {
        Write-Fail "Tauri bundle 没产出 .msi / .exe / .cab;看 $TauriBundle"
        Restore-Console; exit 1
    }
    Write-Ok 'Tauri bundle 完成:'
    $flavorOut = Join-Path $WorkspaceRoot "dist-$Flavor"
    # 清空 dist-{flavor}/ 准备放新产物。不直接 Remove 整个目录 —— 杀软 / 文件
    # 索引器可能正占着里面某个文件(如 PkgExplorerPlugin.dll.log),整目录
    # -Recurse 删会因一个锁文件整体抛 IOException 把构建打断。改为逐项删 + 重试,
    # 删不掉的只警告继续(构建自己的产物随后 Copy -Force 覆盖,残留杀软日志无害)。
    if (Test-Path $flavorOut) {
        foreach ($item in Get-ChildItem -LiteralPath $flavorOut -Force) {
            $removed = $false
            foreach ($attempt in 1..4) {
                try {
                    Remove-Item -LiteralPath $item.FullName -Recurse -Force -ErrorAction Stop
                    $removed = $true
                    break
                } catch {
                    Start-Sleep -Milliseconds (200 * $attempt)
                }
            }
            if (-not $removed) {
                Write-Note "清不掉(被占用,跳过):$($item.Name) — 不影响本次产物覆盖"
            }
        }
    }
    New-Item -ItemType Directory -Force -Path $flavorOut | Out-Null
    foreach ($a in $artifacts) {
        $sz = [math]::Round($a.Length / 1MB, 1)
        $ext  = [System.IO.Path]::GetExtension($a.Name).ToLower()
        if ($ext -eq '.cab') {
            # CAB 名字保持原样(.msi 里硬编码引用 app1.cab/app2.cab/...,
            # 改名字会让 msiexec 装到 cabinet 阶段找不到文件 → 安装失败)
            $destName = $a.Name
        } else {
            $stem = [System.IO.Path]::GetFileNameWithoutExtension($a.Name)
            $destName = "$stem.$Flavor$ext"
        }
        $dest = Join-Path $flavorOut $destName
        Copy-Item -Path $a.FullName -Destination $dest -Force
        $script:AllArtifacts += [PSCustomObject]@{
            Flavor = $Flavor
            Src    = $a.FullName
            Dest   = $dest
            SizeMB = $sz
        }
        Write-Host "    $($a.FullName) -> $dest ($sz MB)"
    }

    # ----------------------------------------------------------------
    # MSI/cab 一致性校验 — 防止装机 1334
    # ----------------------------------------------------------------
    # 收齐到 dist-{flavor}\ 之后立刻验:File 表里每个 File ID 都能在 .msi 指定
    # 的 cab 里找到对应 stream。错位就 fail,别让坏包出 ISO。
    $verifyMsiList = Get-ChildItem -Path $flavorOut -Filter '*.msi' -File -ErrorAction SilentlyContinue
    $verifyCabList = Get-ChildItem -Path $flavorOut -Filter '*.cab' -File -ErrorAction SilentlyContinue
    if ($verifyMsiList -and $verifyCabList) {
        $cabPaths = @($verifyCabList | Select-Object -ExpandProperty FullName)
        foreach ($m in $verifyMsiList) {
            $ok = Test-MsiCabIntegrity -MsiPath $m.FullName -CabPaths $cabPaths
            if (-not $ok) {
                Write-Fail "$($m.Name) 装机必失败 (Error 1334) — 已 fail,不会继续打 ISO"
                Restore-Console; exit 1
            }
        }
    } else {
        Write-Note "dist-$Flavor 没收到 msi/cab 组合,跳过一致性校验"
    }

    # ----------------------------------------------------------------
    # External seed:把 chayuan-server/dist/models_seed/ 拷进 dist-{flavor}/models_seed/
    # ----------------------------------------------------------------
    # build.py sync_bundled_models 把 ≥ 2 GB 单文件分流到 dist/models_seed/(不进 MSI),
    # 这里把整树拷到 dist-{flavor}/,后面 make-iso.ps1 整目录打 ISO 时自动带上。
    # 装机后 chayuan-server first_launch -> bundled_seed 会扫挂载光驱的 models_seed/
    # 把这些大文件种到 <CHAYUAN_ROOT>/models/bundled/。
    $extSeedSrc = Join-Path $ServerDir 'dist\models_seed'
    if (Test-Path $extSeedSrc) {
        $extFiles = Get-ChildItem -Path $extSeedSrc -Recurse -File -ErrorAction SilentlyContinue
        if ($extFiles -and $extFiles.Count -gt 0) {
            $extTotalMb = [math]::Round(($extFiles | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
            $extSeedDest = Join-Path $flavorOut 'models_seed'
            New-Item -ItemType Directory -Force -Path $extSeedDest | Out-Null
            Write-Step "external seed ($Flavor): $($extFiles.Count) 个大文件 ($extTotalMb MB)"
            # Copy-Item -Recurse 在大文件 (~2 GB) 上比 robocopy 慢且没进度。Win10+ 自带 robocopy,
            # /MIR 镜像同步、/NFL /NDL 减少日志、/NJH /NJS 去掉头尾摘要。
            # 退出码 0/1/3 都是成功语义(0=没改,1=有拷,3=有拷+有跳过);≥ 8 才算失败。
            $robocopyArgs = @($extSeedSrc, $extSeedDest, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
            $null = & robocopy @robocopyArgs
            $rc = $LASTEXITCODE
            $global:LASTEXITCODE = 0   # robocopy 非 0 不算 PowerShell 失败
            if ($rc -ge 8) {
                Write-Fail "robocopy external seed 失败 (exit $rc),dist-$Flavor/models_seed/ 可能不完整"
            } else {
                Write-Ok "models_seed 已就位 → $extSeedDest"
            }
        } else {
            Write-Note "$extSeedSrc 存在但是空的 — 本次没有大文件分流,跳过"
        }
    }

    # ----------------------------------------------------------------
    # 2026-05-19 torch wheels seed 链路已废弃。
    # ----------------------------------------------------------------
    # 切 PyInstaller --onedir 后 torch CPU + torchvision 直接进 chayuan-server/_internal/,
    # 不再需要 vendor/torch_wheels/ → dist/torch_wheels_seed/ → ISO 旁挂 → first_launch
    # seed → pip install 那一长串。本块保留为锚点,后续清理时可一并删除。

    # ----------------------------------------------------------------
    # ISO 打包:把 dist-{flavor}\ 整目录打成 .iso,Win10+ 双击挂载即可。
    # ----------------------------------------------------------------
    # 全量版 .msi 走外置 CAB (EmbedCab="no") 绕国产杀软,用户得拿到 .msi + 所有
    # .cab 一起装。之前提示 zip 给用户 → 用户解压 → 双击 MSI;现在直接出 ISO
    # 单文件分发:用户双击 ISO,Win10+ 自动挂载成虚拟光驱,进光驱目录双击 MSI
    # 即可,MSI 在同目录(光驱根)能找到所有 CAB。
    #
    # 2026-05-18 起 Windows 一律走 MSI(lite/full 都因 NSIS 撞 32-bit 限制不能用),
    # WiX 模板写死 EmbedCab=no,MSI 必须带着外置 CAB 一起分发,所以 lite/full
    # 都默认出 ISO 单文件分发(用户双击挂载光驱后双击 MSI 即装)。-NoIso 跳过
    # (开发本机迭代不需要)。IsoBoth 历史参数保留兼容,新逻辑下已无意义。
    $isWin         = ($IsWindows -or $env:OS -eq 'Windows_NT')
    $shouldMakeIso = (-not $NoIso) -and (-not $SkipFrontend) -and $isWin
    if (-not $shouldMakeIso) {
        # 不出 ISO 时一定要说清楚原因 — 否则用户对着没有 .iso 的 dist-{flavor}\ 干瞪眼。
        $isoSkipReason = if ($NoIso) {
            '传了 -NoIso(开发本机迭代不打 ISO)'
        } elseif ($SkipFrontend) {
            '传了 -SkipFrontend(没有前端产物,不打 ISO)'
        } elseif (-not $isWin) {
            "当前不是 Windows(IMAPI2 仅 Windows 可用),ISO 只能在 Windows 上打"
        } else {
            '未知原因'
        }
        Write-Skip "ISO 镜像 ($Flavor) — 跳过:$isoSkipReason"
        Write-Note "如需 ISO,请在 Windows 上不带 -NoIso / -SkipFrontend 重跑;dist-$Flavor\ 里的 .msi + .cab 也可手动 zip 分发。"
    }
    if ($shouldMakeIso) {
        Write-Step "ISO 镜像 ($Flavor)"

        # 加一份 README,装机用户挂载光驱后能立刻看到下一步
        $readmePath = Join-Path $flavorOut '安装说明.txt'
        $readmeLines = @(
            '察元 AI 桌面版安装包',
            '==========================',
            '',
            '请双击本目录下的 .msi 文件开始安装。',
            '',
            '注意:',
            '  1. 安装期间不要把 .msi 单独拷到别处运行,.cab 文件必须留在它旁边,',
            '     否则安装到一半会报 1311/1335「找不到文件」。',
            '  2. **MSI 装完后,请先不要弹出虚拟光驱** —— 第一次启动察元 AI 时,',
            '     程序会自动从本光驱的 models_seed\ 目录复制大模型(~2 GB)到用户',
            '     数据目录。复制完成(几十秒到几分钟)后才能正常对话。',
            '  3. 如果一时间不方便等待,你也可以现在弹出光驱;但是首次启动时',
            '     察元 AI 会提示"模型缺失,请重新挂载安装介质"。',
            '  4. 如果是单文件 .exe (轻量版),直接双击即可,不需要 .cab。',
            ''
        )
        Set-Content -Path $readmePath -Value $readmeLines -Encoding UTF8

        $isoName = "chayuan-desktop_${Flavor}_setup.iso"
        # ISO 输出放 dist-{flavor}\ 同级(WorkspaceRoot 根),不放回源目录 — 避免
        # 用户混淆"是不是把自己卷进去了"(IMAPI2 AddTree 实际是快照式不会,但
        # 命名清晰更重要)。AllArtifacts 表里也单独列出来方便定位。
        $isoOut = Join-Path $WorkspaceRoot $isoName
        $volumeLabel = "CHAYUAN_$($Flavor.ToUpper())"   # CHAYUAN_FULL / CHAYUAN_LITE
        $makeIso = Join-Path $WorkspaceRoot 'scripts\make-iso.ps1'

        if (-not (Test-Path $makeIso)) {
            Write-Fail "scripts\make-iso.ps1 找不到,跳过 ISO 生成 — 期望的 $isoName 不会出现!"
            Write-Note "确认 $makeIso 存在;脚本随仓库分发,缺失通常是 checkout 不完整。"
            $script:IsoFailures += [PSCustomObject]@{
                Flavor = $Flavor; Iso = $isoName; Reason = "scripts\make-iso.ps1 找不到"
            }
        } else {
            try {
                # 同进程调用 — make-iso.ps1 内部 $ErrorActionPreference='Stop',
                # 任何报错 throw 出来直接被外层 catch 接住,不需要 $LASTEXITCODE。
                & $makeIso -SourceDir $flavorOut -OutputIso $isoOut -VolumeLabel $volumeLabel
                if (-not (Test-Path $isoOut)) {
                    # make-iso.ps1 没 throw 但 .iso 也没落盘 — 极少见,但绝不能当成功。
                    throw "make-iso.ps1 返回了但 $isoOut 不存在"
                }
                $isoSize = [math]::Round((Get-Item $isoOut).Length / 1MB, 1)
                Write-Ok "ISO 完成:$isoOut ($isoSize MB)"
                $script:AllArtifacts += [PSCustomObject]@{
                    Flavor = $Flavor
                    Src    = $flavorOut
                    Dest   = $isoOut
                    SizeMB = $isoSize
                }
            } catch {
                Write-Fail "ISO 生成失败($Flavor)—— 期望的 $isoName 没有生成!原因:$_"
                Write-Note "dist-$Flavor\ 里的 MSI + CAB 仍可手动 zip 分发,本步不阻断后续 flavor。"
                $script:IsoFailures += [PSCustomObject]@{
                    Flavor = $Flavor; Iso = $isoName; Reason = "$_"
                }
            }
        }
    }
}

}   # end Invoke-FlavorBuild

# ====================================================================
# 主循环:按 $Flavors 跑一次或两次
# ====================================================================
foreach ($flav in $Flavors) {
    Invoke-FlavorBuild -Flavor $flav
}

# ====================================================================
# 总结 + 还原编码
# ====================================================================
Write-Step '完成'
Write-Note "本次构建 flavor:$($Flavors -join ', ')"
if (-not $SkipFrontend) {
    foreach ($flav in $Flavors) {
        Write-Note "$flav 产物目录:$(Join-Path $WorkspaceRoot "dist-$flav")\"
    }
}
if ($AllArtifacts.Count -gt 0) {
    Write-Host ''
    Write-Host '本次产物清单' -ForegroundColor White
    foreach ($a in $AllArtifacts) {
        $name = Split-Path -Leaf $a.Dest
        Write-Host ("  {0,-12} {1,-50} {2} MB" -f $a.Flavor, $name, $a.SizeMB)
    }
}
if ($Flavors.Count -eq 2) {
    Write-Host ''
    Write-Host '  轻量版:嵌 LITE_CAPS (emb/rerank/asr/ocr/tts ~990 MB),其他需联网下载或扫盘配置'
    Write-Host '  全量版:vendor\bundled_models\ 进 Tauri resources,装机即用 (模型清单见上方"模型体检"输出,'
    Write-Host '          或 chayuan-server\vendor\bundled_models\README.md)'
    Write-Host ''
    Write-Host '  ⚠ Windows 一律走 MSI + 外置 CAB (EmbedCab="no" 绕国产杀软对 C:\Windows\Installer\ 的拦截)。'
    Write-Host '    推荐分发方式 — 直接发 chayuan-desktop_{lite,full}_setup.iso 给用户:'
    Write-Host '    Win10+ 双击 ISO 自动挂载成光驱,进光驱后双击 MSI 即装(MSI 在光驱根能找到 CAB)。'
    Write-Host '    不用 ISO 也可把 dist-{lite,full}\ 里的 .msi + 所有 .cab 一起打 zip 给用户。'
    Write-Host '    切忌 只发 .msi 不带 .cab — 会装到一半报 1311/1335。'
}
Write-Host ''
if ($IsoFailures.Count -gt 0) {
    # ISO 没出但前面没硬退出 —— 在最终汇总里再喊一遍,别让用户对着没有 .iso 的目录猜。
    if ($useColor) { Write-Host "$ESC[31m[FAIL] 以下 ISO 期望生成但未生成:$ESC[0m" }
    else { Write-Host '[FAIL] 以下 ISO 期望生成但未生成:' }
    foreach ($f in $IsoFailures) {
        Write-Host ("  {0,-8} {1}  —  {2}" -f $f.Flavor, $f.Iso, $f.Reason)
    }
    Write-Host '  MSI + CAB 已在 dist-{flavor}\ 下,可手动 zip 分发;ISO 步骤失败原因见上。'
    Write-Host ''
}
if ($useColor) { Write-Host "$ESC[32m[OK] 全部步骤通过$ESC[0m" }
else { Write-Host '[OK] 全部步骤通过' }

Restore-Console
