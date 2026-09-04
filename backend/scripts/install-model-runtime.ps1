param(
    [ValidateSet('cpu', 'cuda')][string]$Device = 'cpu',
    [string]$RuntimeDirectory = '',
    [switch]$QuietProgress
)
$ErrorActionPreference = 'Stop'
$uvOptions = if ($QuietProgress) { @('--quiet') } else { @() }
$backendRoot = Split-Path $PSScriptRoot -Parent
$runtimeRoot = if ($RuntimeDirectory) { [IO.Path]::GetFullPath($RuntimeDirectory) } else { Join-Path $backendRoot '.venv-models' }
$runtimePython = Join-Path $runtimeRoot 'Scripts/python.exe'
if (!(Test-Path -LiteralPath $runtimePython)) {
    & uv venv --python 3.12 $runtimeRoot
    if ($LASTEXITCODE -ne 0) { throw '无法创建模型运行环境' }
}
# CPU is the default. CUDA wheels include the runtime, not the NVIDIA driver.
$torchIndex = if ($Device -eq 'cuda') { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }
$wheelVariant = if ($Device -eq 'cuda') { 'cu128' } else { 'cpu' }
# Pin the local version too: ==2.9.1 alone also accepts an already-installed CPU wheel.
Write-Output 'COMPONENT:torch'
& uv @uvOptions pip install --python $runtimePython --index-url $torchIndex "torch==2.9.1+$wheelVariant" "torchaudio==2.9.1+$wheelVariant"
if ($LASTEXITCODE -ne 0) { throw 'PyTorch 安装失败' }
Write-Output 'COMPONENT:dependencies'
& uv @uvOptions pip install --python $runtimePython -r (Join-Path $PSScriptRoot 'model-requirements.lock') -c (Join-Path $PSScriptRoot 'model-requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '模型依赖安装失败' }
Write-Output 'COMPONENT:verify'
& $runtimePython -c 'import torch; print({"torch":torch.__version__,"cuda_available":torch.cuda.is_available()})'
if ($LASTEXITCODE -ne 0) { throw '模型运行环境检查失败' }
