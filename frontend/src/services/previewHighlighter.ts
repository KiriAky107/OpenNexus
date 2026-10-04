import { MAX_PREVIEW_SOURCE, plainCode, type PreviewHighlightRequest, type PreviewHighlightResponse } from './previewHighlightProtocol'

interface WorkerPort {
  onmessage: ((event: MessageEvent<PreviewHighlightResponse>) => void) | null
  onerror: ((event: ErrorEvent) => void) | null
  onmessageerror: ((event: MessageEvent) => void) | null
  postMessage(message: PreviewHighlightRequest): void
  terminate(): void
}
interface Listener { resolve: (html: string) => void; reject: (error: unknown) => void; cleanup: () => void }
interface Job extends PreviewHighlightRequest { key: string; listeners: Set<Listener> }

export class PreviewHighlighter {
  private worker?: WorkerPort
  private active?: Job
  private queue = new Map<string, Job>()
  private characters = 0
  private serial = 0
  private deadline?: ReturnType<typeof setTimeout>
  private idle?: ReturnType<typeof setTimeout>
  private unavailableUntil = 0
  private cache = new Map<string, string>()
  private cacheCost = 0

  constructor(private createWorker: () => WorkerPort) {}

  cached(source: string, language = 'text'): string | undefined {
    if (source.length > MAX_PREVIEW_SOURCE) return
    return this.cache.get(JSON.stringify([language.toLowerCase(), source]))
  }

  highlight(source: string, language = 'text', signal?: AbortSignal): Promise<string> {
    if (signal?.aborted) return Promise.reject(signal.reason)
    if (source.length > MAX_PREVIEW_SOURCE || Date.now() < this.unavailableUntil) return Promise.resolve(plainCode(source))
    const key = JSON.stringify([language.toLowerCase(), source])
    const cached = this.cache.get(key)
    if (cached !== undefined) {
      this.cache.delete(key); this.cache.set(key, cached)
      return Promise.resolve(cached)
    }
    return new Promise((resolve, reject) => {
      let job = this.active?.key === key ? this.active : this.queue.get(key)
      if (!job) {
        job = { id: ++this.serial, source, language, key, listeners: new Set() }
        this.queue.set(key, job); this.characters += source.length
      }
      const entry = job
      const abort = () => {
        entry.listeners.delete(listener); listener.cleanup(); reject(signal?.reason)
        if (!entry.listeners.size && this.queue.delete(key)) this.characters -= entry.source.length
      }
      const listener: Listener = { resolve, reject, cleanup: () => signal?.removeEventListener('abort', abort) }
      entry.listeners.add(listener)
      signal?.addEventListener('abort', abort, { once: true })
      while (this.queue.size > 32 || this.characters > 4_000_000) {
        const oldest = this.queue.values().next().value!
        this.queue.delete(oldest.key); this.characters -= oldest.source.length
        this.complete(oldest)
      }
      this.pump()
    })
  }

  private complete(job: Job, html?: string) {
    if (!job.listeners.size) return
    const result = html ?? plainCode(job.source)
    for (const listener of job.listeners) { listener.cleanup(); listener.resolve(result) }
    job.listeners.clear()
  }

  private pump() {
    if (this.active) return
    clearTimeout(this.idle)
    const job = this.queue.values().next().value
    if (!job) { this.idle = setTimeout(() => this.dispose(), 60_000); return }
    this.queue.delete(job.key); this.characters -= job.source.length
    this.active = job
    try {
      if (!this.worker) {
        this.worker = this.createWorker()
        this.worker.onmessage = event => this.receive(event.data)
        this.worker.onerror = this.worker.onmessageerror = () => this.fail()
      }
      this.deadline = setTimeout(() => this.fail(), 15_000)
      this.worker.postMessage({ id: job.id, source: job.source, language: job.language })
    } catch { this.fail() }
  }

  private receive(response: PreviewHighlightResponse) {
    const job = this.active
    if (!job || response.id !== job.id) return
    clearTimeout(this.deadline); this.active = undefined
    const html = response.html
    if (html !== undefined && job.listeners.size && job.key.length + html.length <= 250_000) {
      const previous = this.cache.get(job.key)
      if (previous !== undefined) { this.cacheCost -= job.key.length + previous.length; this.cache.delete(job.key) }
      while (this.cache.size && (this.cache.size >= 64 || this.cacheCost + job.key.length + html.length > 1_000_000)) {
        const oldest = this.cache.keys().next().value!
        this.cacheCost -= oldest.length + this.cache.get(oldest)!.length; this.cache.delete(oldest)
      }
      this.cache.set(job.key, html); this.cacheCost += job.key.length + html.length
    }
    this.complete(job, html); this.pump()
  }

  private fail() { this.unavailableUntil = Date.now() + 5_000; this.dispose() }

  dispose() {
    clearTimeout(this.deadline); clearTimeout(this.idle)
    this.worker?.terminate(); this.worker = undefined
    const jobs = [...this.queue.values(), ...(this.active ? [this.active] : [])]
    this.queue.clear(); this.characters = 0; this.active = undefined
    this.cache.clear(); this.cacheCost = 0
    for (const job of jobs) this.complete(job)
  }
}

export const previewHighlighter = new PreviewHighlighter(() => new Worker(new URL('./previewHighlight.worker.ts', import.meta.url), { type: 'module' }))
