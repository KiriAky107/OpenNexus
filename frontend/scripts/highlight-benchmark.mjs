// Run after `pnpm build`: node scripts/highlight-benchmark.mjs [1000 5000]
// Synthetic input only. CPU/Worker timings are not browser input latency.
import { readdirSync } from 'node:fs'
import { Worker } from 'node:worker_threads'
import { fileURLToPath } from 'node:url'
import { performance } from 'node:perf_hooks'
import { EditorState } from '@codemirror/state'
import { Decoration } from '@codemirror/view'
import { createHighlighterCore } from 'shiki/core'
import { createOnigurumaEngine } from 'shiki/engine/oniguruma'
import typescript from '@shikijs/langs/typescript'
import python from '@shikijs/langs/python'
import githubLight from '@shikijs/themes/github-light'
import githubDark from '@shikijs/themes/github-dark'

const assets = new URL('../dist/assets/', import.meta.url)
const file = readdirSync(assets).find(name => /^codeHighlight\.worker-.*\.js$/.test(name))
if (!file) throw new Error('Build the frontend before measuring the production Worker')
const worker = new Worker(`
  const { parentPort } = require('node:worker_threads');
  globalThis.self = { postMessage: (value, options) => parentPort.postMessage(value, options.transfer) };
  import(${JSON.stringify(new URL(file, assets).href)}).then(() => {
    parentPort.on('message', data => self.onmessage({data})); parentPort.postMessage({ready:true});
  });
`, { eval: true })
const waiting = new Map()
let nextId = 0, readyResolve, readyReject
const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject })
worker.on('message', message => {
  if (message.ready) return readyResolve()
  const resolve = waiting.get(message.id); waiting.delete(message.id); resolve?.(message)
})
worker.on('error', error => { readyReject(error); throw error })

function fixture(lines) {
  return Array.from({ length: lines }, (_, index) => `export const value${index}: number = Math.max(${index}, 1) + 2; // demo`).join('\n')
}
async function background(input) {
  const id = ++nextId, start = performance.now()
  const result = new Promise(resolve => waiting.set(id, resolve))
  worker.postMessage({ ...input, id })
  const sendMs = performance.now() - start
  const response = await result
  if (!response.result) throw new Error('Highlight unexpectedly fell back to plain text')
  return { send_ms: sendMs, round_trip_ms: performance.now() - start, spans: response.result.spans.length / 3, result_bytes: response.result.spans.byteLength }
}
function baseline(highlighter, input) {
  const state = EditorState.create({ doc: input.source }), start = performance.now()
  const tokens = highlighter.codeToTokens(input.source, { lang: input.language, theme: input.theme }).tokens
  const ranges = tokens.flatMap((line, index) => {
    let offset = state.doc.line(index + 1).from
    return line.flatMap(token => {
      const from = offset; offset += token.content.length
      if (from === offset) return []
      const fontStyle = token.fontStyle ?? 0
      return [Decoration.mark({ class: 'shiki-token', attributes: { style: `color:${token.color};font-style:${fontStyle & 1 ? 'italic' : 'normal'};font-weight:${fontStyle & 2 ? 'bold' : 'normal'};text-decoration:${fontStyle & 4 ? 'underline' : 'none'}` } }).range(from, offset)]
    })
  })
  Decoration.set(ranges)
  return performance.now() - start
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
try {
  await ready
  const initializeStart = performance.now()
  const highlighter = await createHighlighterCore({ themes: [githubLight, githubDark], langs: [typescript, python], engine: createOnigurumaEngine(import('shiki/wasm')) })
  console.log(JSON.stringify({ measurement: 'Node full-document CPU vs production Worker transport; not UI input latency', node: process.version, worker: fileURLToPath(new URL(file, assets)), baseline_initialize_ms: performance.now() - initializeStart }))
  for (const lines of (process.argv.slice(2).length ? process.argv.slice(2).map(Number) : [1000, 5000])) {
    const source = fixture(lines), edited = source + 'x', pasted = source + '\n' + fixture(200)
    const cases = [['initialize', source], ['single-character', edited], ['paste-200-lines', pasted], ['undo', source], ['redo', pasted], ['dark-theme', source, 'github-dark'], ['python-language', source, 'github-light', 'python']]
    for (const [operation, content, theme = 'github-light', language = 'typescript'] of cases) {
      const input = { source: content, language, theme }, samples = []
      for (let repeat = 0; repeat < 3; repeat++) {
        const baselineMs = baseline(highlighter, input)
        samples.push({ baseline_ms: baselineMs, ...await background(input) })
      }
      console.log(JSON.stringify({ lines, operation, characters: content.length, samples: 3,
        first_baseline_cpu_ms: samples[0].baseline_ms, first_worker_round_trip_ms: samples[0].round_trip_ms,
        baseline_cpu_median_ms: median(samples.map(item => item.baseline_ms)),
        worker_post_median_ms: median(samples.map(item => item.send_ms)),
        worker_round_trip_median_ms: median(samples.map(item => item.round_trip_ms)),
        spans: samples[0].spans, result_bytes: samples[0].result_bytes,
      }))
    }
  }
} finally { await worker.terminate() }
