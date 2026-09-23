param(
    [ValidateSet('cpu', 'cuda')][string]$Device = 'cpu',
    [string]$RuntimeDirectory = '',
    [string]$UvPath = 'uv',
    [string]$PythonDirectory = '',
    [switch]$QuietProgress
)
$ErrorActionPreference = 'Stop'
# Without PATHEXT, Windows PowerShell can launch .exe files through the shell
# without waiting or setting LASTEXITCODE in the desktop's isolated environment.
$env:PATHEXT = '.COM;.EXE;.BAT;.CMD'
[string[]]$uvOptions = @('--no-config')
if ($QuietProgress) { $uvOptions += '--quiet' }
if ($PythonDirectory) {
    $env:UV_PYTHON_INSTALL_DIR = [IO.Path]::GetFullPath($PythonDirectory)
}
# Python and environment files must survive cache cleanup and app upgrades.
$env:UV_LINK_MODE = 'copy'
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$backendRoot = Split-Path $PSScriptRoot -Parent
$runtimeRoot = if ($RuntimeDirectory) { [IO.Path]::GetFullPath($RuntimeDirectory) } else { Join-Path $backendRoot '.venv-models' }
$runtimePython = Join-Path $runtimeRoot 'Scripts/python.exe'
$runtimeHealthy = $false
if (Test-Path -LiteralPath $runtimePython) {
    try {
        & $runtimePython -c 'import sys' 2>$null
        $runtimeHealthy = ($LASTEXITCODE -eq 0)
    } catch { $runtimeHealthy = $false }
}
if (!$runtimeHealthy) {
    Write-Output 'COMPONENT:python'
    # Repair an interrupted venv or a missing base interpreter without deleting data.
    & $UvPath @uvOptions venv --allow-existing --python 3.12 $runtimeRoot
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the model runtime' }
}
# CPU is the default. CUDA wheels include runtime libraries, not NVIDIA drivers.
$torchIndex = if ($Device -eq 'cuda') { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }
$wheelVariant = if ($Device -eq 'cuda') { 'cu128' } else { 'cpu' }
# Pin the local version as well: ==2.9.1 alone also accepts CPU wheels.
Write-Output 'COMPONENT:torch'
& $UvPath @uvOptions pip install --python $runtimePython --index-url $torchIndex "torch==2.9.1+$wheelVariant" "torchaudio==2.9.1+$wheelVariant"
if ($LASTEXITCODE -ne 0) { throw 'PyTorch installation failed' }
Write-Output 'COMPONENT:dependencies'
& $UvPath @uvOptions pip install --python $runtimePython --index-url https://pypi.org/simple -r (Join-Path $PSScriptRoot 'model-requirements.lock') -c (Join-Path $PSScriptRoot 'model-requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Model dependency installation failed' }
Write-Output 'COMPONENT:verify'
& $runtimePython -c 'import torch; print(dict(torch=torch.__version__,cuda_available=torch.cuda.is_available()))'
if ($LASTEXITCODE -ne 0) { throw 'Model runtime verification failed' }
