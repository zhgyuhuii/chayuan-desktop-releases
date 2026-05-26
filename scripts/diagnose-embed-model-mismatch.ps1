<#
.SYNOPSIS
    Confirm or rule out the embedding-model-name-mismatch hypothesis.

.DESCRIPTION
    KB stores embed_model = "models/bundled/embedding/bge-m3" (full path).
    When add_doc runs, langchain OpenAIEmbeddings sends that model name to
    sidecar :62583. But sidecar advertises model id "bge-m3-Q8_0.gguf".
    Suspect: sidecar rejects the unknown model -> langchain silently
    returns [] instead of raising -> 0 vectors in FAISS.

    This script POSTs the SAME inputs sidecar saw during KB upload:
      Test 1: full path "models/bundled/embedding/bge-m3" directly to sidecar
      Test 2: sidecar's real id "bge-m3-Q8_0.gguf" directly (control)
      Test 3: full path through chayuan-server /v1/embeddings (we already
              proved this works in the previous diag, repeated for record)

    If Test 1 fails with model-not-found-ish but Test 2 succeeds, that's
    the bug.

    All ASCII source per CLAUDE.md PS encoding rules.

.PARAMETER SidecarBase
    chayuan-server. Default http://127.0.0.1:62581.

.PARAMETER EmbedSidecar
    llama-server embedding. Default http://127.0.0.1:62583.

.EXAMPLE
    .\scripts\diagnose-embed-model-mismatch.ps1
#>
param(
    [string]$SidecarBase  = "http://127.0.0.1:62581",
    [string]$EmbedSidecar = "http://127.0.0.1:62583"
)

chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ts  = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $env:TEMP "chayuan-embed-mismatch-$ts.md"
$utf8Bom = [System.Text.UTF8Encoding]::new($true)
[System.IO.File]::WriteAllText($out, "# Embed model-name mismatch test $ts`n`n", $utf8Bom)

function Append-Section { param([string]$T, [string]$B)
    [System.IO.File]::AppendAllText($out, "`n## $T`n``````n$B`n```````n", $utf8Bom)
}

function Probe-Embed {
    param(
        [string]$Url,
        [string]$ModelName,
        [string]$Text
    )
    $body = @{ model = $ModelName; input = $Text } | ConvertTo-Json
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $r = Invoke-RestMethod $Url -Method POST -Body $body -ContentType "application/json" -TimeoutSec 30
        $sw.Stop()
        $lines = @()
        $lines += "URL  : $Url"
        $lines += "Body : $body"
        $lines += "elapsed_ms : $($sw.ElapsedMilliseconds)"
        if ($r.data -and $r.data.Count -gt 0) {
            $emb = $r.data[0].embedding
            $lines += "STATUS: OK, dim=$(if ($emb) { $emb.Count } else { 'null' })"
            $lines += "model_in_response: $($r.model)"
            if ($emb -and $emb.Count -ge 3) {
                $lines += "first 3 floats: $($emb[0..2] -join ', ')"
            }
        } else {
            $lines += "STATUS: response missing data[]"
            $lines += ($r | ConvertTo-Json -Depth 5)
        }
        return $lines -join "`n"
    } catch {
        $sw.Stop()
        $lines = @()
        $lines += "URL  : $Url"
        $lines += "Body : $body"
        $lines += "elapsed_ms : $($sw.ElapsedMilliseconds)"
        $lines += "STATUS: FAIL"
        $lines += "exception : $($_.Exception.Message)"
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $lines += "body      : $($_.ErrorDetails.Message)"
        }
        return $lines -join "`n"
    }
}

$Text = "deepseek apikey"

Append-Section "Test 1: sidecar receives KB's registered name 'models/bundled/embedding/bge-m3'" `
    (Probe-Embed -Url "$EmbedSidecar/v1/embeddings" -ModelName "models/bundled/embedding/bge-m3" -Text $Text)

Append-Section "Test 2: sidecar receives its actual /v1/models id 'bge-m3-Q8_0.gguf' (control)" `
    (Probe-Embed -Url "$EmbedSidecar/v1/embeddings" -ModelName "bge-m3-Q8_0.gguf" -Text $Text)

Append-Section "Test 3: chayuan-server forwarded with full path (proven OK in earlier diag, repeated for record)" `
    (Probe-Embed -Url "$SidecarBase/v1/embeddings" -ModelName "models/bundled/embedding/bge-m3" -Text $Text)

Append-Section "Test 4: chayuan-server forwarded with random nonsense name 'no-such-model-xyz'" `
    (Probe-Embed -Url "$SidecarBase/v1/embeddings" -ModelName "no-such-model-xyz" -Text $Text)

Write-Host ""
Write-Host "OK -- mismatch test written to:" -ForegroundColor Green
Write-Host "  $out"
Write-Host ""
Write-Host "View:" -ForegroundColor Cyan
Write-Host "  notepad '$out'"
Write-Host "  Get-Content '$out' -Encoding utf8"
