import { MAX_HIGHLIGHT_CHARACTERS, type HighlightInput, type HighlightResponse, type HighlightResult } from './codeHighlightProtocol'

interface WorkerPort {
  onmessage: ((event: MessageEvent<HighlightResponse>) => void) | null
  onerror: ((event: ErrorEvent) => void) | null
  onmessageerror: ((event: MessageEvent) => void) | null
  postMessage(value: HighlightInput & { id: number }): void
  terminate(): void
}
interface Job { id: number; owner: object; input: HighlightInput; done?: (result?: HighlightResult) => void }
const MAX_QUEUED_JOBS = 32, MAX_QUEUED_CHARACTERS = 4_000_000, CACHE_BUDGET = 1_000_000

export class HighlightClient {
  private worker?: WorkerPort
  private active?: Job
  private queue = new Map<object, Job>()
  private queuedCharacters = 0
  private nextId = 0
  private deadline?: ReturnType<typeof setTimeout>
  private idle?: ReturnType<typeof setTimeout>
  private unavailableUntil = 0
  private cache = new Map<string, HighlightResult>()
  private cacheCost = 0

  constructor(private createWorker: () => WorkerPort) {}

  request(owner: object, input: HighlightInput, done: (result?: HighlightResult) => void) {
    this.cancel(owner)
    if (input.source.length > MAX_HIGHLIGHT_CHARACTERS || Date.now() < this.unavailableUntil) { done(); return }
    const key = this.cacheKey(input), cached = key ? this.cache.get(key) : undefined
    if (cached && key) { this.cache.delete(key); this.cache.set(key, cached); done(cached); return }
    this.queue.set(owner, { id: ++this.nextId, owner, input, done })
    this.queuedCharacters += input.source.length
    while (this.queue.size > MAX_QUEUED_JOBS || this.queuedCharacters > MAX_QUEUED_CHARACTERS) {
      const oldest = this.queue.keys().next().value!
      const dropped = this.queue.get(oldest)!
      this.queue.delete(oldest); this.queuedCharacters -= dropped.input.source.length
      dropped.done?.()
    }
    this.pump()
  }

  cancel(owner: object) {
    const queued = this.queue.get(owner)
    if (queued) { this.queue.delete(owner); this.queuedCharacters -= queued.input.source.length }
    if (this.active?.owner === owner) this.active.done = undefined
  }

  private cacheKey(input: HighlightInput) {
    return input.source.length <= 16_000 ? JSON.stringify([input.language, input.theme, input.source]) : undefined
  }

  private retain(input: HighlightInput, result: HighlightResult) {
    const key = this.cacheKey(input)
    if (!key) return
    const cost = this.cost(key, result)
    if (cost > CACHE_BUDGET / 4) return
    const previous = this.cache.get(key)
    if (previous) { this.cacheCost -= this.cost(key, previous); this.cache.delete(key) }
    while (this.cache.size && (this.cache.size >= 32 || this.cacheCost + cost > CACHE_BUDGET)) {
      const oldest = this.cache.keys().next().value!
      this.cacheCost -= this.cost(oldest, this.cache.get(oldest)!); this.cache.delete(oldest)
    }
    this.cache.set(key, result); this.cacheCost += cost
  }

  private cost(key: string, result: HighlightResult) {
    return key.length * 2 + result.spans.byteLength + result.styles.reduce((sum, style) => sum + style.length * 2, 0)
  }

  private pump() {
    if (this.active) return
    clearTimeout(this.idle)
    const job = this.queue.values().next().value
    if (!job) { this.idle = setTimeout(() => this.dispose(), 60_000); return }
    this.queue.delete(job.owner); this.queuedCharacters -= job.input.source.length
    this.active = job
    try {
      if (!this.worker) {
        this.worker = this.createWorker()
        this.worker.onmessage = event => this.receive(event.data)
        this.worker.onerror = this.worker.onmessageerror = () => this.failed()
      }
      this.deadline = setTimeout(() => this.failed(), 15_000)
      this.worker.postMessage({ ...job.input, id: job.id })
    } catch { this.failed() }
  }

  private receive(response: HighlightResponse) {
    const job = this.active
    if (!job || response.id !== job.id) return
    clearTimeout(this.deadline)
    this.active = undefined
    if (response.result && job.done) this.retain(job.input, response.result)
    job.done?.(response.result)
    this.pump()
  }

  private failed() {
    const jobs = [...this.queue.values(), ...(this.active ? [this.active] : [])]
    this.unavailableUntil = Date.now() + 5_000
    this.dispose()
    for (const job of jobs) job.done?.()
  }

  dispose() {
    clearTimeout(this.deadline); clearTimeout(this.idle)
    this.worker?.terminate(); this.worker = undefined; this.active = undefined
    this.queue.clear(); this.queuedCharacters = 0
    this.cache.clear(); this.cacheCost = 0
  }
}

export const codeHighlights = new HighlightClient(() => new Worker(new URL('./codeHighlight.worker.ts', import.meta.url), { type: 'module' }))
