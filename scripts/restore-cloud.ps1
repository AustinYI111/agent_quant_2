param([Parameter(Mandatory=$true)][string]$Tag,[Parameter(Mandatory=$true)][string]$Asset,[Parameter(Mandatory=$true)][string]$Out,[string]$Repository='AustinYI111/agent_quant_2-backups')
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
$Python=Join-Path $Root '.venv/Scripts/python.exe'
$Download=Join-Path $Root ('backups/'+$Asset)
& $Python -m ashare_mvp.github_backup --repository $Repository download --tag $Tag --asset $Asset --out $Download
if ($LASTEXITCODE -ne 0) { throw 'Private snapshot download failed.' }
& $Python -m ashare_mvp.backup restore --archive $Download --out $Out
if ($LASTEXITCODE -ne 0) { throw 'Private snapshot restore failed.' }
