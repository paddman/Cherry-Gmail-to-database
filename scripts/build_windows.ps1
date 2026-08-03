$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".venv")) {
    py -3.11 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean CherryMailMemory.spec

Write-Host "Build complete: dist\CherryMailMemory" -ForegroundColor Green
