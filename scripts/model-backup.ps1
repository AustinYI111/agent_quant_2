param([Parameter(Mandatory=$true)][string]$Run,[Parameter(Mandatory=$true)][string]$Out)
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
& (Join-Path $Root '.venv/Scripts/python.exe') -m ashare_mvp.model_registry export --run $Run --out $Out
if ($LASTEXITCODE -ne 0) { throw 'Model export failed.' }
