<#
.SYNOPSIS
    One-shot diagnostic dump for Chayuan local runtime -- generates a single
    UTF-8 markdown file you can paste back to a developer.

.DESCRIPTION
    All source code in this .ps1 is ASCII / English so that PowerShell 5.1
    (which defaults to GBK on Chinese-locale Windows when there is no UTF-8
    BOM on the file) does not mojibake string literals and break parsing.
    The output .md is written via [System.IO.File]::WriteAllText with an
    explicit UTF-8 BOM, so the report itself displays Chinese cleanly in
    Notepad / VSCode / PS 5.1 / PS 7 alike.

    Sections gathered:
      1. Standard diagnose (calls scripts/diagnose.ps1 -> /runtime/diagnose)
      2. Process list (chayuan / llama-server / whisper-server / rapidocr ...)
      3. OCR sidecar status (/v1/modality/sidecar/ocr/status)
      4. OCR health (/runtime/ocr/health) - the footer indicator's source
      5. KB creation smoke test (faiss + bge-m3, throwaway name)
      6. server.log tail 100 lines (scans common chayuan_root locations)
      7. bundled_models tree (under <chayuan_root>/models/bundled)
      8. rapidocr sidecar log tail 50 lines (%TEMP%/chayuan_rapidocr.log)
      9. Installed exe size + mtime (for version cross-check)

.EXAMPLE
    bash scripts\diagnose-all.ps1
    bash scripts\diagnose-all.ps1 -SidecarBase http://127.0.0.1:7861
#>
param(
    [string]$SidecarBase = "http://127.0.0.1:62581"
)

# Force UTF-8 on console + all child process IO; -- avoids GBK fallback
# garbling our Get-Content / Out-String reads. Safe on PS 5.1 and 7+.
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ts  = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $env:TEMP "chayuan-diag-all-$ts.md"

# UTF-8 with BOM -- Notepad / PS5.1 Get-Content default decoder both detect
# this and render Chinese correctly.
$utf8Bom = [System.Text.UTF8Encoding]::new($true)
[System.IO.File]::WriteAllText($out, "# Chayuan one-shot diagnostic $ts`n`n", $utf8Bom)

function Append-Section {
    param([string]$Title, [string]$Body)
    $block = "`n## $Title`n``````n$Body`n```````n"
    [System.IO.File]::AppendAllText($out, $block, $utf8Bom)
}

function Try-Invoke {
    param([scriptblock]$Block)
    try { & $Block | Out-String } catch { "FAIL: $($_.Exception.Message)`n$($_.ErrorDetails.Message)" }
}

# ----------------------------------------------------------------------
# 1. Standard diagnose
#
# Do NOT capture child stdout via "& powershell ... | Out-String" -- the
# child process inherits a fresh Console.OutputEncoding (system default,
# usually GBK on zh-CN Windows), so UTF-8 bytes emitted by diagnose.ps1
# get re-decoded as GBK on the way back -> mojibake (Chinese chars come
# back as garbage Latin-1-looking sequences).
#
# Workaround: diagnose.ps1 already Set-Content -Encoding UTF8 writes its
# full report to $env:TEMP\chayuan-diagnose-<ts>.md. Run it (discard
# stdout), then read its file with Get-Content -Encoding utf8 -- bytes
# stay UTF-8 end to end.
# ----------------------------------------------------------------------
$diagOut = Try-Invoke {
    $script = Join-Path $PSScriptRoot "diagnose.ps1"
    if (-not (Test-Path $script)) {
        return "scripts\diagnose.ps1 not found, skipped"
    }
    # Snapshot existing diagnose-*.md before so we can pick the new one
    $before = @(Get-ChildItem "$env:TEMP\chayuan-diagnose-*.md" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    & powershell -NoProfile -ExecutionPolicy Bypass -File $script -SidecarBase $SidecarBase *> $null
    $after = Get-ChildItem "$env:TEMP\chayuan-diagnose-*.md" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    $new = $after | Where-Object { $before -notcontains $_.FullName } | Select-Object -First 1
    if (-not $new) {
        # diagnose.ps1 might have failed before writing; fall back to most recent
        $new = $after | Select-Object -First 1
    }
    if ($new) {
        Get-Content $new.FullName -Raw -Encoding utf8
    } else {
        "diagnose.ps1 ran but produced no output file under `$env:TEMP"
    }
}
Append-Section "1. Standard diagnose (/runtime/diagnose)" $diagOut

# ----------------------------------------------------------------------
# 2. Process list
# ----------------------------------------------------------------------
$proc = Try-Invoke {
    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -like '*chayuan*' -or $_.CommandLine -match '(chayuan|llama-server|whisper-server|rapidocr|paddleocr|funasr|voxcpm)' } |
        Select-Object ProcessId, ParentProcessId, Name, @{N='Cmd';E={$_.CommandLine}} |
        Format-Table -AutoSize -Wrap
}
Append-Section "2. Process list" $proc

# ----------------------------------------------------------------------
# 3. OCR sidecar status
# ----------------------------------------------------------------------
$ocrStatus = Try-Invoke {
    (Invoke-RestMethod "$SidecarBase/v1/modality/sidecar/ocr/status" -TimeoutSec 5) | ConvertTo-Json -Depth 10
}
Append-Section "3. OCR sidecar status /v1/modality/sidecar/ocr/status" $ocrStatus

