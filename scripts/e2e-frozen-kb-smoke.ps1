<#
.SYNOPSIS
    E2E smoke test for the frozen chayuan-desktop KB pipeline on Windows.

.DESCRIPTION
    Mirror of scripts/e2e-frozen-kb-smoke.sh, native PowerShell so users
    without Git Bash can run it.

    All source ASCII / English to dodge the PS 5.1 GBK-decoding-UTF-8 .ps1
    parse trap (see commit 942819f for the history).

    Flow:
      1. Auto-detect installed chayuan-server.exe
      2. Spawn it pointing at an isolated $env:CHAYUAN_ROOT
      3. Wait for /runtime/diagnose and embedding sidecar (62583)
      4. POST /knowledge_base/create_knowledge_base
      5. Upload a .txt fixture file via /knowledge_base/upload_docs
      6. Assert SQLite docs_count > 0 (chunking ok)
      7. Assert on-disk vector_store/<embed>/index.faiss > 9000 bytes
      8. Query /knowledge_base/search_docs with the planted secret
      9. Assert hits > 0 AND retrieval_path includes vector or hybrid
     10. Bonus semantic-only query

    Exit codes:
      0 = PASS
      1 = FAIL (server log tail will be printed to console)
      2 = couldn't start (no installed binary / spawn failed)

.PARAMETER Exe
    Explicit path to chayuan-server.exe. Default: auto-detect installed app.

.PARAMETER Root
    Isolated CHAYUAN_ROOT for this test. Default: $env:TEMP\chayuan-e2e-smoke-<ts>.

.PARAMETER Port
    chayuan-server API port. Default 62581.

.PARAMETER KeepServer
    Don't kill the spawned chayuan-server at the end (for manual inspection).

.EXAMPLE
    .\scripts\e2e-frozen-kb-smoke.ps1
    .\scripts\e2e-frozen-kb-smoke.ps1 -Exe "C:\Program Files\Chayuan\chayuan-server\chayuan-server.exe"
    .\scripts\e2e-frozen-kb-smoke.ps1 -Root D:\tmp\chayuan-e2e -KeepServer
#>
param(
    [string]$Exe = "",
    [string]$Root = "",
    [int]$Port = 62581,
    [switch]$KeepServer
)

# UTF-8 console end-to-end; otherwise child stdout / Get-Content of log gets
# GBK-decoded and asserts on Chinese in the log lines silently mis-fire.
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ErrorActionPreference = "Continue"  # keep going on errors; we report each step
$EmbedPort = 62583

function Red    { param([string]$m) Write-Host $m -ForegroundColor Red }
function Green  { param([string]$m) Write-Host $m -ForegroundColor Green }
function Yellow { param([string]$m) Write-Host $m -ForegroundColor Yellow }

