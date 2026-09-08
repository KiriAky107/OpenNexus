/** A durable preference draft keeps its original CAS base until the user resolves a conflict. */
export interface LogicalRecord<T> { schema: 1; kind: string; id: string; data: T }
export interface RecordDocument<T> { record: LogicalRecord<T>; hash: string; file_id: string }
interface Draft<T> { record: LogicalRecord<T>; expected: string; operation_id: string }
interface Options<T> {
  vaultId: string; kind: string; id: string; read(): T; apply(data: T): void | Promise<void>
  invoke<R>(command: string, args: Record<string, unknown>): Promise<R>
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>; changed?(): void
}
export class RecordBinding<T> {
  private draft: Draft<T> | null = null
  private remote: RecordDocument<T> | null = null
  private running = false
  private active: Promise<void> | null = null
  private restored = false
  private invalidDraft = false
  private stopped = false
  private initialized = false
  private appliedHash: string | null = null
  error = ''
  constructor(private options: Options<T>) {
    try {
      const value = JSON.parse(options.storage.getItem(this.key) ?? 'null') as Draft<T> | null
      if (value && (value.record?.schema !== 1 || value.record.kind !== options.kind || value.record.id !== options.id || !value.record.data || typeof value.record.data !== 'object' || typeof value.expected !== 'string' || !/^(?:[0-9a-f]{64})?$/.test(value.expected) || typeof value.operation_id !== 'string' || !/^[0-9a-f-]{36}$/.test(value.operation_id))) throw new Error('invalid draft')
      this.draft = value; this.restored = value !== null
    } catch { this.error = 'PREFERENCE_DRAFT_INVALID'; this.invalidDraft = true }
  }
  private get key() { return `opennexus-record-draft:${this.options.vaultId}:${this.options.kind}:${this.options.id}` }
  private persist(draft = this.draft) {
    try {
      if (draft) this.options.storage.setItem(this.key, JSON.stringify(draft))
      else this.options.storage.removeItem(this.key)
    } catch { this.error = 'PREFERENCE_DRAFT_STORE_FAILED'; this.options.changed?.(); throw new Error(this.error) }
    this.options.changed?.()
  }
  get conflicted() { return this.error === 'REVISION_CONFLICT' }
  get hasDraft() { return this.draft !== null || this.invalidDraft }
  stop() { this.stopped = true }
  capture() {
    if (this.stopped) return
    const data = JSON.parse(JSON.stringify(this.options.read())) as T
    if (!this.draft && this.remote && JSON.stringify(data) === JSON.stringify(this.remote.record.data)) return
    // New edits while a request runs get a new operation, but preserve the unresolved base.
    this.draft = { record: { schema: 1, kind: this.options.kind, id: this.options.id, data }, expected: this.draft?.expected ?? this.remote?.hash ?? '', operation_id: crypto.randomUUID() }
    this.restored = false; this.invalidDraft = false
    try { this.persist(); if (this.error === 'PREFERENCE_DRAFT_STORE_FAILED') this.error = '' } catch { this.error = 'PREFERENCE_DRAFT_STORE_FAILED'; this.options.changed?.() }
  }
  async seed() {
    await this.poll()
    if (!this.stopped && this.initialized && !this.remote && !this.draft) { this.capture(); await this.poll() }
  }
  poll(): Promise<void> {
    if (this.stopped) return Promise.resolve()
    if (this.active) return this.active
    this.active = this.run().finally(() => { this.active = null })
    return this.active
  }
  private async run() {
    if (this.running || this.stopped || this.invalidDraft || this.error === 'PREFERENCE_DRAFT_STORE_FAILED') return
    this.running = true
    try {
      this.remote = await this.options.invoke<RecordDocument<T> | null>('record_get', { request: { vault_id: this.options.vaultId, kind: this.options.kind, id: this.options.id } })
      if (this.stopped) return
      this.initialized = true
      if (this.draft) {
        if (this.restored) { this.restored = false; await this.options.apply(this.draft.record.data) }
        if (this.stopped || this.conflicted) return
        const draft = this.draft
        const committed = await this.options.invoke<RecordDocument<T>>('record_write', { request: { vault_id: this.options.vaultId, ...draft } })
        this.remote = committed
        if (this.draft.operation_id === draft.operation_id) {
          this.persist(null); this.draft = null; this.appliedHash = committed.hash
        } else {
          // The next local edit follows the just-confirmed predecessor, not its older CAS base.
          this.draft.expected = committed.hash; this.persist()
        }
      } else if (this.remote && this.remote.hash !== this.appliedHash) {
        await this.options.apply(this.remote.record.data)
        this.appliedHash = this.remote.hash
      }
      this.error = ''
    } catch (error) { this.error = error instanceof Error ? error.message : 'PREFERENCE_SYNC_FAILED' }
    finally { this.running = false; this.options.changed?.() }
  }
  async keepLocal() {
    await this.active
    if (this.running || this.stopped) return
    this.running = true
    try {
      if (this.invalidDraft) this.capture()
      if (!this.draft) return
      const remote = await this.options.invoke<RecordDocument<T> | null>('record_get', { request: { vault_id: this.options.vaultId, kind: this.options.kind, id: this.options.id } })
      if (this.stopped) return
      this.remote = remote; this.draft.expected = remote?.hash ?? ''; this.draft.operation_id = crypto.randomUUID(); this.error = ''; this.persist()
    } finally { this.running = false }
    await this.poll()
  }
  async useRemote() {
    await this.active
    if (this.running || this.stopped) return
    this.running = true
    try {
      const decision = this.draft?.operation_id
      const remote = await this.options.invoke<RecordDocument<T> | null>('record_get', { request: { vault_id: this.options.vaultId, kind: this.options.kind, id: this.options.id } })
      if (this.stopped) return
      if (this.draft?.operation_id !== decision) { this.error = 'PREFERENCE_CHANGED'; this.options.changed?.(); return }
      if (!remote) { this.error = 'PREFERENCE_REMOTE_MISSING'; this.options.changed?.(); return }
      this.options.storage.removeItem(this.key)
      this.draft = null; this.invalidDraft = false; this.error = ''; this.remote = remote; this.options.changed?.()
      await this.options.apply(remote.record.data); this.appliedHash = remote.hash
    } finally { this.running = false }
  }
}
