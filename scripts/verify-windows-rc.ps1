$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bundle = Join-Path $root 'frontend\src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis'
$installers = @(Get-ChildItem -LiteralPath $bundle -Filter '*.exe' -File)
if ($installers.Count -ne 1) { throw "应恰好生成一个 NSIS 安装包，实际为 $($installers.Count)" }
$installer = $installers[0]
if ($installer.Length -gt 300MB) { throw "基础安装包超过 300 MiB：$($installer.Length)" }

$hostExecutable = Join-Path $root 'frontend\src-tauri\target\x86_64-pc-windows-msvc\release\notesagent-desktop.exe'
if (-not (Test-Path -LiteralPath $hostExecutable -PathType Leaf)) { throw '缺少 MSVC Host 可执行文件' }
foreach ($path in @($hostExecutable, $installer.FullName)) {
  $signature = Get-AuthenticodeSignature -LiteralPath $path
  if ($signature.Status -ne 'Valid') { throw "Authenticode 签名无效：$path ($($signature.Status))" }
  if ($signature.SignerCertificate.Thumbprint -ne $env:OPENNEXUS_WINDOWS_CERTIFICATE_THUMBPRINT) {
    throw "签名证书与受控证书不匹配：$path"
  }
}

$manifest = Join-Path $root '.build\sidecar\manifest.json'
$manifestSignature = Join-Path $root '.build\sidecar\manifest.sig'
$publicKey = Join-Path $root '.build\sidecar\public-key.hex'
foreach ($path in @($manifest, $manifestSignature, $publicKey)) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "缺少 Core 发布文件：$path" }
}
if ((Get-Item -LiteralPath $manifestSignature).Length -ne 64) { throw 'Core 清单签名长度必须为 64 字节' }
if ((Get-Content -LiteralPath $publicKey -Raw).Trim() -notmatch '^[0-9a-f]{64}$') { throw 'Core 发布公钥格式无效' }

$result = [ordered]@{
  schema = 1
  product = 'OpenNexus'
  target = 'x86_64-pc-windows-msvc'
  installer_bytes = $installer.Length
  certificate_thumbprint = $env:OPENNEXUS_WINDOWS_CERTIFICATE_THUMBPRINT
  files = [ordered]@{}
}
foreach ($path in @($installer.FullName, $hostExecutable, $manifest, $manifestSignature, $publicKey)) {
  $relative = [IO.Path]::GetRelativePath($root, $path).Replace('\', '/')
  $result.files[$relative] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
$output = Join-Path $root '.build\windows-rc-sha256.json'
[IO.File]::WriteAllText($output, ($result | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
Write-Host "Windows RC 校验通过：$($installer.Name)，$([math]::Round($installer.Length / 1MB, 2)) MiB"
