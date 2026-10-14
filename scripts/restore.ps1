param([Parameter(Mandatory=$true)][string]$Archive,[Parameter(Mandatory=$true)][string]$Out)
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
& (Join-Path $Root '.venv/Scripts/python.exe') -m ashare_mvp.backup restore --archive $Archive --out $Out
if ($LASTEXITCODE -ne 0) { throw 'Restore failed.' }