# --- 1. Detect installed chayuan-server.exe -----------------------------
function Detect-Exe {
    if ($script:Exe) {
        if (Test-Path $script:Exe) { return $script:Exe }
        return $null
    }
    $candidates = @(
        "C:\Program Files\Chayuan\chayuan-server\chayuan-server.exe",
        "$env:LOCALAPPDATA\Programs\Chayuan\chayuan-server\chayuan-server.exe",
        "C:\Program Files\chayuan-test\chayuan-server\chayuan-server.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$ExePath = Detect-Exe
if (-not $ExePath) {
    Red "[FAIL] No installed chayuan-server.exe found."
    Red "       Install the desktop app first, or pass -Exe <path>."
    exit 2
}

if (-not $Root) {
    $Root = Join-Path $env:TEMP ("chayuan-e2e-smoke-{0}" -f (Get-Date -Format "yyyyMMddHHmmss"))
}
New-Item -ItemType Directory -Force -Path $Root | Out-Null

$LogFile  = Join-Path $Root "smoke.log"
$KB       = "e2e_smoke_$([System.IO.Path]::GetRandomFileName().Substring(0,8))"
$SecretRaw = "$KB-$(Get-Random)"
$Sha = [System.Security.Cryptography.SHA1]::Create()
$Bytes = [System.Text.Encoding]::UTF8.GetBytes($SecretRaw)
$Secret = ([BitConverter]::ToString($Sha.ComputeHash($Bytes)).Replace("-", "")).Substring(0, 16)
$TestFile = Join-Path $Root "api_key.txt"

@"
Chayuan E2E frozen-KB smoke test fixture.

The DeepSeek apikey value used by the verification suite is:
sk-$Secret

That secret above should be retrievable by querying the KB after upload.
If you cannot find it via /knowledge_base/search_docs with retrieval_path
including "vector" or "hybrid", the frozen build's KB pipeline is broken.
"@ | Set-Content -Path $TestFile -Encoding utf8

Write-Host "================================================"
Write-Host "[smoke] exe          : $ExePath"
Write-Host "[smoke] CHAYUAN_ROOT : $Root (isolated)"
Write-Host "[smoke] test KB      : $KB"
Write-Host "[smoke] secret token : sk-$Secret"
Write-Host "[smoke] log file     : $LogFile"
Write-Host "================================================"

# --- 2. Spawn chayuan-server -------------------------------------------
# ! Start-Process with $env: assignment is unreliable for env-var propagation
# on PS 5.1. Use ProcessStartInfo explicitly so CHAYUAN_ROOT definitely lands
# in the child env. Without this, chayuan-server falls back to the user's
# last `chayuan init` choice (often D:\nn or %APPDATA%\chayuan) and we end
# up testing against the user's REAL KB store -- not an isolated temp root.
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName               = $ExePath
$psi.Arguments              = "start -a --single-machine"
$psi.UseShellExecute        = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError  = $true
$psi.CreateNoWindow         = $true
$psi.WorkingDirectory       = $Root
# Copy parent env, then override CHAYUAN_ROOT
foreach ($k in [System.Environment]::GetEnvironmentVariables().Keys) {
    if (-not $psi.EnvironmentVariables.ContainsKey($k)) {
        $psi.EnvironmentVariables.Add($k, [System.Environment]::GetEnvironmentVariable($k))
    }
}
$psi.EnvironmentVariables["CHAYUAN_ROOT"] = $Root
$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
$logStream = [System.IO.StreamWriter]::new($LogFile, $false, [System.Text.UTF8Encoding]::new($true))
$proc.add_OutputDataReceived({ if ($EventArgs.Data) { $logStream.WriteLine($EventArgs.Data); $logStream.Flush() } })
$proc.add_ErrorDataReceived({  if ($EventArgs.Data) { $logStream.WriteLine("[STDERR] " + $EventArgs.Data); $logStream.Flush() } })
$null = $proc.Start()
$proc.BeginOutputReadLine()
$proc.BeginErrorReadLine()
$ServerPid = $proc.Id
Write-Host "[smoke] spawned chayuan-server PID=$ServerPid"
Write-Host "[smoke] requested CHAYUAN_ROOT=$Root (env-injected)"

$Fail = 0
function Cleanup-Server {
    if ($script:KeepServer) {
        Yellow "[smoke] -KeepServer set, leaving PID=$script:ServerPid running"
        Yellow "        Cleanup: Stop-Process -Id $script:ServerPid -Force; Remove-Item -Recurse '$script:Root'"
        return
    }
    if (Get-Process -Id $script:ServerPid -ErrorAction SilentlyContinue) {
        Write-Host "[smoke] killing server PID=$script:ServerPid"
        Stop-Process -Id $script:ServerPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

try {

    # --- 3. Wait for API ------------------------------------------------
    Write-Host "[smoke] waiting for http://127.0.0.1:$Port/runtime/diagnose ..."
    $apiUp = $false
    for ($i = 1; $i -le 90; $i++) {
        try {
            $null = Invoke-RestMethod "http://127.0.0.1:$Port/runtime/diagnose" -TimeoutSec 2 -ErrorAction Stop
            $apiUp = $true
            Write-Host "[smoke] API up after ${i}s"
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $apiUp) { Red "[FAIL] API never came up"; throw "api-not-up" }

    # --- 3b. ACTUAL chayuan_root (in case env override didn't stick) ----
    # chayuan-server precedence: env CHAYUAN_ROOT > last `chayuan init` choice.
    # If our env injection failed, the server uses the user's existing root.
    # We MUST use whatever the running server reports for on-disk checks.
    $ActualRoot = $Root
    try {
        $diag = Invoke-RestMethod "http://127.0.0.1:$Port/runtime/diagnose" -TimeoutSec 5
        $ActualRoot = $diag.data.chayuan_root
        if ($ActualRoot -ne $Root) {
            Yellow "[smoke] !! requested CHAYUAN_ROOT=$Root"
            Yellow "[smoke] !! ACTUAL    chayuan_root=$ActualRoot"
            Yellow "[smoke] !! env override DID NOT propagate to chayuan-server."
            Yellow "[smoke] !! On-disk checks will use the actual root above."
            Yellow "[smoke] !! Your real KB store may be polluted by this test KB '$KB'."
        } else {
            Write-Host "[smoke] actual chayuan_root = $ActualRoot (env override ok)"
        }
    } catch {
        Yellow "[smoke] could not query /runtime/diagnose for actual root: $($_.Exception.Message)"
    }

    # --- 4. Wait for embedding sidecar ----------------------------------
    Write-Host "[smoke] waiting for embedding sidecar @ 127.0.0.1:$EmbedPort ..."
    $embedUp = $false
    for ($i = 1; $i -le 120; $i++) {
        try {
            $null = Invoke-RestMethod "http://127.0.0.1:$EmbedPort/v1/models" -TimeoutSec 2 -ErrorAction Stop
            $embedUp = $true
            Write-Host "[smoke] embedding sidecar up after ${i}s"
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $embedUp) {
        Red "[FAIL] embedding sidecar @ $EmbedPort never became reachable"
        $Fail = 1
    }

    # --- 4b. Discover the actual embed model id from /v1/models --------
    # The platform registry uses full paths like `models/bundled/embedding/bge-m3`,
    # NOT the short `bge-m3`. Hardcoding the short name -> KB create 422
    # "embedding model 'bge-m3' unavailable". Auto-pick the first embed model the
    # server actually advertises so this script does not lie about the bug.
    Write-Host "[smoke] discovering embed model from /v1/models ..."
    $EmbedModel = "bge-m3"   # fallback
    try {
        $models = Invoke-RestMethod "http://127.0.0.1:$Port/v1/models" -TimeoutSec 10
        $embedEntry = $models.data | Where-Object { $_.model_type -eq "embed" -and $_.available } | Select-Object -First 1
        if ($embedEntry) {
            $EmbedModel = $embedEntry.id
            Write-Host "  -> using embed_model = $EmbedModel (platform=$($embedEntry.platform_name))"
        } else {
            Yellow "  -> no available embed model in /v1/models; falling back to '$EmbedModel'"
        }
    } catch {
        Yellow "  -> /v1/models query failed: $($_.Exception.Message); falling back to '$EmbedModel'"
    }

    # --- 5. Create KB ---------------------------------------------------
    Write-Host "[smoke] POST /knowledge_base/create_knowledge_base name=$KB"
    $body = @{
        knowledge_base_name = $KB
        vector_store_type   = "faiss"
        embed_model         = $EmbedModel
    } | ConvertTo-Json
    $createOk = $false
    try {
        $r = Invoke-RestMethod "http://127.0.0.1:$Port/knowledge_base/create_knowledge_base" `
            -Method POST -Body $body -ContentType "application/json" -TimeoutSec 30
        Write-Host "  -> $($r | ConvertTo-Json -Depth 3 -Compress)"
        if ($r.code -eq 200) { $createOk = $true }
    } catch {
        Red "[FAIL] KB creation: $($_.Exception.Message)"
    }
    if (-not $createOk) { $Fail = 1 }

    # --- 6. Upload file -------------------------------------------------
    # ! Invoke-RestMethod -Form is PS 7+ only. On PS 5.1 we hand-build the
    # multipart body. Test PS version and dispatch accordingly.
    Write-Host "[smoke] POST /knowledge_base/upload_docs file=$TestFile"
    try {
        $uploadUrl = "http://127.0.0.1:$Port/knowledge_base/upload_docs"
        if ($PSVersionTable.PSVersion.Major -ge 7) {
            $form = @{
                knowledge_base_name = $KB
                files               = Get-Item $TestFile
                override            = "true"
                to_vector_store     = "true"
            }
            $up = Invoke-RestMethod $uploadUrl -Method POST -Form $form -TimeoutSec 180
        } else {
            # PS 5.1 manual multipart
            $boundary  = [System.Guid]::NewGuid().ToString()
            $LF        = "`r`n"
            $fileBytes = [System.IO.File]::ReadAllBytes($TestFile)
            $fileName  = [System.IO.Path]::GetFileName($TestFile)
            # multipart body must be raw bytes -- mixing strings with bytes via
            # Encoding.UTF8 only works when the binary part is text. For .txt
            # it's fine; for binary uploads we'd need a binary writer.
            $bodyLines = @(
                "--$boundary",
                "Content-Disposition: form-data; name=`"knowledge_base_name`"",
                "",
                $KB,
                "--$boundary",
                "Content-Disposition: form-data; name=`"override`"",
                "",
                "true",
                "--$boundary",
                "Content-Disposition: form-data; name=`"to_vector_store`"",
                "",
                "true",
                "--$boundary",
                "Content-Disposition: form-data; name=`"files`"; filename=`"$fileName`"",
                "Content-Type: text/plain; charset=utf-8",
                "",
                [System.Text.Encoding]::UTF8.GetString($fileBytes),
                "--$boundary--",
                ""
            )
            $bodyStr = $bodyLines -join $LF
            $up = Invoke-RestMethod $uploadUrl -Method POST `
                    -Body $bodyStr `
                    -ContentType "multipart/form-data; boundary=$boundary" `
                    -TimeoutSec 180
        }
        $upJson = $up | ConvertTo-Json -Depth 3 -Compress
        if ($upJson.Length -gt 300) { $upJson = $upJson.Substring(0, 300) + "...[truncated]" }
        Write-Host "  -> $upJson"
    } catch {
        Red "[FAIL] upload_docs: $($_.Exception.Message)"
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            Red "       body: $($_.ErrorDetails.Message)"
        }
        $Fail = 1
    }

    Start-Sleep -Seconds 5

    # --- 7. docs_count check --------------------------------------------
    Write-Host "[smoke] checking list_files docs_count ..."
    $docsCount = 0
    try {
        $list = Invoke-RestMethod "http://127.0.0.1:$Port/knowledge_base/list_files?knowledge_base_name=$KB" -TimeoutSec 15
        foreach ($f in $list.data) { $docsCount += [int]($f.docs_count) }
    } catch {
        Red "[FAIL] list_files: $($_.Exception.Message)"
    }
    Write-Host "  -> total docs_count = $docsCount"
    if ($docsCount -eq 0) {
        Red "[FAIL] docs_count == 0 (chunking failed; check unstructured / text_splitter / tiktoken)"
        $Fail = 1
    }

    # --- 8. Vector store on-disk check ----------------------------------
    # Use $ActualRoot (what /runtime/diagnose said), not $Root, so we look
    # in the correct place even when env injection failed.
    $KbDir = Join-Path $ActualRoot "data\knowledge_base\$KB"
    Write-Host "[smoke] scanning vector_store at $KbDir"
    $faiss = Get-ChildItem $KbDir -Recurse -File -Filter "*.faiss" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($faiss) {
        Write-Host "[smoke] index.faiss = $($faiss.FullName)  ($($faiss.Length) bytes)"
        if ($faiss.Length -lt 9000) {
            Red "[FAIL] index.faiss too small ($($faiss.Length) B) -- vector_store has no real vectors"
            $Fail = 1
        }
    } else {
        Red "[FAIL] no index.faiss under $KbDir"
        Red "       (this is the actual chayuan_root from /runtime/diagnose, so it's authoritative)"
        $Fail = 1
    }

    # --- 9. search_docs with planted secret -----------------------------
    Write-Host "[smoke] POST /knowledge_base/search_docs query='sk-$Secret'"
    $sBody = @{
        query               = "sk-$Secret"
        knowledge_base_name = $KB
        top_k               = 5
        score_threshold     = 2.0
    } | ConvertTo-Json
    $hits = 0
    $paths = "?"
    try {
        $s = Invoke-RestMethod "http://127.0.0.1:$Port/knowledge_base/search_docs" `
                -Method POST -Body $sBody -ContentType "application/json" -TimeoutSec 30
        $arr = if ($s.value) { $s.value } elseif ($s.data) { $s.data } else { @() }
        $hits = @($arr).Count
        # ! Filter null/empty -- some hits omit metadata.retrieval_path entirely
        # (when DB fallback returns matched chunks without the routing tag).
        # Without Where-Object {$_}, Sort-Object -Unique can return [""] which
        # then joins to nothing -> we'd mis-report "no retrieval path engaged"
        # even when keyword retrieval clearly did run.
        $paths = ($arr | ForEach-Object { $_.metadata.retrieval_path } |
                    Where-Object { $_ -and $_ -ne "" } |
                    Sort-Object -Unique) -join ","
        if (-not $paths) { $paths = "(none in metadata)" }
        $s | ConvertTo-Json -Depth 6 | ForEach-Object {
            if ($_.Length -gt 1500) { $_.Substring(0, 1500) + "...[truncated]" } else { $_ }
        } | Write-Host
    } catch {
        Red "[FAIL] search_docs: $($_.Exception.Message)"
        $Fail = 1
    }
    Write-Host "[smoke] hits=$hits  retrieval_paths=[$paths]"

    if ($hits -eq 0) {
        Red "[FAIL] search_docs returned 0 hits for the planted secret"
        $Fail = 1
    } elseif ($paths -match "vector|hybrid") {
        Green "[PASS] vector / hybrid retrieval engaged"
    } else {
        Yellow "[FAIL-soft] only keyword retrieval engaged (vector_store likely empty)"
        Yellow "             paths=$paths"
        $Fail = 1
    }

    # --- Bonus semantic query -------------------------------------------
    Write-Host ""
    Write-Host "[smoke] semantic query: 'what is the DeepSeek apikey'"
    $semBody = @{
        query               = "what is the DeepSeek apikey"
        knowledge_base_name = $KB
        top_k               = 5
        score_threshold     = 2.0
    } | ConvertTo-Json
    try {
        $sem = Invoke-RestMethod "http://127.0.0.1:$Port/knowledge_base/search_docs" `
                -Method POST -Body $semBody -ContentType "application/json" -TimeoutSec 30
        $semArr = if ($sem.value) { $sem.value } elseif ($sem.data) { $sem.data } else { @() }
        $semHits = @($semArr).Count
        $semPaths = ($semArr | ForEach-Object { $_.metadata.retrieval_path } | Sort-Object -Unique) -join ","
        Write-Host "[smoke] semantic hits=$semHits  retrieval_paths=[$semPaths]"
    } catch {
        Yellow "[smoke] semantic query failed: $($_.Exception.Message)"
    }

} catch {
    Red "[smoke] aborted: $($_.Exception.Message)"
    $Fail = 1
} finally {
    Cleanup-Server
}

# --- Summary -----------------------------------------------------------
Write-Host ""
Write-Host "================================================"
if ($Fail -eq 0) {
    Green "[OK] E2E frozen KB smoke PASSED"
    Green "     KB dir: $(Join-Path $Root "data\knowledge_base\$KB")"
    Green "     server log: $LogFile"
    exit 0
} else {
    Red   "[FAIL] E2E frozen KB smoke FAILED"
    Red   "       Test root preserved: $Root"
    Red   "       Server log tail (50 lines):"
    if (Test-Path $LogFile) {
        Get-Content $LogFile -Tail 50 -Encoding utf8 | ForEach-Object { Write-Host "         $_" }
    }
    exit 1
}
