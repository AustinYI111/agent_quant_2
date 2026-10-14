param([Parameter(Mandatory=$true)][string[]]$Include,[Parameter(Mandatory=$true)][string]$Out)
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
& (Join-Path $Root '.venv/Scripts/python.exe') -m ashare_mvp.backup backup --root $Root --include $Include --out $Out
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
