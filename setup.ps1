param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$PythonReady=$false
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 -c 'import struct; assert struct.calcsize("P") == 8'
    $PythonReady=($LASTEXITCODE -eq 0)
}
if (-not $PythonReady) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw 'Install Python 3.12 from python.org, then rerun setup.ps1.' }
    & winget install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
}
$env:Path=[Environment]::GetEnvironmentVariable("Path","Machine")+";"+[Environment]::GetEnvironmentVariable("Path","User")+";"+$env:Path
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    & winget install --id Git.Git --exact --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Git installation failed." }
    $env:Path=[Environment]::GetEnvironmentVariable("Path","Machine")+";"+[Environment]::GetEnvironmentVariable("Path","User")+";"+$env:Path
}
& py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
$Python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
& $Python -m pip install -r requirements-operations.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $Python -m pip install --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Project installation failed.' }
if (-not $SkipTests) {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
}
Write-Host 'Installation complete. No model retraining was performed.'
