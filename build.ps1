# Builds dist\Gather.exe (single file, no console).
param([switch]$NoDesktop)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (Get-Process Gather -ErrorAction SilentlyContinue) {
    throw "Quit the running Gather.exe before building or testing."
}
if (-not (Test-Path .venv)) {
    python -m venv .venv
    if ($LASTEXITCODE) { throw "venv creation failed" }
}
.\.venv\Scripts\python -c "import sys; assert sys.version_info[:3] == (3, 12, 7), 'Use Python 3.12.7'"
if ($LASTEXITCODE) { throw "wrong Python version" }
.\.venv\Scripts\python -m pip install -q -r requirements-dev.txt
if ($LASTEXITCODE) { throw "dependency installation failed" }
$testArgs = @('-q')
if ($NoDesktop) { $testArgs += '--no-desktop' }
.\.venv\Scripts\python -m pytest @testArgs
if ($LASTEXITCODE) { throw "tests failed" }
New-Item -ItemType Directory -Force build | Out-Null
.\.venv\Scripts\python -c "from gather.ui import _icon_image; _icon_image(False).save('build/gather.ico', sizes=[(16,16),(32,32),(48,48),(64,64)])"
if ($LASTEXITCODE) { throw "icon generation failed" }
.\.venv\Scripts\pyinstaller --clean --noconfirm --onefile --windowed --noupx --name Gather --icon build\gather.ico --version-file resources\windows-version.txt --add-data "LICENSE;." --add-data "THIRD_PARTY_NOTICES.md;." --add-data "docs\licenses;licenses" Gather.pyw
if ($LASTEXITCODE) { throw "pyinstaller failed" }
$version = .\.venv\Scripts\python -c "from gather import __version__; print(__version__)"
if ($LASTEXITCODE) { throw "version lookup failed" }
$info = (Get-Item dist\Gather.exe).VersionInfo
if ($info.ProductName -ne 'Gather' -or $info.ProductVersion -ne $version -or $info.FileVersion -ne $version) {
    throw "EXE version metadata does not match Gather $version"
}
Compress-Archive -Path LICENSE, THIRD_PARTY_NOTICES.md, docs\licenses -DestinationPath dist\Gather-licenses.zip -Force
$sources = Get-Content resources\lgpl-sources.json -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force build\dependency-sources | Out-Null
foreach ($source in $sources) {
    $path = Join-Path build\dependency-sources $source.file
    if (-not (Test-Path $path) -or (Get-FileHash $path -Algorithm SHA256).Hash -ne $source.sha256) {
        Invoke-WebRequest -Uri $source.url -OutFile $path
    }
    if ((Get-FileHash $path -Algorithm SHA256).Hash -ne $source.sha256) { throw "Source checksum mismatch: $path" }
}
$sourcePaths = @($sources | ForEach-Object { Join-Path build\dependency-sources $_.file })
$sourcePaths += @('resources\lgpl-sources.json', 'THIRD_PARTY_NOTICES.md')
Compress-Archive -Path $sourcePaths -DestinationPath dist\Gather-dependency-sources.zip -Force
Write-Host "Built dist\Gather.exe"
