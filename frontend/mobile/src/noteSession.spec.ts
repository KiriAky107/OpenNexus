import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createNoteSession } from './noteSession'
import type { Draft, DraftView, MobileRequest, NativeRequest, SaveIntent } from './types'

function view(id = 'a', content = 'base'): DraftView {
  return { draft: { id, file_id: null, path: `${id}.md`, base_hash: '', generation: 1, saved_generation: 0, content, edit_session: `lease-${id}` },
    current: null, current_error: null, pending_save: null, pending_mutation: null }
}
function intent(draft: Draft, content: string, generation = 2): SaveIntent {
  return { operation_id: 'original-save-id', draft_id: draft.id, generation, file_id: draft.file_id,
    path: draft.path, expected: draft.base_hash, content, state: 'prepared', error: null }
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
async function tick() { for (let i = 0; i < 20; i++) await Promise.resolve() }

describe('mobile editor persistence and request ownership', () => {
  const sessions: ReturnType<typeof createNoteSession>[] = []
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => { sessions.splice(0).forEach(s => s.dispose()); vi.useRealTimers() })
  function session(fn: (r: MobileRequest) => Promise<unknown>) {
    const rpc = vi.fn(fn), s = createNoteSession(rpc as NativeRequest)
    sessions.push(s); return { s, rpc }
  }

  it('clears a recovered draft error but cannot clear newer feedback with an older acknowledgement', async () => {
    const a = view(), acknowledgement = deferred<Draft>(); let failing = true, delayed = false
    const { s } = session(async request => {
      if (request.kind === 'open_draft') return a
      if (request.kind === 'persist_draft') {
        if (failing) throw Error('QUOTA_EXCEEDED')
        if (delayed) return acknowledgement.promise
        return { ...a.draft, generation: request.generation, content: request.content }
      }
      throw Error(`Unexpected ${request.kind}`)
    })
    await s.openDraft('a'); s.edit('kept after retry')
    await expect(s.flush()).rejects.toThrow('QUOTA_EXCEEDED'); expect(s.state.error).toBe('QUOTA_EXCEEDED')
    failing = false; await s.flush(); expect(s.state.error).toBe('')
    delayed = true; s.edit('newer input'); const pending = s.flush(); await tick()
    s.reportError('newer feedback')
    acknowledgement.resolve({ ...a.draft, generation: 3, content: 'newer input' }); await pending
    expect(s.state.error).toBe('newer feedback'); expect(s.state.persistedGeneration).toBe(3)
  })

  it('does not reflush a draft after the native discard was already acknowledged', async () => {
    const { s, rpc } = session(async request => { if (request.kind === 'open_draft') return view(); throw Error(`Unexpected ${request.kind}`) })
    await s.openDraft('a'); s.edit('explicitly discarded input')
    await s.closeAfterDiscard('a'); await vi.advanceTimersByTimeAsync(400)
    expect(s.state.view).toBeNull(); expect(rpc.mock.calls).toHaveLength(1)
  })

  it('saves exactly the clicked text while retaining later typing as an uncommitted draft', async () => {
    const initial = view(), prepared = deferred<SaveIntent>(), frozen = intent(initial.draft, 'first')
    const { s, rpc } = session(async request => {
      if (request.kind === 'open_draft') return initial
      if (request.kind === 'persist_draft') return { ...initial.draft, generation: request.generation, content: request.content }
      if (request.kind === 'prepare_save') return prepared.promise
      if (request.kind === 'apply_save') return { ...frozen, state: 'committed' }
      if (request.kind === 'draft_status') return { ...initial, draft: { ...initial.draft, content: 'first', generation: 2, saved_generation: 2, file_id: 'native-file' } }
      throw Error(`Unexpected ${request.kind}`)
    })
    await s.openDraft('a'); s.edit('first')
    const saving = s.save(); await tick()
    s.edit('later typing'); prepared.resolve(frozen); await saving
    expect(s.state.content).toBe('later typing')
    expect(s.state.generation).toBe(3); expect(s.state.savedGeneration).toBe(2)
    expect(rpc.mock.calls.filter(([r]) => r.kind === 'persist_draft').map(([r]) => r)).toEqual([
      { kind: 'persist_draft', draft_id: 'a', edit_session: 'lease-a', generation: 2, content: 'first' },
    ])
    await s.flush(); expect(s.state.persistedGeneration).toBe(3); expect(s.state.savedGeneration).toBe(2)
  })

  it('checks an unknown original read-only and retries the same frozen operation explicitly', async () => {
    const initial = view(), frozen = intent(initial.draft, 'changed')
    let failed = false, committed = false
    const { s, rpc } = session(async request => {
      if (request.kind === 'open_draft') return initial
      if (request.kind === 'persist_draft') return { ...initial.draft, generation: request.generation, content: request.content }
      if (request.kind === 'prepare_save') return frozen
      if (request.kind === 'observe_save') return frozen
      if (request.kind === 'apply_save') {
        if (!failed) { failed = true; throw Error('MOBILE_HOST_INTERRUPTED') }
        committed = true; return { ...frozen, state: 'committed' }
      }
      if (request.kind === 'draft_status') return { ...initial,
        draft: { ...initial.draft, content: 'changed', generation: 2, saved_generation: committed ? 2 : 0 }, pending_save: committed ? null : frozen }
      throw Error(`Unexpected ${request.kind}`)
    })
    await s.openDraft('a'); s.edit('changed')
    await expect(s.save()).rejects.toThrow('MOBILE_HOST_INTERRUPTED')
    expect(s.state.unknownSave).toBe(true)
    await s.checkResult()
    expect(rpc.mock.calls.filter(([r]) => r.kind === 'apply_save')).toHaveLength(1)
    await s.retryOriginal()
    expect(rpc.mock.calls.filter(([r]) => r.kind === 'prepare_save')).toHaveLength(1)
    expect(rpc.mock.calls.filter(([r]) => r.kind === 'apply_save').map(([r]) => r)).toEqual([
      { kind: 'apply_save', operation_id: 'original-save-id' }, { kind: 'apply_save', operation_id: 'original-save-id' },
    ])
    expect(s.state.unknownSave).toBe(false); expect(s.state.savedGeneration).toBe(2)
  })

  it('serializes opening requests and ignores an earlier failed navigation', async () => {
    const first = deferred<DraftView>()
    const { s, rpc } = session(async request => {
      if (request.kind === 'open_note' && request.file_id === 'first') return first.promise
      if (request.kind === 'open_note' && request.file_id === 'latest') return view('latest', 'current note')
      throw Error(`Unexpected ${request.kind}`)
    })
    const old = s.openNote('first').catch(() => undefined); await tick()
    const latest = s.openNote('latest'); await tick()
    expect(rpc.mock.calls).toHaveLength(1)
    first.reject(Error('old navigation error')); await old; await latest
    expect(s.state.view?.draft.id).toBe('latest'); expect(s.state.content).toBe('current note'); expect(s.state.error).toBe('')
  })

  it('waits for the previous document draft acknowledgement before leaving it', async () => {
    const saved = deferred<Draft>(), a = view('a', 'a original')
    const { s, rpc } = session(async request => {
      if (request.kind === 'open_draft') return a
      if (request.kind === 'persist_draft') return saved.promise
      if (request.kind === 'open_note') return view('b', 'b original')
      throw Error(`Unexpected ${request.kind}`)
    })
    await s.openDraft('a'); s.edit('a changed')
    const navigation = s.openNote('b'); await tick()
    expect(rpc.mock.calls.some(([r]) => r.kind === 'open_note')).toBe(false)
    saved.resolve({ ...a.draft, generation: 2, content: 'a changed' }); await navigation
    expect(s.state.content).toBe('b original'); expect(s.state.error).toBe('')
  })

  it('does not persist partial IME composition and persists the final composed text', async () => {
    const a = view()
    const { s, rpc } = session(async request => {
      if (request.kind === 'open_draft') return a
      if (request.kind === 'persist_draft') return { ...a.draft, generation: request.generation, content: request.content }
      throw Error(`Unexpected ${request.kind}`)
    })
    await s.openDraft('a'); s.compositionStart(); s.edit('zhong'); await vi.advanceTimersByTimeAsync(400)
    expect(rpc.mock.calls).toHaveLength(1)
    await expect(s.save()).rejects.toThrow('INPUT_COMPOSITION_ACTIVE')
    s.edit('中文'); s.compositionEnd(); await vi.advanceTimersByTimeAsync(300)
    expect(rpc.mock.calls.at(-1)?.[0]).toMatchObject({ kind: 'persist_draft', content: '中文', generation: 3 })
    expect(s.state.persistedGeneration).toBe(3)
  })

  it('retains RAM text after storage failure and a later successful save is not poisoned', async () => {
    const a = view(), frozen = intent(a.draft, 'keep me'); let failing = true
    const { s, rpc } = session(async request => {
      if (request.kind === 'open_draft') return a
      if (request.kind === 'persist_draft') { if (failing) throw Error('QUOTA_EXCEEDED'); return { ...a.draft, generation: 2, content: 'keep me' } }
      if (request.kind === 'prepare_save') return frozen
      if (request.kind === 'apply_save') return { ...frozen, state: 'committed' }
      if (request.kind === 'draft_status') return { ...a, draft: { ...a.draft, generation: 2, content: 'keep me', saved_generation: 2 } }
      throw Error(`Unexpected ${request.kind}`)
    })
    await s.openDraft('a'); s.edit('keep me')
    await expect(s.save()).rejects.toThrow('QUOTA_EXCEEDED')
    expect(s.state.content).toBe('keep me'); expect(s.state.savedGeneration).toBe(0)
    expect(rpc.mock.calls.some(([r]) => r.kind === 'prepare_save')).toBe(false)
    failing = false; await s.save(); expect(s.state.savedGeneration).toBe(2)
  })

  it('preserves current RAM in a new draft when its previous editor lease is no longer valid', async () => {
    const a = view()
    const { s, rpc } = session(async request => {
      if (request.kind === 'open_draft') return a
      if (request.kind === 'persist_draft') throw Error('DRAFT_SESSION_CHANGED')
      if (request.kind === 'create_draft') return view('copy', request.content)
      throw Error(`Unexpected ${request.kind}`)
    })
    await s.openDraft('a'); s.edit('unacknowledged 中文')
    await s.copy('copy.md')
    expect(rpc.mock.calls.at(-1)?.[0]).toEqual({ kind: 'create_draft', path: 'copy.md', content: 'unacknowledged 中文' })
    expect(s.state.view?.draft.id).toBe('copy'); expect(s.state.content).toBe('unacknowledged 中文'); expect(s.state.savedGeneration).toBe(0)
  })
})
