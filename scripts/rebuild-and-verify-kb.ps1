<#
.SYNOPSIS
    One-shot: rebuild PyInstaller -> redeploy to installed dir -> restart
    chayuan-server -> run E2E KB smoke -> aggregate all evidence into a
    single UTF-8 markdown report you paste back.

.DESCRIPTION
    Frozen vs dev KB-retrieval debug loop has been tedious: stop processes,
    rebuild, copy past file locks / Program Files perms, restart, find
    fresh logs, run smoke, grep raw stderr for runtime hooks, etc.
    This script automates the whole loop and produces one .md with:
      - PyInstaller build tail
      - install-dir overwrite result (robocopy)
      - chayuan-server raw stdout/stderr (catches runtime-hook lines like
        [tiktoken-rthook], [mp-freeze], which never reach the loguru
        chayuan.log)
      - chayuan.log tail post-restart
      - e2e-frozen-kb-smoke output

    Source is 100% ASCII per CLAUDE.md "PowerShell encoding rules".
    Output: $env:TEMP\chayuan-rebuild-verify-<ts>.md (UTF-8 BOM).

.PARAMETER ServerRepo
    chayuan-server poetry repo root.
    Default: D:\code\chayuan\chayuan-desktop\chayuan-server

.PARAMETER InstallDir
    Target install location to overwrite.
    Default: C:\Program Files\chayuan-test\chayuan-server

.PARAMETER SmokeScript
    Path to e2e-frozen-kb-smoke.ps1.
    Default: <ServerRepo>\..\scripts\e2e-frozen-kb-smoke.ps1

.PARAMETER SkipBuild
    If set, skip PyInstaller and just do the kill-copy-restart-verify loop.
    Useful when you only changed PS scripts.

.PARAMETER Port
    chayuan-server API port. Default 62581.

.EXAMPLE
    # Run as Administrator (Program Files copy needs it):
    Start-Process powershell -Verb RunAs
    .\scripts\rebuild-and-verify-kb.ps1

    # Or skip the build (just restart + verify):
    .\scripts\rebuild-and-verify-kb.ps1 -SkipBuild
#>
param(
    [string]$ServerRepo = "D:\code\chayuan\chayuan-desktop\chayuan-server",
    [string]$InstallDir = "C:\Program Files\chayuan-test\chayuan-server",
    [string]$SmokeScript = "",
    [int]   $Port       = 62581,
    [switch]$SkipBuild
)

# UTF-8 console end-to-end
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ErrorActionPreference = "Continue"

function Red    { param([string]$m) Write-Host $m -ForegroundColor Red }
function Green  { param([string]$m) Write-Host $m -ForegroundColor Green }
function Yellow { param([string]$m) Write-Host $m -ForegroundColor Yellow }
function Info   { param([string]$m) Write-Host "[rebuild-verify] $m" -ForegroundColor Cyan }

# Pause-And-Exit:every early-exit must use this so admin-elevated PS
# windows don't close before the user reads the failure / report path.
# Skip via $env:CHAYUAN_REBUILD_VERIFY_NOPAUSE=1 in CI / scripted use.
function Pause-And-Exit {
    param([int]$Code = 0, [string]$Hint = "")
    if ($Hint) { Write-Host $Hint -ForegroundColor Yellow }
    if (-not $env:CHAYUAN_REBUILD_VERIFY_NOPAUSE) {
        Write-Host ""
        Write-Host "Exit code: $Code. Press Enter to close (Ctrl+C to keep window)..." -ForegroundColor Yellow
        try { $null = Read-Host } catch { Start-Sleep 30 }
    }
    exit $Code
}

$ts  = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $env:TEMP "chayuan-rebuild-verify-$ts.md"
$utf8Bom = [System.Text.UTF8Encoding]::new($true)
[System.IO.File]::WriteAllText($out, "# Chayuan rebuild + verify run $ts`n`n", $utf8Bom)

