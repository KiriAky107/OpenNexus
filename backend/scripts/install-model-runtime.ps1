param(
    [ValidateSet('cpu', 'cuda')][string]$Device = 'cpu'
)
$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path $PSScriptRoot -Parent
$runtimeRoot = Join-Path $backendRoot '.venv-models'
$runtimePython = Join-Path $runtimeRoot 'Scripts/python.exe'
if (!(Test-Path -LiteralPath $runtimePython)) {
    & uv venv --python 3.12 $runtimeRoot
    if ($LASTEXITCODE -ne 0) { throw '无法创建模型运行环境' }
}
# CPU is the default. CUDA wheels include the runtime, not the NVIDIA driver.
$torchIndex = if ($Device -eq 'cuda') { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }
& uv pip install --python $runtimePython --index-url $torchIndex 'torch==2.9.1' 'torchaudio==2.9.1'
if ($LASTEXITCODE -ne 0) { throw 'PyTorch 安装失败' }
& uv pip install --python $runtimePython -r (Join-Path $PSScriptRoot 'model-requirements.lock') -c (Join-Path $PSScriptRoot 'model-requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '模型依赖安装失败' }
& $runtimePython -c 'import torch; print({"torch":torch.__version__,"cuda_available":torch.cuda.is_available()})'
if ($LASTEXITCODE -ne 0) { throw '模型运行环境检查失败' }
