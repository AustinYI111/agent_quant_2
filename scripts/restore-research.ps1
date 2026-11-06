param([Parameter(Mandatory=$true)][string]$Plan)
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
$Python=Join-Path $Root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run setup.ps1 first.' }
& $Python -m ashare_mvp.restore_research --project $Root --plan $Plan
if ($LASTEXITCODE -ne 0) { throw 'Research restore failed; existing differing files are not overwritten.' }
