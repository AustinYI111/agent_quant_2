param([Parameter(Mandatory=$true)][string]$Model)
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
& (Join-Path $Root '.venv/Scripts/python.exe') -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency conflict.' }
if ((Test-Path -LiteralPath (Join-Path $Model 'metadata.json')) -and
    (Test-Path -LiteralPath (Join-Path $Model 'feature_input_policy.json'))) {
    & (Join-Path $Root '.venv/Scripts/python.exe') -m ashare_mvp.horizon_inference check --model $Model
} else {
    & (Join-Path $Root '.venv/Scripts/python.exe') -m ashare_mvp.model_registry check --model $Model
}
if ($LASTEXITCODE -ne 0) { throw 'Model incompatible or corrupted.' }
