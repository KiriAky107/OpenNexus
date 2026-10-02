export interface PreviewValue { text?: string; url?: string; limited?: boolean }
export interface PreviewPayload { text?: string; blob?: Blob }
export interface PreviewSource { vault: string | null; path: string; key: string }
interface Entry { source: PreviewSource; value: PreviewValue; bytes: number; limitedBytes?: number }

/** URLs belong to this bounded cache, including previews shared by two nodes. */
export class CanvasPreviewCache {
  private entries = new Map<string, Entry>()
  private pending = new Map<string, Promise<PreviewValue | undefined>>()
  private wanted = new Set<string>()
  private vault: string | null | undefined
  private epoch = 0
  private disposed = false
  bytes = 0
  constructor(readonly maxEntries = 96, readonly maxBytes = 32*1024*1024) {}
  get size() { return this.entries.size }
  private remove(key: string) {
    const entry = this.entries.get(key)
    if (!entry) return
    this.entries.delete(key); this.bytes -= entry.bytes
    if (entry.value.url) URL.revokeObjectURL(entry.value.url)
  }
  configure(vault: string | null, sources: PreviewSource[]) {
    if (vault !== this.vault) { this.clear(); this.vault = vault }
    this.wanted = new Set(sources.map(source=>source.key))
    const revisions = new Map(sources.map(source=>[source.path,source.key]))
    for (const [key,entry] of this.entries) {
      if (revisions.has(entry.source.path) && revisions.get(entry.source.path) !== key) this.remove(key)
    }
  }
  private room(bytes: number) {
    while (this.entries.size >= this.maxEntries || this.bytes+bytes > this.maxBytes) {
      const victim = [...this.entries.keys()].find(key=>!this.wanted.has(key))
      if (!victim) return false
      this.remove(victim)
    }
    return true
  }
  async read(source: PreviewSource, loader: ()=>Promise<PreviewPayload>): Promise<PreviewValue | undefined> {
    if (this.disposed || source.vault !== this.vault || !this.wanted.has(source.key)) return
    const entry = this.entries.get(source.key)
    if (entry?.limitedBytes) {
      this.entries.delete(source.key)
      if (!this.room(entry.limitedBytes)) { this.entries.set(source.key,entry); return entry.value }
    } else if (entry) {
      this.entries.delete(source.key); this.entries.set(source.key,entry)
      return entry.value
    }
    const waiting = this.pending.get(source.key)
    if (waiting) return waiting
    const epoch = this.epoch
    const promise = (async () => {
      try {
        const payload = await Promise.resolve().then(loader)
        if (this.disposed || epoch !== this.epoch || !this.wanted.has(source.key)) return
        const bytes = payload.blob?.size ?? (payload.text?.length || 0)*2
        if (!this.room(bytes)) {
          const value = {limited:true}
          if (this.room(0)) this.entries.set(source.key,{source,value,bytes:0,limitedBytes:bytes})
          return value
        }
        const value = payload.blob ? {url:URL.createObjectURL(payload.blob)} : {text:payload.text}
        this.entries.set(source.key,{source,value,bytes}); this.bytes += bytes
        return value
      } finally { if (epoch === this.epoch) this.pending.delete(source.key) }
    })()
    this.pending.set(source.key,promise)
    return promise
  }
  clear() {
    this.epoch++; this.pending.clear(); this.wanted.clear()
    for (const key of this.entries.keys()) this.remove(key)
  }
  dispose() { this.disposed = true; this.clear() }
}
