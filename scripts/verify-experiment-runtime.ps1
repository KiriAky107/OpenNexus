param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    & $Python scripts/test_prepare_experiment_runtime.py
    if ($LASTEXITCODE -ne 0) { throw 'Runtime preparation negative tests failed' }
    & $Python scripts/prepare-experiment-runtime.py
    if ($LASTEXITCODE -ne 0) { throw 'Runtime preparation failed' }
    $lock = Get-Content scripts/experiment-runtime-lock.json -Raw | ConvertFrom-Json
    $runtimeDir = Join-Path $projectRoot ".build/experiment-runtime/$($lock.runtime_id)"
    $signatures = @(Get-ChildItem -LiteralPath $runtimeDir -File | Where-Object Extension -in '.exe','.dll','.pyd' | Get-AuthenticodeSignature)
    if ($signatures.Count -eq 0 -or @($signatures | Where-Object Status -ne Valid).Count -ne 0) {
        throw 'Embedded runtime native signature verification failed'
    }
    $signatures | Select-Object @{n='file';e={Split-Path $_.Path -Leaf}},@{n='status';e={$_.Status.ToString()}},@{n='signer';e={$_.SignerCertificate.Subject}} |
        ConvertTo-Json | Set-Content .build/experiment-runtime/signatures.json -Encoding utf8
    $priorToken = $env:OPENNEXUS_PROBE_PARENT_TOKEN
    $env:OPENNEXUS_PROBE_PARENT_TOKEN = 'synthetic-parent-token-never-inherited'
    try {
        Push-Location frontend/src-tauri
        try {
            cargo test --lib --release --locked experiment_runtime_probe:: -- --ignored --test-threads=1 --nocapture
            if ($LASTEXITCODE -ne 0) { throw 'Native AppContainer runtime probe failed' }
        } finally { Pop-Location }
    } finally { $env:OPENNEXUS_PROBE_PARENT_TOKEN = $priorToken }
} finally { Pop-Location }
