$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"
$stamp = Join-Path $venv ".deps-installed"

if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv $venv
}

if (-not (Test-Path $stamp)) {
    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet -r (Join-Path $root "requirements.txt")
    New-Item -ItemType File -Path $stamp -Force | Out-Null
}

$env:PYTHONIOENCODING = "utf-8"
Write-Host ""
Write-Host "  BidGapCheck  ->  http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000