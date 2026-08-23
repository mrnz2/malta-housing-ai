# Backfill listings.area_sqm from scraped listing text (data/scraped_listings.json).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Missing venv. Run .\setup.ps1 first."
}
& $python -m malta_housing backfill-area
