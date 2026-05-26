$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$envFile = Join-Path $Root "config\default.env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -and ($_ -notmatch '^\s*#') } | ForEach-Object {
        $kv = $_ -split '=', 2
        if ($kv.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($kv[0].Trim(), $kv[1].Trim(), "Process")
        }
    }
}

$PY = $env:CHAYUAN_PYTHON
if (-not $PY) { $PY = "python" }

& $PY -m chayuan_supervisor up --foreground $args
