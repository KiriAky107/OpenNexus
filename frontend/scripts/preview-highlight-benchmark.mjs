// Run after `pnpm build`: node scripts/preview-highlight-benchmark.mjs [1000 5000]
// Measures production Worker transport and HTML parity, not browser input latency.
import { readdirSync } from 'node:fs'
import { Worker } from 'node:worker_threads'
import { performance } from 'node:perf_hooks'
import { createHighlighterCore } from 'shiki/core'
import { createOnigurumaEngine } from 'shiki/engine/oniguruma'
import typescript from '@shikijs/langs/typescript'
import githubLight from '@shikijs/themes/github-light'
import githubDark from '@shikijs/themes/github-dark'

const assets = new URL('../dist/assets/', import.meta.url)
const file = readdirSync(assets).find(name => /^previewHighlight\.worker-.*\.js$/.test(name))
if (!file) throw new Error('Build the frontend before measuring the production Worker')
const worker = new Worker(`
  const { parentPort } = require('node:worker_threads');
  globalThis.self = { postMessage: value => parentPort.postMessage(value) };
  import(${JSON.stringify(new URL(file, assets).href)}).then(() => {
    parentPort.on('message', data => self.onmessage({data})); parentPort.postMessage({ready:true});
  });
`, { eval: true })
let nextId = 0, readyResolve, readyReject
const waiting = new Map()
const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject })
worker.on('message', message => {
  if (message.ready) return readyResolve()
  const pending = waiting.get(message.id)
  waiting.delete(message.id)
  if (pending) { clearTimeout(pending.timer); pending.resolve(message) }
})
worker.on('error', error => {
  readyReject(error)
  for (const pending of waiting.values()) { clearTimeout(pending.timer); pending.reject(error) }
  waiting.clear()
})
async function background(source) {
  const id = ++nextId, started = performance.now()
  const result = new Promise((resolve, reject) => {
    const timer = setTimeout(() => { waiting.delete(id); reject(new Error('Worker reply timed out')) }, 30000)
    waiting.set(id, { resolve, reject, timer })
  })
  worker.postMessage({ id, source, language: 'typescript' })
  const postMs = performance.now() - started
  const response = await result
  if (!response.html) throw new Error('Production Worker unexpectedly returned plain-text fallback')
  return { post_ms: postMs, round_trip_ms: performance.now() - started, html: response.html }
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
try {
  await ready
  const highlighter = await createHighlighterCore({ themes: [githubLight, githubDark], langs: [typescript], engine: createOnigurumaEngine(import('shiki/wasm')) })
  console.log(JSON.stringify({ measurement: 'Preview full-document Shiki CPU vs production Worker transport; not UI input latency', node: process.version, worker: file }))
  for (const lines of (process.argv.slice(2).length ? process.argv.slice(2).map(Number) : [1000, 5000])) {
    if (!Number.isSafeInteger(lines) || lines <= 0) throw new Error('Line count must be a positive integer')
    const source = Array.from({ length: lines }, (_, i) => `export const value${i}: number = Math.max(${i}, 1) + 2; // demo`).join('\n')
    const samples = []
    for (let repeat = 0; repeat < 3; repeat++) {
      const start = performance.now()
      const html = highlighter.codeToHtml(source, { lang: 'typescript', themes: { light: 'github-light', dark: 'github-dark' }, defaultColor: false })
      const baseline = performance.now() - start
      const result = await background(source)
      if (result.html !== html) throw new Error('Worker output differs from synchronous Shiki')
      samples.push({ baseline_cpu_ms: baseline, post_ms: result.post_ms, round_trip_ms: result.round_trip_ms })
    }
    console.log(JSON.stringify({ lines, characters: source.length, identical_html: true, samples,
      baseline_cpu_median_ms: median(samples.map(s => s.baseline_cpu_ms)),
      worker_post_median_ms: median(samples.map(s => s.post_ms)),
      worker_round_trip_median_ms: median(samples.map(s => s.round_trip_ms)),
    }))
  }
} finally { await worker.terminate() }
