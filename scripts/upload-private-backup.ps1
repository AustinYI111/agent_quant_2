param([Parameter(Mandatory=$true)][string]$Archive,[Parameter(Mandatory=$true)][string]$Tag,[string]$Repository='AustinYI111/agent_quant_2-backups')
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
& (Join-Path $Root '.venv/Scripts/python.exe') -m ashare_mvp.github_backup --repository $Repository upload --archive $Archive --tag $Tag
if ($LASTEXITCODE -ne 0) { throw 'Private GitHub backup upload failed.' }
