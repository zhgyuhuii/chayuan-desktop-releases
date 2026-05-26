<#
.SYNOPSIS
  Add Chayuan installation paths to Windows Defender exclusions.

.NOTES
  Must run elevated (Administrator).
#>
param(
  [string]$Home = $env:LOCALAPPDATA + "\Chayuan"
)

if (-not (Test-Path $Home)) {
    Write-Host "Chayuan home not found at $Home — skip"
    exit 0
}

Write-Host "Adding Defender exclusions for: $Home"
Add-MpPreference -ExclusionPath  $Home
Add-MpPreference -ExclusionPath  (Join-Path $Home "vendor")
Add-MpPreference -ExclusionPath  (Join-Path $Home "models")
Add-MpPreference -ExclusionProcess (Join-Path $Home "vendor\runtimes\python\python.exe")
Add-MpPreference -ExclusionProcess (Join-Path $Home "vendor\services\ollama\ollama.exe")

Write-Host "Done. Run 'chayuan doctor' to confirm."
