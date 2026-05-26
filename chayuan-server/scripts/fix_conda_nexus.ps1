param(
    [string]$NexusBase = "http://192.168.1.130:8081",
    [string]$RepoName = "conda-main",
    [string]$ChannelPath = "pkgs/main",
    [string]$EnvName = "py312",
    [string]$PythonVersion = "3.12",
    [switch]$SkipCreateEnv
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Test-Url([string]$url) {
    try {
        $resp = Invoke-WebRequest -Uri $url -Method Head -TimeoutSec 20 -UseBasicParsing
        return @{
            Ok = $true
            StatusCode = $resp.StatusCode
            Url = $url
            Error = ""
        }
    } catch {
        return @{
            Ok = $false
            StatusCode = 0
            Url = $url
            Error = $_.Exception.Message
        }
    }
}

function Run-Conda([string[]]$CondaArgs) {
    $display = ($CondaArgs -join " ")
    Write-Host "conda $display" -ForegroundColor DarkGray
    & conda @CondaArgs
    if ($LASTEXITCODE -ne 0) {
        throw "conda command failed: conda $display"
    }
}

try {
    $channel = "$NexusBase/repository/$RepoName/$ChannelPath"
    $noarch = "$channel/noarch/repodata.json"
    $win64 = "$channel/win-64/repodata.json"

    Write-Step "Checking conda command availability"
    $null = Get-Command conda -ErrorAction Stop
    conda --version

    Write-Step "Checking Nexus channel URLs"
    $r1 = Test-Url $noarch
    $r2 = Test-Url $win64
    $results = @($r1, $r2)
    foreach ($r in $results) {
        if ($r.Ok) {
            Write-Host "[OK]  $($r.Url) => HTTP $($r.StatusCode)" -ForegroundColor Green
        } else {
            Write-Host "[ERR] $($r.Url) => $($r.Error)" -ForegroundColor Yellow
        }
    }

    if (-not ($r1.Ok -and $r2.Ok)) {
        Write-Host "`nNexus channel probe failed. Check repository path or anonymous read permission first." -ForegroundColor Red
        exit 2
    }

    Write-Step "Resetting conda channels and forcing Nexus channel"
    try { Run-Conda @("config","--remove-key","channels") } catch {}
    try { Run-Conda @("config","--remove-key","default_channels") } catch {}
    try { Run-Conda @("config","--remove-key","custom_channels") } catch {}

    Run-Conda @("config","--add","channels",$channel)
    try { Run-Conda @("config","--remove","channels","defaults") } catch {}
    Run-Conda @("config","--set","show_channel_urls","yes")
    Run-Conda @("config","--set","ssl_verify","false")
    # Best-effort cleanup of legacy system channel entries that may cause 404.
    try { Run-Conda @("config","--system","--remove","channels","http://192.168.1.130:8081/repository/tsinghua-conda/main") } catch {}
    Run-Conda @("clean","-i","-y")

    Write-Step "Printing current conda sources and channels"
    Run-Conda @("config","--show-sources")
    Run-Conda @("config","--show","channels")

    Write-Step "Quick package index test"
    Run-Conda @("search","python")

    if (-not $SkipCreateEnv) {
        Write-Step "Creating environment: $EnvName (python=$PythonVersion)"
        Run-Conda @("create","-n",$EnvName,"python=$PythonVersion","-y")
        $envExists = (& conda env list) -match "^\s*$([regex]::Escape($EnvName))\s"
        if (-not $envExists) {
            throw "Environment '$EnvName' was not found after creation."
        }
        Write-Host "`nEnvironment created and verified. Activate with: conda activate $EnvName" -ForegroundColor Green
    } else {
        Write-Host "`nSkipCreateEnv is set; environment creation skipped." -ForegroundColor Yellow
    }

    Write-Host "`nAll done." -ForegroundColor Green
    exit 0
} catch {
    Write-Host "`nFAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Tip: run 'conda config --show-sources' and verify no repo.anaconda.com remains."
    exit 1
}
