<#
.SYNOPSIS
    Diagnose where the embedding HTTP chain breaks for KB vectorization.

.DESCRIPTION
    KB upload returns 200 OK + docs_count=1 in SQLite, but vector_store
    index.faiss stays at ~4 KB (FAISS header only, 0 vectors). That means
    embed_documents() silently returned an empty list inside add_doc.

    This script makes 2 isolated HTTP probes:
      A. chayuan-server /v1/embeddings (the server's own forwarded route)
      B. embedding sidecar /v1/embeddings (62583, direct)

    Each probe sends the same input text with the model name appropriate
    for that endpoint and asserts:
      - HTTP 200
      - data[0].embedding is a 1024-dim float vector (bge-m3 is 1024-dim)
      - model field in response

    Also dumps:
      - /v1/models (what chayuan-server thinks the platforms expose)
      - /runtime/diagnose chayuan_root (so on-disk debug points right)

    Source is 100% ASCII per CLAUDE.md "PowerShell encoding rules".
    Output written to $env:TEMP\chayuan-embed-route-<ts>.md (UTF-8 BOM).

.PARAMETER SidecarBase
    chayuan-server base URL. Default http://127.0.0.1:62581.

.PARAMETER EmbedSidecar
    Embedding sidecar base. Default http://127.0.0.1:62583.

.PARAMETER ServerModel
    Model name to send to chayuan-server. Default discovered from /v1/models.

.PARAMETER SidecarModel
    Model name to send to embedding sidecar directly. Default discovered
    from sidecar /v1/models.

.PARAMETER TestText
    Test text. Default "deepseek apikey".
    NOTE: param name MUST NOT be $Input -- $Input is a PowerShell automatic
    variable (pipeline enumerator). Declaring param([string]$Input) silently
    fails: the body JSON's input field becomes the enumerator serialized as
    {"Current": null}, not the test string. Renamed to $TestText.

.EXAMPLE
    .\scripts\diagnose-embed-route.ps1
    .\scripts\diagnose-embed-route.ps1 -TestText "what is the apikey"
#>
param(
    [string]$SidecarBase  = "http://127.0.0.1:62581",
    [string]$EmbedSidecar = "http://127.0.0.1:62583",
    [string]$ServerModel  = "",
    [string]$SidecarModel = "",
    [string]$TestText        = "deepseek apikey"
)

chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ts  = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $env:TEMP "chayuan-embed-route-$ts.md"
$utf8Bom = [System.Text.UTF8Encoding]::new($true)
[System.IO.File]::WriteAllText($out, "# Chayuan embed route diagnostic $ts`n`n", $utf8Bom)

function Append-Section {
    param([string]$Title, [string]$Body)
    $block = "`n## $Title`n``````n$Body`n```````n"
    [System.IO.File]::AppendAllText($out, $block, $utf8Bom)
}

function Try-Invoke {
    param([scriptblock]$Block)
    try { & $Block | Out-String }
    catch {
        "FAIL: $($_.Exception.Message)`n--- body ---`n$($_.ErrorDetails.Message)"
    }
}

# ---- 0. context ----
$ctx = Try-Invoke {
    "SidecarBase  : $SidecarBase"
    "EmbedSidecar : $EmbedSidecar"
    "Test input   : $TestText"
    try {
        $diag = Invoke-RestMethod "$SidecarBase/runtime/diagnose" -TimeoutSec 10
        "chayuan_root : $($diag.data.chayuan_root)"
        "server ver   : $($diag.data.chayuan_server_version)"
    } catch {
        "diagnose query failed: $($_.Exception.Message)"
    }
}
Append-Section "0. context" $ctx

# ---- 1. discover model names from /v1/models on both endpoints ----
$disc = Try-Invoke {
    "--- chayuan-server /v1/models (model_type=embed) ---"
    try {
        $m = Invoke-RestMethod "$SidecarBase/v1/models" -TimeoutSec 10
        $embedRows = $m.data | Where-Object { $_.model_type -eq "embed" -and $_.available }
        foreach ($r in $embedRows) {
            "  id=$($r.id)  platform=$($r.platform_name)  owned_by=$($r.owned_by)"
        }
        if (-not $script:ServerModel -and $embedRows) {
            $script:ServerModel = ($embedRows | Select-Object -First 1).id
            "  -> auto-pick ServerModel = $script:ServerModel"
        }
    } catch {
        "  FAIL: $($_.Exception.Message)"
    }

    ""
    "--- embedding sidecar /v1/models ---"
    try {
        $m2 = Invoke-RestMethod "$EmbedSidecar/v1/models" -TimeoutSec 10
        # llama-server returns {models:[{name,model}, ...], data:[{id,...}, ...]}
        $ids = @()
        if ($m2.data)   { $ids += @($m2.data   | ForEach-Object { $_.id }) }
        if ($m2.models) { $ids += @($m2.models | ForEach-Object { $_.model }) }
        # ! Force array semantics with @() wrapper. Without it, single-element
        # pipeline outputs are unwrapped to scalars, and `$ids[0]` on a string
        # returns the first CHARACTER (e.g. "bge-m3-Q8_0.gguf"[0] = "b").
        # @() forces array; @($scalar) is still [string[]] of length 1.
        $ids = @($ids | Where-Object { $_ } | Select-Object -Unique)
        foreach ($id in $ids) { "  id=$id" }
        if (-not $script:SidecarModel -and $ids.Count -gt 0) {
            $script:SidecarModel = [string]$ids[0]
            "  -> auto-pick SidecarModel = $script:SidecarModel"
        }
    } catch {
        "  FAIL: $($_.Exception.Message)"
    }
}
Append-Section "1. model discovery" $disc

# ---- 2. probe chayuan-server /v1/embeddings ----
$probeA = Try-Invoke {
    if (-not $ServerModel) { return "ServerModel unresolved, skipping probe A" }
    $body = @{ model = $ServerModel; input = $TestText } | ConvertTo-Json
    "URL  : $SidecarBase/v1/embeddings"
    "Body : $body"
    ""
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $r = Invoke-RestMethod "$SidecarBase/v1/embeddings" `
                -Method POST -Body $body -ContentType "application/json" -TimeoutSec 30
        $sw.Stop()
        "elapsed_ms : $($sw.ElapsedMilliseconds)"
        if ($r.data -and $r.data.Count -gt 0) {
            $emb = $r.data[0].embedding
            "vector_count   : $($r.data.Count)"
            "dim            : $(if ($emb) { $emb.Count } else { 'null' })"
            "first 5 floats : $(if ($emb -and $emb.Count -ge 5) { ($emb[0..4] -join ', ') } else { 'n/a' })"
            "all-zero?      : $(if ($emb) { ($emb | Where-Object { $_ -ne 0 } | Select-Object -First 1).Count -eq 0 } else { 'n/a' })"
            "model returned : $($r.model)"
        } else {
            "RAW RESPONSE (no data[]):"
            $r | ConvertTo-Json -Depth 5
        }
    } catch {
        $sw.Stop()
        "elapsed_ms : $($sw.ElapsedMilliseconds)"
        "FAIL: $($_.Exception.Message)"
        if ($_.ErrorDetails) { "body: $($_.ErrorDetails.Message)" }
    }
}
Append-Section "2. probe A: chayuan-server /v1/embeddings (the actual route KB uses)" $probeA

# ---- 3. probe embedding sidecar directly ----
$probeB = Try-Invoke {
    if (-not $SidecarModel) { return "SidecarModel unresolved, skipping probe B" }
    $body = @{ model = $SidecarModel; input = $TestText } | ConvertTo-Json
    "URL  : $EmbedSidecar/v1/embeddings"
    "Body : $body"
    ""
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $r = Invoke-RestMethod "$EmbedSidecar/v1/embeddings" `
                -Method POST -Body $body -ContentType "application/json" -TimeoutSec 30
        $sw.Stop()
        "elapsed_ms : $($sw.ElapsedMilliseconds)"
        if ($r.data -and $r.data.Count -gt 0) {
            $emb = $r.data[0].embedding
            "vector_count   : $($r.data.Count)"
            "dim            : $(if ($emb) { $emb.Count } else { 'null' })"
            "first 5 floats : $(if ($emb -and $emb.Count -ge 5) { ($emb[0..4] -join ', ') } else { 'n/a' })"
            "all-zero?      : $(if ($emb) { ($emb | Where-Object { $_ -ne 0 } | Select-Object -First 1).Count -eq 0 } else { 'n/a' })"
            "model returned : $($r.model)"
        } else {
            "RAW RESPONSE (no data[]):"
            $r | ConvertTo-Json -Depth 5
        }
    } catch {
        $sw.Stop()
        "elapsed_ms : $($sw.ElapsedMilliseconds)"
        "FAIL: $($_.Exception.Message)"
        if ($_.ErrorDetails) { "body: $($_.ErrorDetails.Message)" }
    }
}
Append-Section "3. probe B: embedding sidecar /v1/embeddings (direct, bypasses server forwarding)" $probeB

