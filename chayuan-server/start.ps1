$ErrorActionPreference = "Stop"

try {
    conda activate py312
}
catch {
    Write-Error "conda activate py312 failed. Please ensure Conda is installed and initialized for PowerShell."
    exit 1
}

Set-Location "D:\code\chayuan\chayuan-server\libs\chayuan-server\chayuan"
python cli.py start -a
