import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// The GNU Host imports this DLL; the WebView2 Runtime does not install it for us.
// Resolve the SDK from Cargo.lock instead of finding a DLL on the developer's PATH.
const frontend = fileURLToPath(new URL('../', import.meta.url))
const requestedArch = process.env.TAURI_ENV_ARCH ?? process.arch
const architectures = {
  x64: ['x64', 'x86_64', 0x8664],
  x86_64: ['x64', 'x86_64', 0x8664],
  ia32: ['x86', 'i686', 0x014c],
  x86: ['x86', 'i686', 0x014c],
  i686: ['x86', 'i686', 0x014c],
  arm64: ['arm64', 'aarch64', 0xaa64],
  aarch64: ['arm64', 'aarch64', 0xaa64],
}
const selected = architectures[requestedArch]
if (!selected) throw new Error(`Unsupported Windows architecture: ${requestedArch}`)
const [sdkArch, rustArch, machine] = selected
const host = execFileSync('rustc', ['-vV'], { encoding: 'utf8' }).match(/^host: (.+)$/m)?.[1]
const targetEnvironment = host?.includes('-pc-windows-gnu') ? 'gnu' : 'msvc'
const metadataTarget = process.env.CARGO_BUILD_TARGET ?? `${rustArch}-pc-windows-${targetEnvironment}`
const metadata = JSON.parse(execFileSync('cargo', [
  // A fresh builder has no SDK crate yet. Locked metadata fetches only the
  // resolved dependencies before Tauri's build script validates resources.
  'metadata', '--locked', '--format-version', '1', '--features', 'desktop',
  '--manifest-path', resolve(frontend, 'src-tauri/Cargo.toml'),
  '--filter-platform', metadataTarget,
], { cwd: frontend, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }))
const packages = metadata.packages.filter(pkg => pkg.name === 'webview2-com-sys')
if (packages.length !== 1) throw new Error('Expected one locked webview2-com-sys SDK')
const sdk = packages[0]
const source = resolve(dirname(sdk.manifest_path), sdkArch, 'WebView2Loader.dll')
const bytes = readFileSync(source)
if (bytes.length < 64 || bytes.toString('ascii', 0, 2) !== 'MZ') throw new Error('Invalid WebView2 loader')
const pe = bytes.readUInt32LE(0x3c)
if (pe + 6 > bytes.length || bytes.readUInt32LE(pe) !== 0x00004550 || bytes.readUInt16LE(pe + 4) !== machine) {
  throw new Error(`WebView2 loader does not match ${sdkArch}`)
}
let certificate = null
if (process.platform === 'win32') {
  certificate = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', `
    $ErrorActionPreference = 'Stop'
    $signature = Get-AuthenticodeSignature -LiteralPath $env:OPENNEXUS_TASK_LOADER_PATH
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'CN=Microsoft Corporation,') {
      throw 'WebView2Loader.dll must have a valid Microsoft signature'
    }
    Write-Output $signature.SignerCertificate.Thumbprint
  `], { encoding: 'utf8', env: {
    ...process.env,
    PSModulePath: resolve(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/Modules'),
    OPENNEXUS_TASK_LOADER_PATH: source,
  } }).trim()
}
const output = resolve(frontend, '../.build/windows')
mkdirSync(output, { recursive: true })
writeFileSync(resolve(output, 'WebView2Loader.dll'), bytes)
const sha256 = createHash('sha256').update(bytes).digest('hex')
writeFileSync(resolve(output, 'webview2-loader.json'), JSON.stringify({
  architecture: sdkArch, crate: sdk.name, version: sdk.version, sha256, certificate,
}, null, 2) + '\n')
console.log(`Prepared ${sdkArch} WebView2Loader.dll from ${sdk.name} ${sdk.version}; SHA-256 ${sha256}`)
