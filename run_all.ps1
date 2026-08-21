# ==============================================================================
# Script: run_all.ps1
# Description: Run all portal scrapers, parse, UPSERT into SQLite, rank new listings
# Usage:
#   .\run_all.ps1
#   .\run_all.ps1 -Pages 5
#   .\run_all.ps1 -Pages 3 -Force
# ==============================================================================

param(
    [int]$Pages = 3,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[ERROR] Missing venv. Run .\setup.ps1 first." -ForegroundColor Red
    exit 1
}

$sources = @(
    "maltapark"
    "ownersbest"
    "djar"
    "propertymarket"
    "yitaku"
    "remax"
    "simonmamo"
    "belair"
    "re316"
    "franksalt"
    "sensar"
)

Write-Host "Malta Housing AI - full pipeline (all scrapers -> parse -> db -> rank)" -ForegroundColor Cyan
Write-Host ("  Pages per portal: {0}" -f $Pages) -ForegroundColor Gray
if ($Force) {
    Write-Host "  Parse: --force" -ForegroundColor Gray
    Write-Host "  Rank: --new-only --force" -ForegroundColor Gray
} else {
    Write-Host "  Rank: --new-only" -ForegroundColor Gray
}
Write-Host ""

$step = 0
$total = $sources.Count + 3

foreach ($source in $sources) {
    $step = $step + 1
    Write-Host ("[{0}/{1}] Scrape: {2} ..." -f $step, $total, $source) -ForegroundColor Yellow
    & $venvPython -m malta_housing scrape --source $source --pages $Pages
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("[ERROR] Scraper '{0}' failed (exit {1})." -f $source, $LASTEXITCODE) -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host ("  OK: {0}" -f $source) -ForegroundColor Green
    Write-Host ""
}

$step = $step + 1
Write-Host ("[{0}/{1}] Parse (Ollama) ..." -f $step, $total) -ForegroundColor Yellow
if ($Force) {
    & $venvPython -m malta_housing parse --force
} else {
    & $venvPython -m malta_housing parse
}
if ($LASTEXITCODE -ne 0) {
    Write-Host ("[ERROR] Parser failed (exit {0})." -f $LASTEXITCODE) -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "  OK: parse" -ForegroundColor Green
Write-Host ""

$step = $step + 1
Write-Host ("[{0}/{1}] Save to SQLite ..." -f $step, $total) -ForegroundColor Yellow
& $venvPython -m malta_housing db
if ($LASTEXITCODE -ne 0) {
    Write-Host ("[ERROR] DB save failed (exit {0})." -f $LASTEXITCODE) -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "  OK: db" -ForegroundColor Green
Write-Host ""

$step = $step + 1
Write-Host ("[{0}/{1}] Rank new listings (Ollama) ..." -f $step, $total) -ForegroundColor Yellow
if ($Force) {
    & $venvPython -m malta_housing rank --new-only --force
} else {
    & $venvPython -m malta_housing rank --new-only
}
if ($LASTEXITCODE -ne 0) {
    Write-Host ("[ERROR] Rank failed (exit {0})." -f $LASTEXITCODE) -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "  OK: rank" -ForegroundColor Green

Write-Host ""
Write-Host "Pipeline finished." -ForegroundColor Green
Write-Host "  Browser UI: python -m malta_housing serve" -ForegroundColor Yellow
