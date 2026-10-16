import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const cli = fileURLToPath(new URL('../../node_modules/@tauri-apps/cli/tauri.js', import.meta.url))
const cwd = fileURLToPath(new URL('..', import.meta.url))
const child = spawn(process.execPath, [cli, 'android', ...process.argv.slice(2)], { cwd, stdio: 'inherit' })
child.on('error', error => { console.error(error.message); process.exitCode = 1 })
child.on('exit', (code, signal) => { process.exitCode = signal ? 1 : code ?? 1 })
