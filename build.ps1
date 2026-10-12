# Builds dist\Gather.exe (single file, no console).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -q -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
if ($LASTEXITCODE) { throw "tests failed" }
New-Item -ItemType Directory -Force build | Out-Null
.\.venv\Scripts\python -c "from gather.ui import _icon_image; _icon_image(False).save('build/gather.ico', sizes=[(16,16),(32,32),(48,48),(64,64)])"
.\.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name Gather --icon build\gather.ico Gather.pyw
if ($LASTEXITCODE) { throw "pyinstaller failed" }
Write-Host "Built dist\Gather.exe"
