<#
.SYNOPSIS
    Diagnose why uploaded files into a knowledge base never get vectorized
    (KB visible in UI but chat returns no KB content / search returns
    keyword-only hits). Generates a single UTF-8 markdown report safe to
    paste to a developer.

.DESCRIPTION
    All source is ASCII / English. PowerShell 5.1 on zh-CN Windows decodes
    BOM-less .ps1 files as GBK and mojibake-s Chinese literals -- keep this
    file English to stay parse-safe. The output .md is written via
    [System.IO.File]::WriteAllText with explicit UTF-8 BOM so the report
    renders Chinese cleanly in Notepad / VSCode / PS / etc.

    Key fix vs the 2026-05-23 v1 of this script: the on-disk KB path is
    <chayuan_root>/data/knowledge_base/<kb_name>/  -- note the `data/`
    segment. Settings.basic_settings.KB_ROOT_PATH defaults to that. v1
    was checking <chayuan_root>/knowledge_base/<kb_name>/ and incorrectly
    reported "KB dir does not exist".

    Sections collected (in this order):
      0. environment resolution (chayuan_root, server.log path, install exe)
      1. tiktoken runtime-hook log trace -- did 77c80ac actually take effect
         in the API-server mp child (the process that runs KB chunking)?
      2. KB on-disk layout at the CORRECT path (data/knowledge_base/<kb>)
         -- look for vector_store/<embed_model>/index.faiss with non-zero size
      3. /knowledge_base/list_files (what the SQLite DB believes)
      4. /knowledge_base/recreate_vector_store -- forces backend to redo
         chunking + embedding for all files; surfaces real exception
      5. /knowledge_base/search_docs with a test query -- look at retrieval_path
         (vector / hybrid / keyword). keyword-only means vector_store empty.
      6. server.log tail 300 lines -- raw tracebacks
      7. server.log filtered for KB / tiktoken / vector / embed / exception
      8. embedding sidecar @ 62583 reachability
      9. running process tree (chayuan-server, llama-server, etc.)

.PARAMETER KbName
    KB to diagnose. Default "bb".

.PARAMETER SidecarBase
    chayuan-server base URL. Default http://127.0.0.1:62581.

.PARAMETER TestQuery
    Test query string. Default "deepseek apikey".

.EXAMPLE
    bash scripts/diagnose-kb-vectorize.ps1
    bash scripts/diagnose-kb-vectorize.ps1 -KbName my_kb
    bash scripts/diagnose-kb-vectorize.ps1 -KbName bb -TestQuery "apikey"
#>
param(
    [string]$KbName = "bb",
    [string]$SidecarBase = "http://127.0.0.1:62581",
    [string]$TestQuery = "deepseek apikey"
)

# Force UTF-8 console end-to-end so child commands and Get-Content do not
# GBK-decode UTF-8 bytes.
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ts  = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $env:TEMP "chayuan-kb-diag-$ts.md"

$utf8Bom = [System.Text.UTF8Encoding]::new($true)
$header = "# KB vectorize diagnostic for KB='${KbName}' at ${ts}`n`n" +
          "- Sidecar : ${SidecarBase}`n" +
          "- Query   : ${TestQuery}`n`n"
[System.IO.File]::WriteAllText($out, $header, $utf8Bom)

function Append-Section {
    param([string]$Title, [string]$Body)
    $block = "`n## $Title`n``````n$Body`n```````n"
    [System.IO.File]::AppendAllText($out, $block, $utf8Bom)
}

function Try-Invoke {
    param([scriptblock]$Block)
    try { & $Block | Out-String }
    catch { "FAIL: $($_.Exception.Message)`n$($_.ErrorDetails.Message)" }
}

# ----------------------------------------------------------------------
# Resolve runtime context
# ----------------------------------------------------------------------
$chayuanRoot = $null
$rootErr = $null
try {
    $chayuanRoot = (Invoke-RestMethod "${SidecarBase}/runtime/diagnose" -TimeoutSec 10).data.chayuan_root
} catch {
    $rootErr = $_.Exception.Message
}