function Append-Section { param([string]$T, [string]$B)
    [System.IO.File]::AppendAllText($out, "`n## $T`n``````n$B`n```````n", $utf8Bom)
    # Also mirror to console so the user sees everything live even when the
    # script crashes mid-way (sections after the crash never reach .md).
    Write-Host ""
    Write-Host "================================================" -ForegroundColor DarkGray
    Write-Host "## $T" -ForegroundColor Cyan
    Write-Host "------------------------------------------------" -ForegroundColor DarkGray
    Write-Host $B
    Write-Host "================================================" -ForegroundColor DarkGray
}

# ---- 0. preflight ----
if (-not $SmokeScript) {
    $SmokeScript = Join-Path (Split-Path $ServerRepo -Parent) "scripts\e2e-frozen-kb-smoke.ps1"
}
if (-not (Test-Path $ServerRepo)) {
    Red "[FAIL] ServerRepo not found: $ServerRepo"
    Pause-And-Exit -Code 2
}
if (-not (Test-Path $SmokeScript)) {
    Red "[FAIL] SmokeScript not found: $SmokeScript"
    Pause-And-Exit -Code 2
}

# Admin check (Program Files needs elevation)
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($InstallDir.StartsWith("C:\Program Files") -and -not $isAdmin) {
    Red "[FAIL] InstallDir is under Program Files but PowerShell is not elevated."
    Red "       Open as Administrator: Start-Process powershell -Verb RunAs"
    Red "       Or use a non-Program-Files install dir via -InstallDir <path>."
    Pause-And-Exit -Code 2
}

Info "ServerRepo : $ServerRepo"
Info "InstallDir : $InstallDir"
Info "SmokeScript: $SmokeScript"
Info "Report out : $out"
Info "Admin      : $isAdmin"
Info "SkipBuild  : $SkipBuild"
Info ""

Append-Section "0. preflight" @"
ServerRepo   : $ServerRepo
InstallDir   : $InstallDir
SmokeScript  : $SmokeScript
Port         : $Port
SkipBuild    : $SkipBuild
Admin        : $isAdmin
Host         : $(hostname)
PSVersion    : $($PSVersionTable.PSVersion)
"@

# ---- 1. PyInstaller build ----
$buildLog = Join-Path $env:TEMP "chayuan-rebuild-verify-$ts-build.log"
if ($SkipBuild) {
    Yellow "[skip] PyInstaller build (SkipBuild set)"
    Append-Section "1. PyInstaller build" "(skipped via -SkipBuild)"
} else {
    Info "Step 1: PyInstaller build (poetry run python packaging\pyinstaller\build.py --offline)"
    Push-Location $ServerRepo
    try {
        & poetry run python packaging\pyinstaller\build.py --offline `
            2>&1 |
            Tee-Object -FilePath $buildLog |
            Out-Null
        $buildRc = $LASTEXITCODE
    } catch {
        $buildRc = -1
        $_.Exception.Message | Out-File -Append $buildLog -Encoding utf8
    } finally {
        Pop-Location
    }
    if ($buildRc -ne 0) {
        Red "[FAIL] PyInstaller build exit=$buildRc"
        Append-Section "1. PyInstaller build (FAIL exit=$buildRc, last 80 lines)" `
            ((Get-Content $buildLog -Tail 80 -Encoding utf8) -join "`n")
        Write-Host "Full build log: $buildLog"
        Write-Host "Aggregated report so far: $out"
        Pause-And-Exit -Code 1
    }
    Green "[OK] PyInstaller build done"
    Append-Section "1. PyInstaller build (OK, last 40 lines)" `
        ((Get-Content $buildLog -Tail 40 -Encoding utf8) -join "`n")
}

# Verify build artifact exists
$builtExe = Join-Path $ServerRepo "dist\chayuan-server\chayuan-server.exe"
if (-not (Test-Path $builtExe)) {
    Red "[FAIL] Built exe not found: $builtExe"
    Append-Section "1b. built exe check" "FAIL: $builtExe not found"
    Pause-And-Exit -Code 1
}
$buildMtime = (Get-Item $builtExe).LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
Info "built exe: $builtExe ($buildMtime)"

