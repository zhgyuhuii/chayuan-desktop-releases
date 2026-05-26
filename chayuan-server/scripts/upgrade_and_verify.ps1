# Upgrade for Python 3.12, then run import smoke + unit tests.
# Run from repo root: .\scripts\upgrade_and_verify.ps1
# Requires: conda; script can auto-create env if needed.

param(
    [string]$EnvName = "py312",
    [string]$PythonVersion = "3.12",
    [string]$PrivatePyPIUrl = "http://192.168.1.130:8081/repository/pypi_group/simple/",
    [string]$FallbackPyPIUrl = "https://pypi.org/simple/",
    [switch]$SkipSourceProbe,
    [switch]$NoFallback
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ChayuanData = Join-Path $RepoRoot "chayuan_data"
$ServerDir = Join-Path $RepoRoot "libs\chayuan-server"

$env:CHAYUAN_ROOT = $ChayuanData
Write-Host "CHAYUAN_ROOT=$($env:CHAYUAN_ROOT)"

# Prefer conda run so this works without conda init in the current shell
$CondaExe = $null
foreach ($c in @(
        "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
        "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
        "$env:LOCALAPPDATA\miniconda3\Scripts\conda.exe",
        "$env:ProgramData\anaconda3\Scripts\conda.exe",
        "$env:ProgramData\miniconda3\Scripts\conda.exe"
    )) {
    if (Test-Path $c) { $CondaExe = $c; break }
}
if (-not $CondaExe) {
    $CondaExe = (Get-Command conda -ErrorAction SilentlyContinue).Source
}
if (-not $CondaExe) {
    Write-Error "conda.exe not found. Install Anaconda/Miniconda and add conda to PATH."
    exit 1
}

function Normalize-PyPISource([string]$url) {
    if ([string]::IsNullOrWhiteSpace($url)) { return $url }
    $normalized = $url.Trim()
    if (-not $normalized.EndsWith("/")) {
        $normalized = "$normalized/"
    }
    return $normalized
}

function Test-Url([string]$url) {
    $base = Normalize-PyPISource $url
    $candidates = @()
    if ($base -match "/simple/$") {
        $candidates += $base
        $candidates += ($base -replace "/simple/$", "/")
    } else {
        $candidates += "${base}simple/"
        $candidates += $base
    }
    foreach ($candidate in $candidates) {
        try {
            # Some Nexus setups reject HEAD (405), so probe with GET first.
            $resp = Invoke-WebRequest -Uri $candidate -Method Get -TimeoutSec 15 -MaximumRedirection 5 -UseBasicParsing
            return @{
                Reachable = $true
                Url = $candidate
                Detail = "HTTP $($resp.StatusCode)"
            }
        } catch {
            $status = $null
            try { $status = [int]$_.Exception.Response.StatusCode } catch {}
            # Auth-required responses still prove the endpoint is reachable.
            if ($status -in @(401, 403, 405)) {
                return @{
                    Reachable = $true
                    Url = $candidate
                    Detail = "HTTP $status (reachable, auth/method restricted)"
                }
            }
            $lastError = $_.Exception.Message
        }
    }
    return @{
        Reachable = $false
        Url = $base
        Detail = $lastError
    }
}

function Invoke-CondaRun {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArgs)
    & $CondaExe run -n $EnvName --no-capture-output @CommandArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-CondaRunWithRetry {
    param(
        [Parameter(Mandatory = $true, Position = 0, ValueFromRemainingArguments = $true)][string[]]$CommandArgs,
        [int]$MaxAttempts = 3,
        [int]$DelaySeconds = 5
    )
    for ($i = 1; $i -le $MaxAttempts; $i++) {
        & $CondaExe run -n $EnvName --no-capture-output @CommandArgs
        if ($LASTEXITCODE -eq 0) { return }
        if ($i -lt $MaxAttempts) {
            Write-Host ">>> command failed (attempt $i/$MaxAttempts), retrying in $DelaySeconds seconds..." -ForegroundColor Yellow
            Start-Sleep -Seconds $DelaySeconds
        }
    }
    exit $LASTEXITCODE
}

Push-Location $ServerDir
try {
    Write-Host ">>> ensuring conda env '$EnvName' exists (python=$PythonVersion)..."
    $envList = & $CondaExe env list
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $envExists = $envList -match "^\s*$([regex]::Escape($EnvName))\s"
    if (-not $envExists) {
        & $CondaExe create -n $EnvName "python=$PythonVersion" -y
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    if ($SkipSourceProbe) {
        $activeSource = Normalize-PyPISource $PrivatePyPIUrl
        Write-Host ">>> SkipSourceProbe is set, force using private source: $activeSource" -ForegroundColor Yellow
    } else {
        $privateProbe = Test-Url $PrivatePyPIUrl
        if ($privateProbe.Reachable) {
            $activeSource = $privateProbe.Url
            Write-Host ">>> private PyPI is reachable, using internal source: $($privateProbe.Detail)" -ForegroundColor Green
        } else {
            if ($NoFallback) {
                $activeSource = Normalize-PyPISource $PrivatePyPIUrl
                Write-Host ">>> private probe failed but -NoFallback is enabled, still trying private source: $activeSource" -ForegroundColor Yellow
            } else {
                Write-Host ">>> private PyPI unavailable ($($privateProbe.Detail)), switching to public fallback..." -ForegroundColor Yellow
                $fallbackProbe = Test-Url $FallbackPyPIUrl
                if ($fallbackProbe.Reachable) {
                    $activeSource = $fallbackProbe.Url
                    Write-Host ">>> fallback PyPI is reachable: $($fallbackProbe.Detail)" -ForegroundColor Green
                } else {
                    $activeSource = Normalize-PyPISource $PrivatePyPIUrl
                    Write-Host ">>> probes failed (private=[$($privateProbe.Detail)] fallback=[$($fallbackProbe.Detail)]), trying private source anyway: $activeSource" -ForegroundColor Yellow
                }
            }
        }
    }

    Write-Host ">>> configuring poetry source: group-pypi => $activeSource"
    try {
        Invoke-CondaRun poetry source remove group-pypi
    } catch {
        Write-Host ">>> source 'group-pypi' not present, skipping remove." -ForegroundColor DarkYellow
    }
    Invoke-CondaRun poetry source add --priority primary group-pypi $activeSource

    Write-Host ">>> poetry update (LangChain stack)..."
    Invoke-CondaRunWithRetry poetry update `
        langchain langchain-core langchain-community langchain-openai `
        langchain-experimental langchain-text-splitters langchain-classic langchainhub

    Write-Host ">>> poetry install (with lint,test and xinference)..."
    Invoke-CondaRunWithRetry poetry install --with lint,test -E xinference --no-interaction

    Write-Host ">>> verify_chayuan_imports.py..."
    Invoke-CondaRun python (Join-Path $RepoRoot "scripts\verify_chayuan_imports.py")

    Write-Host ">>> verify local package import (chayuan)..."
    try {
        Invoke-CondaRun poetry run python -c "import chayuan; print(chayuan.__file__)"
    } catch {
        Write-Host ">>> import failed, fallback to PYTHONPATH injection..." -ForegroundColor Yellow
        if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
            $env:PYTHONPATH = $ServerDir
        } else {
            $env:PYTHONPATH = "$ServerDir;$($env:PYTHONPATH)"
        }
        Invoke-CondaRun poetry run python -c "import chayuan; print(chayuan.__file__)"
    }

    Write-Host ">>> pytest unit_tests (smoke)..."
    Invoke-CondaRun poetry run python -m pytest tests/unit_tests -q --tb=line
}
finally {
    Pop-Location
}

Write-Host "upgrade_and_verify: done."