# server.log can live at multiple plausible paths -- scan all of them.
$logCandidates = @()
if ($chayuanRoot) {
    $logCandidates += @(
        (Join-Path $chayuanRoot "logs\server.log"),
        (Join-Path $chayuanRoot "logs\chayuan.log"),
        (Join-Path $chayuanRoot "data\logs\server.log"),
        (Join-Path $chayuanRoot "data\logs\chayuan.log")
    )
}
$logCandidates += @(
    "$env:APPDATA\chayuan\logs\server.log",
    "$env:APPDATA\chayuan\logs\chayuan.log",
    "$env:LOCALAPPDATA\chayuan\logs\server.log",
    "D:\chayuan-data\logs\server.log"
)
$logPath = $logCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

# Installed exe -- for build time vs current run cross-check
$exeCandidates = @(
    "C:\Program Files\Chayuan\chayuan-server\chayuan-server.exe",
    "$env:LOCALAPPDATA\Programs\Chayuan\chayuan-server\chayuan-server.exe"
)
$exePath = $exeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
$exeMtime = if ($exePath) { (Get-Item $exePath).LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss") } else { "(not installed)" }
$exeSizeMB = if ($exePath) { [math]::Round((Get-Item $exePath).Length / 1MB, 1) } else { 0 }

# The actual on-disk KB dir (note the `data` segment -- KB_ROOT_PATH default)
$kbDir = $null
if ($chayuanRoot) {
    $kbDir = Join-Path $chayuanRoot "data\knowledge_base\$KbName"
}

$envSummary = @"
chayuan_root        : $chayuanRoot
chayuan_root status : $(if ($rootErr) { "FAIL: $rootErr" } else { "OK" })
log path picked     : $logPath
log scanned         : $($logCandidates -join "; ")
KB dir (expected)   : $kbDir
installed exe       : $exePath
exe size (MB)       : $exeSizeMB
exe mtime           : $exeMtime
"@
Append-Section "0. environment resolution" $envSummary

# ----------------------------------------------------------------------
# 1. tiktoken hook trace -- did 77c80ac actually take effect?
# ----------------------------------------------------------------------
$tikLines = Try-Invoke {
    if (-not $logPath) { return "(no log path; cannot verify)" }
    $hits = Get-Content $logPath -Encoding utf8 -ErrorAction SilentlyContinue |
        Select-String "tiktoken-rthook"
    if (-not $hits) {
        "(no [tiktoken-rthook] lines found in $logPath)`n" +
        "Meaning the running build does NOT have commit 77c80ac (tiktoken " +
        "hook moved before mp-freeze). Without it, the API-server mp child " +
        "never patches tiktoken, so KB chunking + vector_store init still " +
        "trips 'Unknown encoding cl100k_base'. Rebuild PyInstaller and " +
        "copy dist/chayuan-server/* into the install dir."
    } else {
        ($hits | Select-Object -Last 20 | ForEach-Object { $_.Line }) -join "`n"
    }
}
Append-Section "1. tiktoken hook trace (expect a 'patched _available_plugin_modules' line per process boot)" $tikLines

# ----------------------------------------------------------------------
# 2. KB on-disk layout at the CORRECT path
# ----------------------------------------------------------------------
$disk = Try-Invoke {
    if (-not $kbDir) { return "(chayuan_root unresolved)" }
    if (-not (Test-Path $kbDir)) {
        return "(KB dir does not exist: $kbDir)`n" +
               "If you expected files here, something failed at KB creation " +
               "before content/ was even written. Check upload_docs response " +
               "in section 7."
    }
    $sb = [System.Text.StringBuilder]::new()
    $sb.AppendLine("KB dir: $kbDir") | Out-Null
    Get-ChildItem $kbDir -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object FullName |
        Select-Object @{N='Rel';E={$_.FullName.Substring($kbDir.Length + 1)}},
                      @{N='Bytes';E={$_.Length}},
                      @{N='KB';E={[math]::Round($_.Length / 1KB, 1)}} |
        Format-Table -AutoSize |
        Out-String |
        ForEach-Object { $sb.AppendLine($_) | Out-Null }
    # Per-question key: is index.faiss present and > 0 bytes?
    $faiss = Get-ChildItem $kbDir -Recurse -Filter "*.faiss" -ErrorAction SilentlyContinue
    if ($faiss) {
        $sb.AppendLine("FAISS index files found:") | Out-Null
        foreach ($f in $faiss) {
            $sb.AppendLine("  $($f.FullName)  $($f.Length) bytes") | Out-Null
        }
    } else {
        $sb.AppendLine("NO .faiss file under KB dir -- vector_store empty / missing.") | Out-Null
    }
    $sb.ToString()
}
Append-Section "2. KB on-disk layout (look for vector_store/<embed_model>/index.faiss with non-zero size)" $disk

# ----------------------------------------------------------------------
# 3. /knowledge_base/list_files
# ----------------------------------------------------------------------
$listFiles = Try-Invoke {
    (Invoke-RestMethod "${SidecarBase}/knowledge_base/list_files?knowledge_base_name=$KbName" -TimeoutSec 15) | ConvertTo-Json -Depth 8
}
Append-Section "3. /knowledge_base/list_files" $listFiles

# ----------------------------------------------------------------------
# 4. /knowledge_base/recreate_vector_store -- forces real exception
# ----------------------------------------------------------------------
$recreate = Try-Invoke {
    $body = @{
        knowledge_base_name = $KbName
        allow_empty_kb = $false
    } | ConvertTo-Json
    Invoke-RestMethod "${SidecarBase}/knowledge_base/recreate_vector_store" `
        -Method POST -Body $body -ContentType "application/json" -TimeoutSec 600 |
        ConvertTo-Json -Depth 6
}
Append-Section "4. /knowledge_base/recreate_vector_store (real exception surfaces here)" $recreate

# ----------------------------------------------------------------------
# 5. /knowledge_base/search_docs probe (post-recreate)
# ----------------------------------------------------------------------
$search = Try-Invoke {
    $body = @{
        query = $TestQuery
        knowledge_base_name = $KbName
        top_k = 5
        score_threshold = 2.0
    } | ConvertTo-Json
    Invoke-RestMethod "${SidecarBase}/knowledge_base/search_docs" `
        -Method POST -Body $body -ContentType "application/json" -TimeoutSec 30 |
        ConvertTo-Json -Depth 6
}
Append-Section "5. /knowledge_base/search_docs (query='$TestQuery', check retrieval_path)" $search

# ----------------------------------------------------------------------
# 6. server.log tail 300
# ----------------------------------------------------------------------
$tail = Try-Invoke {
    if (-not $logPath) { return "(no log path)" }
    (Get-Content $logPath -Tail 300 -Encoding utf8) -join "`n"
}
Append-Section "6. server.log tail 300" $tail

# ----------------------------------------------------------------------
# 7. server.log filtered (key terms, last 80 hits)
# ----------------------------------------------------------------------
$filtered = Try-Invoke {
    if (-not $logPath) { return "(no log path)" }
    $hits = Get-Content $logPath -Encoding utf8 -ErrorAction SilentlyContinue |
        Select-String -Pattern "tiktoken|cl100k|vector_store|embedding|embed_documents|upload_docs|recreate_vector|search_docs|kb_doc_api|splitter|atomic_rebuild|FileNotFound|Traceback|ERROR|Exception"
    if (-not $hits) { return "(no matching lines)" }
    ($hits | Select-Object -Last 80 | ForEach-Object { $_.Line }) -join "`n"
}
Append-Section "7. server.log filtered (tiktoken/vector_store/embed/upload/recreate/splitter/atomic_rebuild/Traceback/Exception, last 80)" $filtered

# ----------------------------------------------------------------------
# 8. embedding sidecar reachability
# ----------------------------------------------------------------------
$emb = Try-Invoke {
    (Invoke-RestMethod "http://127.0.0.1:62583/v1/models" -TimeoutSec 5) | ConvertTo-Json -Depth 6
}
Append-Section "8. embedding sidecar /v1/models @ 62583" $emb

# ----------------------------------------------------------------------
# 9. running process tree
# ----------------------------------------------------------------------
$procs = Try-Invoke {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like '*chayuan*' -or $_.CommandLine -match '(chayuan|llama-server|whisper-server|rapidocr|paddleocr)' } |
        Select-Object ProcessId, ParentProcessId, Name, @{N='Cmd';E={$_.CommandLine}} |
        Format-Table -AutoSize -Wrap |
        Out-String
}
Append-Section "9. running process tree" $procs

# ----------------------------------------------------------------------
# Done
# ----------------------------------------------------------------------
Write-Host ""
Write-Host "OK -- KB vectorize diagnostic written to:" -ForegroundColor Green
Write-Host "  $out"
Write-Host ""
Write-Host "View:" -ForegroundColor Cyan
Write-Host "  notepad '$out'"
Write-Host "  Get-Content '$out' -Encoding utf8"
Write-Host ""
Write-Host "Paste the entire file contents back to the developer." -ForegroundColor Cyan