# ---- 2. Stop running chayuan processes (robust) ----
Info "Step 2: stop running chayuan / sidecar processes"
$procPattern = '^(chayuan-server|chayuan-desktop|llama-server|whisper-server)$'
$pre = Get-Process | Where-Object { $_.ProcessName -match $procPattern } | Select Id, ProcessName
if ($pre) {
    Yellow "running before kill:"
    $pre | Format-Table -AutoSize | Out-String | Write-Host
    $pre | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep 3
}
$post = Get-Process | Where-Object { $_.ProcessName -match $procPattern } | Select Id, ProcessName
if ($post) {
    Yellow "still alive after first kill, hammering harder:"
    $post | ForEach-Object {
        try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {}
    }
    Start-Sleep 3
}
$still = Get-Process | Where-Object { $_.ProcessName -match $procPattern }
$killSummary = if ($still) {
    "WARN: $($still.Count) chayuan process(es) still alive (may lock files):`n$(($still | Select Id,ProcessName | Format-Table | Out-String))"
} else {
    "OK: no chayuan processes running"
}
Info $killSummary
Append-Section "2. stop running processes" $killSummary

# ---- 2a. Kill anything listening on chayuan's well-known ports ----
# Process-name kill (Step 2) misses chayuan-server respawned by Tauri
# supervisor or processes started under non-standard names. Also kill by
# port -- if anything is listening on chayuan's ports, kill it.
# Without this, Step 4's chayuan-server spawn hits _preflight_port_check
# which WARN-aborts startup when ports are already bound -> sidecars never
# spawn -> Step 5b hangs forever waiting for 62583.
Info "Step 2a: kill anything listening on chayuan ports (62581/62582/62583/62584/62585/8502/18380)"
$chayuanPorts = 62581, 62582, 62583, 62584, 62585, 8502, 18380
$portPids = Get-NetTCPConnection -LocalPort $chayuanPorts -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
if ($portPids) {
    foreach ($id in $portPids) {
        $p = Get-Process -Id $id -ErrorAction SilentlyContinue
        Yellow "  killing PID $id ($($p.ProcessName)) holding port"
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep 5
} else {
    Info "  no process listening on chayuan ports"
}
$stillBound = Get-NetTCPConnection -LocalPort $chayuanPorts -State Listen -ErrorAction SilentlyContinue
$portSummary = if ($stillBound) {
    "WARN: ports still bound after kill:`n" +
    (($stillBound | Select LocalPort, OwningProcess | Format-Table -AutoSize | Out-String))
} else {
    "OK: all chayuan ports free"
}
Info $portSummary
Append-Section "2a. ports cleared" $portSummary

# ---- 2b. Clear stale runtime.json cache ----
# Without this, chayuan-server reads runtime.json on startup and sees stale
# "ready PID=X" entries from a previous run. After [reap] kills those PIDs,
# auto-start STILL reports them as ready (without respawning). Net effect:
# sidecar is dead but chayuan-server thinks it's alive -> embed_documents
# silently fails -> 0 vectors -> 4141-byte index.faiss. Same root cause
# debugged for hours today. Just delete the stale state files; chayuan-server
# rebuilds them on next sidecar spawn.
Info "Step 2b: clear stale runtime state files (force fresh sidecar spawn)"
$staleFiles = @()
$candidates = @(
    "D:\nn\runtime.json",
    "D:\nn\.chayuan_runtime.json",
    "$env:APPDATA\chayuan\runtime.json",
    "$env:APPDATA\chayuan\.chayuan_runtime.json"
)
foreach ($p in $candidates) {
    if (Test-Path $p) {
        $staleFiles += $p
        Remove-Item $p -Force -ErrorAction SilentlyContinue
    }
}
if ($staleFiles) {
    Yellow "deleted stale runtime state: $($staleFiles -join '; ')"
} else {
    Info "no stale runtime state files found"
}
Append-Section "2b. cleared stale runtime state" `
    ($(if ($staleFiles) { ($staleFiles -join "`n") } else { "(none found)" }))

# ---- 3. Robocopy install dir ----
Info "Step 3: robocopy dist -> $InstallDir"
$rcLog = Join-Path $env:TEMP "chayuan-rebuild-verify-$ts-robocopy.log"
$rcSrc = Join-Path $ServerRepo "dist\chayuan-server"
& robocopy $rcSrc $InstallDir /MIR /R:2 /W:2 /MT:8 /NFL /NDL /NJH /NJS /NC /NS /NP /LOG:$rcLog 2>&1 | Out-Null
$rcExit = $LASTEXITCODE
# robocopy exit codes: 0-7 success, 8+ failure
if ($rcExit -ge 8) {
    Red "[FAIL] robocopy exit=$rcExit (full log: $rcLog)"
    Append-Section "3. robocopy (FAIL exit=$rcExit)" ((Get-Content $rcLog -Tail 60 -Encoding ascii) -join "`n")
    Pause-And-Exit -Code 1
}
Green "[OK] robocopy exit=$rcExit (0-7 are success)"
$installedExe = Join-Path $InstallDir "chayuan-server.exe"
if (Test-Path $installedExe) {
    $instMtime = (Get-Item $installedExe).LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
    Info "installed exe mtime: $instMtime  (was $buildMtime in dist)"
    if ($instMtime -ne $buildMtime) {
        Yellow "WARN: installed mtime != built mtime; copy may not have replaced this file (locked?)"
    }
}
Append-Section "3. robocopy" @"
exit=$rcExit (0-7 success, 8+ fail)
built mtime    : $buildMtime
installed mtime: $instMtime
robocopy log   : $rcLog
"@

# ---- 4. Start chayuan-server with stderr capture (PS-native, more reliable) ----
# ! Earlier impl used ProcessStartInfo + add_OutputDataReceived. PS 5.1's
# event handler variable binding ($EventArgs vs $Event vs $_) is finicky
# and silently swallows exceptions; users reported the script dying after
# Section 3 with no Section 4-7 written. Switch to Start-Process which uses
# native PS file redirection -- handles stderr capture in a separate file,
# but it works reliably across PS 5.1 / 7+.
Info "Step 4: start chayuan-server (Start-Process + redirected stderr)"
$rawOut = Join-Path $env:TEMP "chayuan-rebuild-verify-$ts-rawstdout.log"
$rawErr = Join-Path $env:TEMP "chayuan-rebuild-verify-$ts-rawstderr.log"
# rawLog kept for backward-compat with later Append-Section call; point at
# stderr (runtime hooks write to sys.stderr).
$rawLog = $rawErr
$proc = Start-Process -FilePath $installedExe `
                      -ArgumentList "start","-a","--single-machine" `
                      -RedirectStandardOutput $rawOut `
                      -RedirectStandardError  $rawErr `
                      -WindowStyle Hidden `
                      -PassThru
$serverPid = $proc.Id
Info "spawned PID=$serverPid"
Info "  stdout -> $rawOut"
Info "  stderr -> $rawErr  (runtime hooks land here)"

# Wait for API
Info "Step 5: wait for API /runtime/diagnose ..."
$apiUp = $false
for ($i = 1; $i -le 90; $i++) {
    try {
        $null = Invoke-RestMethod "http://127.0.0.1:$Port/runtime/diagnose" -TimeoutSec 2 -ErrorAction Stop
        $apiUp = $true; Info "API up after ${i}s"; break
    } catch { Start-Sleep 1 }
}
if (-not $apiUp) {
    Red "[FAIL] API never came up"
    Append-Section "4-5. server restart (FAIL: API down)" `
        ((Get-Content $rawLog -Tail 80 -Encoding utf8) -join "`n")
    Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue
    Pause-And-Exit -Code 1
}

