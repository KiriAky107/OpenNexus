import { reactive } from 'vue'
import { mobileRequest } from './native'
import type { Draft, DraftView, ImageIntent, NativeRequest, SaveIntent } from './types'

export function errorCode(error: unknown): string {
  return error instanceof Error ? error.message : typeof error === 'string' ? error : 'MOBILE_OPERATION_FAILED'
}

interface Capture { epoch: number; id: string; session: string; generation: number; content: string }
const knownRejection = new Set(['REVISION_CONFLICT', 'INVALID_OPERATION', 'UNSAFE_PATH', 'FILE_IDENTITY_CHANGED', 'SAVE_REQUIRES_REVIEW', 'DRAFT_SESSION_CHANGED', 'DRAFT_GENERATION_CONFLICT', 'PENDING_SAVE_REQUIRES_REVIEW', 'CANVAS_INVALID', 'CANVAS_TOO_LARGE', 'CANVAS_TOO_COMPLEX'])

export function createNoteSession(rpc: NativeRequest = mobileRequest) {
  const state = reactive({
    view: null as DraftView | null, content: '', generation: 0, persistedGeneration: 0, savedGeneration: 0,
    loading: false, saving: false, persisting: false, composing: false, error: '',
    pendingSave: null as SaveIntent | null, unknownSave: false,
  })
  let epoch = 0, navigation = 0, disposed = false
  let feedback = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let tail: Promise<unknown> = Promise.resolve()
  function enqueue<T>(action: () => Promise<T>): Promise<T> {
    const result = tail.then(action)
    tail = result.catch(() => undefined)
    return result
  }
  function capture(): Capture | null {
    const draft = state.view?.draft
    return draft ? { epoch, id: draft.id, session: draft.edit_session, generation: state.generation, content: state.content } : null
  }
  function current(c: Capture): boolean {
    const view = state.view
    return !disposed && Boolean(view && c.epoch === epoch && c.id === view.draft.id && c.session === view.draft.edit_session)
  }
  function clearError() { state.error = ''; feedback++ }
  function reportError(code: string) { state.error = code; feedback++ }
  function fail(c: Capture, error: unknown) { if (current(c)) reportError(errorCode(error)) }
  function stopTimer() { if (timer) { clearTimeout(timer); timer = undefined } }
  function applyView(view: DraftView) {
    epoch++
    state.view = view; state.content = view.draft.content; state.generation = view.draft.generation
    state.persistedGeneration = view.draft.generation; state.savedGeneration = view.draft.saved_generation
    state.pendingSave = view.pending_save; state.unknownSave = Boolean(view.pending_save)
    clearError(); state.composing = false
  }
  async function persist(c: Capture): Promise<Draft> {
    const owner = feedback
    if (current(c)) state.persisting = true
    try {
      const result = await rpc<Draft>({ kind: 'persist_draft', draft_id: c.id, edit_session: c.session, generation: c.generation, content: c.content })
      if (current(c)) {
        state.persistedGeneration = Math.max(state.persistedGeneration, result.generation)
        if (state.error && feedback === owner) clearError()
      }
      return result
    } catch (error) { fail(c, error); throw error }
    finally { if (current(c)) state.persisting = false }
  }
  function schedule() {
    stopTimer()
    if (state.composing) return
    timer = setTimeout(() => { timer = undefined; void flush().catch(() => undefined) }, 300)
  }
  function edit(content: string) {
    if (!state.view || disposed || content === state.content) return
    state.content = content; state.generation++; clearError(); schedule()
  }
  async function flush() {
    stopTimer()
    if (state.composing) throw new Error('INPUT_COMPOSITION_ACTIVE')
    const c = capture()
    if (!c) return
    await enqueue(async () => {
      if (current(c) && c.generation <= state.persistedGeneration) return
      await persist(c)
    })
  }
  async function open(action: () => Promise<DraftView>) {
    const request = ++navigation
    state.loading = true
    try {
      await flush()
      const view = await enqueue(action)
      if (!disposed && request === navigation) applyView(view)
    } catch (error) {
      if (!disposed && request === navigation) reportError(errorCode(error))
      throw error
    } finally { if (!disposed && request === navigation) state.loading = false }
  }
  async function refresh(c: Capture) {
    const result = await rpc<DraftView>({ kind: 'draft_status', draft_id: c.id })
    if (!current(c)) return
    if (result.draft.edit_session !== c.session) throw new Error('DRAFT_SESSION_CHANGED')
    // Never copy server text over newer in-memory input.
    state.view = result
    state.persistedGeneration = Math.max(state.persistedGeneration, result.draft.generation)
    state.savedGeneration = result.draft.saved_generation
    state.pendingSave = result.pending_save; state.unknownSave = Boolean(result.pending_save)
  }
  async function save() {
    if (state.composing) throw new Error('INPUT_COMPOSITION_ACTIVE')
    const c = capture()
    if (!c || state.saving) return
    if (state.pendingSave) throw new Error('PENDING_SAVE_REQUIRES_REVIEW')
    if (state.generation === state.savedGeneration && state.view?.draft.file_id) return
    stopTimer(); state.saving = true; clearError()
    return enqueue(async () => {
      let phase: 'persist' | 'prepare' | 'apply' | 'refresh' = 'persist'
      try {
        await persist(c)
        phase = 'prepare'
        let intent = await rpc<SaveIntent>({ kind: 'prepare_save', draft_id: c.id, edit_session: c.session, generation: c.generation })
        if (current(c)) state.pendingSave = intent
        phase = 'apply'
        if (intent.state !== 'committed') intent = await rpc<SaveIntent>({ kind: 'apply_save', operation_id: intent.operation_id })
        if (intent.state !== 'committed') throw new Error('MOBILE_SAVE_NOT_CONFIRMED')
        if (current(c)) { state.savedGeneration = intent.generation; state.pendingSave = null; state.unknownSave = false }
        phase = 'refresh'
        await refresh(c)
      } catch (error) {
        if (current(c)) {
          reportError(errorCode(error))
          if (phase === 'apply' || phase === 'prepare') state.unknownSave = !knownRejection.has(state.error)
        }
        if (phase === 'apply' && knownRejection.has(errorCode(error))) await refresh(c).catch(() => undefined)
        throw error
      } finally { if (current(c)) state.saving = false }
    })
  }
  async function checkResult() {
    const c = capture(); if (!c) return
    return enqueue(async () => {
      const owner = feedback
      try { await refresh(c); if (current(c) && feedback === owner) clearError() }
      catch (error) { fail(c, error); throw error }
    })
  }
  async function acceptImage(intent: ImageIntent, updateEditor: () => boolean) {
    const c = capture()
    if (!c || c.id !== intent.draft_id || intent.state !== 'committed') throw new Error('MOBILE_EDITOR_CONTENT_CHANGED')
    if (state.content !== intent.content_after || state.generation !== intent.generation + 1) {
      if (state.content !== intent.content_before || state.generation !== intent.generation || !updateEditor()) throw new Error('MOBILE_EDITOR_CONTENT_CHANGED')
    }
    stopTimer()
    await enqueue(() => refresh(c))
  }
  async function original(action: 'apply_save' | 'cancel_save') {
    const c = capture(), id = state.pendingSave?.operation_id
    if (!c || !id || state.saving) return
    state.saving = true; clearError()
    return enqueue(async () => {
      try {
        // Always observe the original before an explicit original-content retry/cancel.
        let result = await rpc<SaveIntent>({ kind: 'observe_save', operation_id: id })
        if (result.state !== 'committed') result = await rpc<SaveIntent>({ kind: action, operation_id: id })
        if (current(c)) {
          state.pendingSave = result.state === 'prepared' ? result : null
          state.unknownSave = result.state === 'prepared'
          if (result.state === 'committed') state.savedGeneration = result.generation
        }
        await refresh(c)
      } catch (error) {
        fail(c, error)
        if (current(c)) state.unknownSave = !knownRejection.has(errorCode(error))
        if (knownRejection.has(errorCode(error))) await refresh(c).catch(() => undefined)
        throw error
      }
      finally { if (current(c)) state.saving = false }
    })
  }
  async function close() {
    await flush()
    navigation++; epoch++; state.view = null; state.content = ''; clearError()
  }
  async function closeAfterDiscard(id: string) {
    stopTimer(); await tail
    if (state.view?.draft.id !== id) return
    navigation++; epoch++; state.view = null; state.content = ''; clearError()
  }
  async function copy(path: string) {
    const c = capture(); if (!c) return
    if (state.composing) throw new Error('INPUT_COMPOSITION_ACTIVE')
    stopTimer()
    // The user explicitly asked for a new copy. Preserve RAM text even when the old lease is invalid.
    const request = ++navigation
    state.loading = true
    try {
      await flush().catch(error => {
        if (!['DRAFT_SESSION_CHANGED', 'DRAFT_NOT_FOUND'].includes(errorCode(error))) throw error
      })
      const view = await enqueue(() => rpc<DraftView>({ kind: 'create_draft', path, content: c.content }))
      if (current(c) && request === navigation) applyView(view)
    } catch (error) { fail(c, error); throw error }
    finally { if (!disposed && request === navigation) state.loading = false }
  }
  function dispose() { disposed = true; navigation++; epoch++; stopTimer() }
  return {
    state, edit, flush, save, checkResult, acceptImage, close, closeAfterDiscard, copy, dispose, clearError, reportError,
    openNote: (file_id: string) => open(() => rpc<DraftView>({ kind: 'open_note', file_id })),
    openDraft: (draft_id: string) => open(() => rpc<DraftView>({ kind: 'open_draft', draft_id })),
    create: (path: string) => open(() => rpc<DraftView>({ kind: 'create_draft', path })),
    retryOriginal: () => original('apply_save'), cancelOriginal: () => original('cancel_save'),
    compositionStart: () => { state.composing = true; stopTimer() },
    compositionEnd: () => { state.composing = false; schedule() },
  }
}
