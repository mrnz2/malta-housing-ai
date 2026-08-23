# One-time backfill of listings.area_sqm from raw text, key_features, titles, etc.
# Visible listings only; hidden listings are ignored.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Missing venv. Run .\setup.ps1 first."
}
& $python (Join-Path $root "scripts\backfill_area_once.py") @args