# Wait for embedding sidecar
Info "Step 5b: wait for embedding sidecar @ 62583 ..."
$embedUp = $false
for ($i = 1; $i -le 120; $i++) {
    try {
        $null = Invoke-RestMethod "http://127.0.0.1:62583/v1/models" -TimeoutSec 2 -ErrorAction Stop
        $embedUp = $true; Info "embedding sidecar up after ${i}s"; break
    } catch { Start-Sleep 1 }
}
if (-not $embedUp) {
    Yellow "WARN: embedding sidecar didn't reach /v1/models in 120s, smoke will still try"
}

# ---- 6. Run smoke ----
Info "Step 6: run e2e-frozen-kb-smoke.ps1"
$smokeLog = Join-Path $env:TEMP "chayuan-rebuild-verify-$ts-smoke.log"
& powershell -NoProfile -ExecutionPolicy Bypass -File $SmokeScript *> $smokeLog
$smokeRc = $LASTEXITCODE
if ($smokeRc -eq 0) {
    Green "[OK] smoke PASSED"
} else {
    Red "[FAIL] smoke exit=$smokeRc"
}

# ---- 7. Aggregate logs ----
Info "Step 7: aggregating evidence into $out"

# raw stderr (runtime hooks)
$rawTail = if (Test-Path $rawLog) { Get-Content $rawLog -Encoding utf8 } else { @() }
$hookLines = $rawTail | Where-Object { $_ -match 'tiktoken-rthook|mp-freeze|chayuan-rthook|distlib-finder' }
Append-Section "4. chayuan-server raw stderr -- runtime hook lines (key evidence)" `
    ($(if ($hookLines) { ($hookLines -join "`n") } else { "(no runtime-hook lines found in raw stderr -- hooks may not have fired)" }))

