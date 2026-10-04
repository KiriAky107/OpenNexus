import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { PreviewHighlighter } from './previewHighlighter'
import { plainCode, type PreviewHighlightRequest, type PreviewHighlightResponse } from './previewHighlightProtocol'
import { computePreviewHighlight } from './previewHighlightProcessor'

class WorkerStub {
  onmessage: ((event: MessageEvent<PreviewHighlightResponse>) => void) | null = null
  onerror: ((event: ErrorEvent) => void) | null = null
  onmessageerror: ((event: MessageEvent) => void) | null = null
  postMessage = vi.fn<(input: PreviewHighlightRequest) => void>()
  terminate = vi.fn()
  reply(index = this.postMessage.mock.calls.length - 1, html = '<pre>colored</pre>') {
    this.onmessage?.({ data: { id: this.postMessage.mock.calls[index]![0].id, html } } as MessageEvent<PreviewHighlightResponse>)
  }
}
let worker: WorkerStub, client: PreviewHighlighter
beforeEach(() => { worker = new WorkerStub(); client = new PreviewHighlighter(() => worker) })
afterEach(() => { client.dispose(); vi.useRealTimers() })

it('deduplicates active blocks and reuses their dual-theme result across messages', async () => {
  const a = client.highlight('const answer = 42', 'typescript')
  const b = client.highlight('const answer = 42', 'typescript')
  expect(worker.postMessage).toHaveBeenCalledTimes(1)
  worker.reply()
  expect(await a).toBe(await b)
  expect(await client.highlight('const answer = 42', 'typescript')).toBe('<pre>colored</pre>')
  expect(worker.postMessage).toHaveBeenCalledTimes(1)
  expect(client.cached('const answer = 42', 'typescript')).toBe('<pre>colored</pre>')
})

it('cancels obsolete queued work and keeps a shared active request alive', async () => {
  const owner = new AbortController()
  const first = client.highlight('shared', 'text', owner.signal)
  const rejected = expect(first).rejects.toMatchObject({ name: 'AbortError' })
  const shared = client.highlight('shared')
  const obsolete = new AbortController()
  const queued = client.highlight('obsolete', 'text', obsolete.signal)
  const queuedRejected = expect(queued).rejects.toMatchObject({ name: 'AbortError' })
  owner.abort(); obsolete.abort()
  const latest = client.highlight('latest')
  worker.reply()
  expect(worker.postMessage.mock.calls[1]![0].source).toBe('latest')
  worker.reply(0, 'stale')
  worker.reply(1, 'newest')
  await rejected; await queuedRejected
  expect(await shared).toBe('<pre>colored</pre>')
  expect(await latest).toBe('newest')
})

it('bounds pending blocks, source bytes and cached result size', async () => {
  const results = Array.from({ length: 40 }, (_, index) => client.highlight(`block-${index}`))
  expect(worker.postMessage).toHaveBeenCalledTimes(1)
  expect(await results[1]).toBe(plainCode('block-1'))
  for (let index = 0; index < 33; index++) worker.reply()
  await Promise.all(results)
  const large = 'x'.repeat(500_000)
  const bigJobs = Array.from({ length: 12 }, (_, index) => client.highlight(index + large))
  expect(await bigJobs[1]).toBe(plainCode('1' + large))
  client.dispose(); await Promise.all(bigJobs)
  expect(client.cached('block-39')).toBeUndefined()
})

it('falls back to escaped copyable text on worker failure, timeout and oversize input', async () => {
  vi.useFakeTimers()
  const source = '<img onerror="unsafe">&value'
  const pending = client.highlight(source)
  await vi.advanceTimersByTimeAsync(15_000)
  expect(await pending).toBe(plainCode(source))
  expect(await pending).not.toContain('<img')
  expect(worker.terminate).toHaveBeenCalledTimes(1)
  expect(await client.highlight(source)).toBe(plainCode(source))
  await vi.advanceTimersByTimeAsync(5_000)
  const retry = client.highlight('retry'); worker.reply()
  expect(await retry).toBe('<pre>colored</pre>')
  expect(await client.highlight('x'.repeat(1_000_001))).toContain('class="line"')
})

it('produces the existing dual-theme HTML, multiline syntax and Unicode in the worker processor', async () => {
  const html = await computePreviewHighlight('const 城市 = "😀"\n/*\nconst hidden = 42\n*/', 'typescript')
  expect(html).toContain('--shiki-light')
  expect(html).toContain('--shiki-dark')
  expect(html).toContain('😀')
  expect(html).toContain('hidden = 42')
  expect(await computePreviewHighlight('x'.repeat(1_000_001), 'typescript')).toBeUndefined()
})
