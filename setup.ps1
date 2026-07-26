# ==============================================================================
# Script: setup.ps1
# Description: Automated setup script for Malta Housing AI pipeline
# ==============================================================================

$ErrorActionPreference = "Stop"

Write-Host "🚀 Rozpoczynam konfigurację środowiska Malta Housing AI..." -ForegroundColor Cyan

# 1. Sprawdzanie obecności Pythona
Write-Host "`n[1/5] Sprawdzanie instalacji Pythona..." -ForegroundColor Yellow
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "❌ BŁĄD: Python nie został znaleziony w systemie! Zainstaluj Python 3.10+ i dodaj go do PATH." -ForegroundColor Red
    exit 1
}
$pythonVersion = python --version
Write-Host "  └─ Znaleziono: $pythonVersion" -ForegroundColor Green

# 2. Sprawdzanie obecności Ollamy
Write-Host "`n[2/5] Sprawdzanie instalacji Ollama..." -ForegroundColor Yellow
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "⚠️ OSTRZEŻENIE: Ollama nie jest zainstalowana lub brakuje jej w PATH." -ForegroundColor Red
    Write-Host "   Pobierz i zainstaluj Ollama z: https://ollama.com/" -ForegroundColor Yellow
} else {
    Write-Host "  └─ Znaleziono Ollama w systemie." -ForegroundColor Green
    Write-Host "  └─ Sprawdzanie/Pobieranie modelu Qwen 2.5 (7B)..." -ForegroundColor Yellow
    ollama pull qwen2.5:7b
}

# 3. Tworzenie i aktywacja wirtualnego środowiska Python (venv)
Write-Host "`n[3/5] Konfiguracja środowiska wirtualnego (venv)..." -ForegroundColor Yellow
if (-not (Test-Path "venv")) {
    python -m venv venv
    Write-Host "  └─ Utworzono katalog 'venv'." -ForegroundColor Green
} else {
    Write-Host "  └─ Środowisko 'venv' już istnieje." -ForegroundColor Green
}

# Ścieżka do Pythona i PIPa wewnątrz venv
$venvPython = ".\venv\Scripts\python.exe"
$venvPip = ".\venv\Scripts\pip.exe"

# 4. Instalacja wymaganych bibliotek (requirements)
Write-Host "`n[4/5] Instalacja pakietów Python..." -ForegroundColor Yellow

if (-not (Test-Path "requirements.txt")) {
    Write-Host "❌ BŁĄD: Brak pliku requirements.txt w repozytorium." -ForegroundColor Red
    exit 1
}

& $venvPip install --upgrade pip | Out-Null
& $venvPip install -r requirements.txt
Write-Host "  └─ Pomyślnie zainstalowano wszystkie zależności." -ForegroundColor Green

# 5. Inicjalizacja struktury bazy danych (tylko schema — bez parsed JSON)
Write-Host "`n[5/5] Inicjalizacja bazy danych SQLite..." -ForegroundColor Yellow
& $venvPython -m malta_housing init-db
Write-Host "  └─ Baza danych jest gotowa do użycia." -ForegroundColor Green

Write-Host "`n==================================================================" -ForegroundColor Cyan
Write-Host "✅ INSTALACJA ZAKOŃCZONA SUKCESEM!" -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "Aby rozpocząć pracę z projektem, aktywuj środowisko:" -ForegroundColor White
Write-Host "   .\venv\Scripts\Activate.ps1" -ForegroundColor Yellow
Write-Host "`nPełny pipeline (przykład):" -ForegroundColor White
Write-Host "   python -m malta_housing run --source maltapark --pages 3" -ForegroundColor Yellow
Write-Host "`nAlbo kroki osobno:" -ForegroundColor White
Write-Host "   python -m malta_housing scrape --source ownersbest --pages 3" -ForegroundColor Yellow
Write-Host "   python -m malta_housing parse" -ForegroundColor Yellow
Write-Host "   python -m malta_housing db" -ForegroundColor Yellow
Write-Host "`nPrzeglądarka bazy (HTML):" -ForegroundColor White
Write-Host "   python -m malta_housing serve" -ForegroundColor Yellow
Write-Host "   http://127.0.0.1:8765" -ForegroundColor Yellow
Write-Host "==================================================================`n" -ForegroundColor Cyan
