import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'
const root = fileURLToPath(new URL('../dist/', import.meta.url))
const manifest = JSON.parse(readFileSync(resolve(root, '.vite/manifest.json'), 'utf8'))
const files = new Map(Object.values(manifest).filter(x => x.file.endsWith('.js')).map(x => [x.file, x]))
const sizes = file => { const bytes = readFileSync(resolve(root, file)); return { bytes: bytes.length, gzip: gzipSync(bytes).length } }
const closure = key => {
  const seen = new Set()
  function visit(k) { if (seen.has(k)) return; seen.add(k); for (const i of manifest[k]?.imports ?? []) visit(i) }
  visit(key)
  return [...new Set([...seen].map(k => manifest[k]?.file).filter(f => f?.endsWith('.js')))]
}
const entries = Object.entries(manifest).filter(([key, value]) => value.isEntry || /(?:WorkspaceView|VisualMarkdownEditor|VaultEntry|ChatView)\.vue$/.test(key)).map(([key]) => {
  const files = closure(key)
  return { entry: key, files, bytes: files.reduce((n, f) => n + sizes(f).bytes, 0), gzip: files.reduce((n, f) => n + sizes(f).gzip, 0) }
})
console.log(JSON.stringify({ entries, largest: [...files.keys()].map(file => ({ file, ...sizes(file) })).sort((a,b) => b.bytes-a.bytes).slice(0,15), chunks: files.size }, null, 2))
