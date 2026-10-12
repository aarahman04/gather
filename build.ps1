# Builds dist\Gather.exe (single file, no console).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -q -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name Gather Gather.pyw
Write-Host "Built dist\Gather.exe"