# ----------------------------------------------------------------------
# 4. OCR health (the source of truth for the footer indicator)
# ----------------------------------------------------------------------
$ocrHealth = Try-Invoke {
    (Invoke-RestMethod "$SidecarBase/runtime/ocr/health" -TimeoutSec 5) | ConvertTo-Json -Depth 10
}
Append-Section "4. OCR health /runtime/ocr/health" $ocrHealth

# ----------------------------------------------------------------------
# 5. KB creation smoke test
# ----------------------------------------------------------------------
$smoke = Try-Invoke {
    $body = @{
        knowledge_base_name = "_diag_smoke_$ts"
        vector_store_type   = "faiss"
        embed_model         = "bge-m3"
    } | ConvertTo-Json
    Invoke-RestMethod "$SidecarBase/knowledge_base/create_knowledge_base" `
        -Method POST -Body $body -ContentType "application/json" -TimeoutSec 30 |
        ConvertTo-Json -Depth 6
}
Append-Section "5. KB creation smoke test (throwaway name, surfaces real error on fail)" $smoke

# ----------------------------------------------------------------------
# 6. server.log tail 100
# ----------------------------------------------------------------------
$logs = Try-Invoke {
    $candidates = @(
        "$env:APPDATA\chayuan\logs\server.log",
        "D:\chayuan-data\logs\server.log",
        "$env:APPDATA\chayuan\logs\chayuan.log",
        "D:\chayuan-data\logs\chayuan.log",
        "C:\chayuan-data\logs\server.log"
    ) | Where-Object { Test-Path $_ }
    if (-not $candidates) { return "(no server log found)" }
    $sb = [System.Text.StringBuilder]::new()
    foreach ($p in $candidates) {
        $sb.AppendLine("--- $p ---") | Out-Null
        Get-Content $p -Tail 100 -Encoding utf8 | ForEach-Object {
            $sb.AppendLine($_) | Out-Null
        }
    }
    $sb.ToString()
}
Append-Section "6. server.log tail 100" $logs

# ----------------------------------------------------------------------
# 7. bundled_models tree
# ----------------------------------------------------------------------
$bundled = Try-Invoke {
    $candidates = @(
        "$env:APPDATA\chayuan\models\bundled",
        "D:\chayuan-data\models\bundled",
        "C:\chayuan-data\models\bundled"
    ) | Where-Object { Test-Path $_ }
    if (-not $candidates) { return "(no bundled dir found)" }
    $sb = [System.Text.StringBuilder]::new()
    foreach ($p in $candidates) {
        $sb.AppendLine("--- $p ---") | Out-Null
        $files = Get-ChildItem $p -Recurse -File |
            Select-Object @{N='Rel';E={$_.FullName.Substring($p.Length + 1)}}, @{N='MB';E={[math]::Round($_.Length / 1MB, 1)}}
        ($files | Format-Table -AutoSize | Out-String).Split([Environment]::NewLine) | ForEach-Object {
            $sb.AppendLine($_) | Out-Null
        }
    }
    $sb.ToString()
}
Append-Section "7. bundled_models tree (<chayuan_root>/models/bundled)" $bundled

# ----------------------------------------------------------------------
# 8. rapidocr sidecar log tail
# ----------------------------------------------------------------------
$ocrLog = Try-Invoke {
    $p = "$env:TEMP\chayuan_rapidocr.log"
    if (Test-Path $p) { Get-Content $p -Tail 50 -Encoding utf8 | Out-String }
    else { "(file missing -- rapidocr sidecar has never started)" }
}
Append-Section "8. rapidocr sidecar log tail 50" $ocrLog

# ----------------------------------------------------------------------
# 9. Installed exe info
# ----------------------------------------------------------------------
$exeInfo = Try-Invoke {
    $exes = @(
        "C:\Program Files\Chayuan\chayuan-server\chayuan-server.exe",
        "$env:LOCALAPPDATA\Programs\Chayuan\chayuan-server\chayuan-server.exe"
    ) | Where-Object { Test-Path $_ }
    if (-not $exes) { return "(chayuan-server.exe not found)" }
    $sb = [System.Text.StringBuilder]::new()
    foreach ($e in $exes) {
        $info = Get-Item $e
        $sb.AppendLine("$e") | Out-Null
        $sb.AppendLine("  SizeMB:   $([math]::Round($info.Length / 1MB, 1))") | Out-Null
        $sb.AppendLine("  Modified: $($info.LastWriteTime)") | Out-Null
    }
    $sb.ToString()
}
Append-Section "9. Installed exe location + build info" $exeInfo

# ----------------------------------------------------------------------
# Done
# ----------------------------------------------------------------------
Write-Host ""
Write-Host "OK -- diagnostic written to $out (UTF-8 with BOM)" -ForegroundColor Green
Write-Host ""
Write-Host "View (any of):" -ForegroundColor Cyan
Write-Host "  Get-Content '$out' -Encoding utf8       # force UTF-8 read"
Write-Host "  notepad '$out'                          # Notepad auto-detects BOM"
Write-Host "  code '$out'                             # VSCode"
Write-Host ""
Write-Host "Paste the full file contents back to the developer." -ForegroundColor Cyan
