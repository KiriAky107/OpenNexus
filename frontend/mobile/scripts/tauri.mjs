import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { access, copyFile, mkdir } from 'node:fs/promises'
import { dirname } from 'node:path'

const cli = fileURLToPath(new URL('../../node_modules/@tauri-apps/cli/tauri.js', import.meta.url))
const cwd = fileURLToPath(new URL('..', import.meta.url))
const args = process.argv.slice(2)
function run(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [cli, 'android', ...args], { cwd, stdio: 'inherit' })
    child.on('error', reject)
    child.on('exit', (code, signal) => code === 0 && !signal ? resolve() : reject(Error(`Android command failed (${signal || code})`)))
  })
}
async function nativeFiles() {
  const source = fileURLToPath(new URL('../android/MobileFilesPlugin.kt', import.meta.url))
  const destination = fileURLToPath(new URL('../src-tauri/gen/android/app/src/main/java/cc/kronecker/opennexus/files/MobileFilesPlugin.kt', import.meta.url))
  await mkdir(dirname(destination), { recursive: true })
  await copyFile(source, destination)
}
try {
  const help = args.includes('--help') || args.includes('-h') || args.includes('--version')
  if (!help && ['build','dev'].includes(args[0])) {
    const project = fileURLToPath(new URL('../src-tauri/gen/android/settings.gradle', import.meta.url))
    try { await access(project) } catch { await run(['init','--ci','--skip-targets-install']) }
    await nativeFiles()
  }
  await run(args)
  if (!help && args[0] === 'init') await nativeFiles()
} catch (error) { console.error(error.message); process.exitCode = 1 }
