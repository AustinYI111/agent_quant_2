param([Parameter(Mandatory=$true)][string]$Bundle,[Parameter(Mandatory=$true)][string]$Tag,[switch]$ConfirmPublic)
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
& (Join-Path $Root '.venv/Scripts/python.exe') -m ashare_mvp.model_registry check --model $Bundle
if ($LASTEXITCODE -ne 0) { throw 'Model validation failed.' }
$Metadata=Get-Content -LiteralPath (Join-Path $Bundle 'model.json') -Raw | ConvertFrom-Json
if ($Metadata.synthetic -or -not $Metadata.formal_release_approved -or -not $ConfirmPublic) { throw 'Only owner-approved real models reviewed for public disclosure may be released.' }
if (-not $Metadata.code_git_commit -or $Metadata.code_dirty) { throw 'Formal release requires a committed, reproducible code reference.' }
if ($Tag -notmatch '^model-[A-Za-z0-9._-]+$') { throw 'Use an explicit model-* release tag.' }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'GitHub CLI authentication is required for release publishing.' }
$Archive=Join-Path $Root ('backups/'+$Tag+'.zip')
if (Test-Path -LiteralPath $Archive) { throw 'Release archive already exists.' }
New-Item -ItemType Directory -Force (Split-Path $Archive -Parent) | Out-Null
$Files=@('model.txt','model.json','summary.json','training_audit.csv','cohort_performance.csv','factor_importance.csv') | ForEach-Object { Join-Path $Bundle $_ }
Compress-Archive -LiteralPath $Files -DestinationPath $Archive
if ((Get-Item -LiteralPath $Archive).Length -ge 2GB) { throw 'Release asset must be under 2 GiB.' }
& gh release create $Tag $Archive --target $Metadata.code_git_commit --repo AustinYI111/agent_quant_2 --draft --title $Tag --notes 'Owner-approved model bundle. Metadata, feature contract, dependencies and rolling evaluation included. Results are not a profitability guarantee.'
if ($LASTEXITCODE -ne 0) { throw 'Release creation failed.' }