Append-Section "4b. chayuan-server raw stderr -- tail 100" `
    ($(if ($rawTail) { (($rawTail | Select-Object -Last 100) -join "`n") } else { "(empty)" }))

# loguru chayuan.log post-restart
$diagRoot = (Invoke-RestMethod "http://127.0.0.1:$Port/runtime/diagnose" -TimeoutSec 5).data.chayuan_root
$logCandidates = @(
    (Join-Path $diagRoot "data\logs\chayuan.log"),
    (Join-Path $diagRoot "logs\chayuan.log"),
    (Join-Path $diagRoot "data\logs\server.log"),
    (Join-Path $diagRoot "logs\server.log")
) | Where-Object { Test-Path $_ }
$serverLog = $logCandidates | Select-Object -First 1
if ($serverLog) {
    Append-Section "5. loguru chayuan.log tail 200 (from $serverLog)" `
        ((Get-Content $serverLog -Tail 200 -Encoding utf8) -join "`n")
} else {
    Append-Section "5. loguru chayuan.log" "(not found; scanned $($logCandidates -join '; '))"
}

# smoke output
Append-Section "6. e2e smoke output (exit=$smokeRc)" `
    ((Get-Content $smokeLog -Encoding utf8) -join "`n")

# defensive guard trigger? grep for our new RuntimeError message
$guardHit = if ($serverLog) {
    Get-Content $serverLog -Encoding utf8 | Select-String "embed_documents returned"
} else { $null }
if ($guardHit) {
    Append-Section "7. defensive guard TRIGGERED (commit 253acbe)" `
        (($guardHit | ForEach-Object { $_.Line }) -join "`n")
} else {
    Append-Section "7. defensive guard" "(no 'embed_documents returned N vectors for M texts' lines -- guard either didn't fire, or build is pre-253acbe)"
}

# Cleanup chayuan-server
if (Get-Process -Id $serverPid -ErrorAction SilentlyContinue) {
    Info "killing chayuan-server PID=$serverPid"
    Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Green "================================================"
Green "DONE. Aggregated report:"
Green "  $out"
Green ""
Green "View / paste:"
Green "  notepad '$out'"
Green "  Get-Content '$out' -Encoding utf8"
Green "================================================"

# ! Pause before exit. When run via `Start-Process powershell -Verb RunAs`
# or a double-click .ps1, the host window closes immediately on exit, so
# the user never sees the report path printed above. Pause keeps the
# window open until user acknowledges. To skip the pause in CI / scripted
# invocations, set $env:CHAYUAN_REBUILD_VERIFY_NOPAUSE=1.
if ($smokeRc -ne 0) { Pause-And-Exit -Code 1 } else { Pause-And-Exit -Code 0 }
