param([Parameter(Mandatory=$true)][string]$Model)
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
& (Join-Path $Root '.venv/Scripts/python.exe') -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency conflict.' }
& (Join-Path $Root '.venv/Scripts/python.exe') -m ashare_mvp.model_registry check --model $Model
if ($LASTEXITCODE -ne 0) { throw 'Model incompatible or corrupted.' }