# ---- 4. try the SHORT model name through chayuan-server (the smoke test
#         first-round failure pattern: bge-m3 short name resolves to
#         LocalAIEmbeddings which validates openai_api_key) ----
$probeShort = Try-Invoke {
    $body = @{ model = "bge-m3"; input = $TestText } | ConvertTo-Json
    "URL  : $SidecarBase/v1/embeddings"
    "Body : $body"
    ""
    try {
        $r = Invoke-RestMethod "$SidecarBase/v1/embeddings" `
                -Method POST -Body $body -ContentType "application/json" -TimeoutSec 30
        if ($r.data -and $r.data.Count -gt 0) {
            "OK: dim=$($r.data[0].embedding.Count) model=$($r.model)"
        } else {
            "RAW:"
            $r | ConvertTo-Json -Depth 5
        }
    } catch {
        "FAIL (expected if short-name routing is broken): $($_.Exception.Message)"
        if ($_.ErrorDetails) { "body: $($_.ErrorDetails.Message)" }
    }
}
Append-Section "4. probe C: short-name 'bge-m3' through chayuan-server (expect this to FAIL with api_key validation if the smoke earlier triggered it)" $probeShort

# ---- 5. dump /v1/models full (for cross-ref) ----
$models = Try-Invoke {
    # ! Use Invoke-WebRequest + manual UTF-8 decode to preserve Chinese
    # platform_display_name. Invoke-RestMethod on PS 5.1 sometimes falls
    # back to ISO-8859-1 when HTTP charset header is absent -> mojibake.
    $wr = Invoke-WebRequest "$SidecarBase/v1/models" -TimeoutSec 10 -UseBasicParsing
    $text = [System.Text.Encoding]::UTF8.GetString($wr.Content)
    ($text | ConvertFrom-Json) | ConvertTo-Json -Depth 6
}
Append-Section "5. /v1/models full (cross-ref)" $models

Write-Host ""
Write-Host "OK -- embed route diagnostic written to:" -ForegroundColor Green
Write-Host "  $out"
Write-Host ""
Write-Host "View:" -ForegroundColor Cyan
Write-Host "  notepad '$out'"
Write-Host "  Get-Content '$out' -Encoding utf8"
Write-Host ""
Write-Host "Paste the entire file contents back." -ForegroundColor Cyan
