$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Target = $env:CHAYUAN_HOME
if (-not $Target) { $Target = Join-Path $env:LOCALAPPDATA "Chayuan" }

Write-Host "Installing Chayuan into $Target ..."
New-Item -ItemType Directory -Force -Path $Target | Out-Null

robocopy $Root $Target /E /XD .git dist __pycache__ | Out-Null
Set-Location $Target

if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --system -e .
} else {
    python -m pip install -e .
}
Write-Host "Installed. Try:  `$env:CHAYUAN_HOME=`"$Target`"; python -m chayuan_cli info"
