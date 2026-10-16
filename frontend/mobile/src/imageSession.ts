import { reactive } from 'vue'
import { mobileRequest } from './native'
import { errorCode, type createNoteSession } from './noteSession'
import type { DraftView, ImageIntent, NativeRequest } from './types'

interface Selection { from: number; to: number }
export interface ImageEditor { acceptImage: (before: string, after: string) => boolean }
const rejected = new Set(['MOBILE_IMAGE_TOO_LARGE', 'WORKSPACE_IMAGE_UNSUPPORTED', 'MOBILE_SELECTION_INVALID', 'DRAFT_SESSION_CHANGED', 'DRAFT_GENERATION_CONFLICT', 'MOBILE_DRAFT_INVALID', 'PENDING_SAVE_REQUIRES_REVIEW', 'PENDING_MUTATION_REQUIRES_REVIEW'])

export function createImageSession(notes: ReturnType<typeof createNoteSession>, editor: () => ImageEditor | undefined, rpc: NativeRequest = mobileRequest) {
  const state = reactive({ busy: false, pending: null as ImageIntent | null, unknown: false, error: '' })
  const key = () => { const d = notes.state.view?.draft; return d ? `${d.id}:${d.edit_session}` : '' }
  let epoch = 0
  function bind() { epoch++; state.pending = notes.state.view?.pending_image ?? null; state.unknown = false; state.error = ''; state.busy = false }
  async function accept(intent: ImageIntent) {
    state.pending = intent
    if (intent.state === 'committed') {
      await notes.acceptImage(intent, () => editor()?.acceptImage(intent.content_before, intent.content_after) ?? false)
      state.pending = null; state.unknown = false; state.error = ''
    } else if (intent.state === 'cancelled') {
      await notes.checkResult(); state.pending = null; state.unknown = false; state.error = ''
    }
  }
  async function perform(action: (active: () => boolean) => Promise<void>) {
    if (state.busy) return
    const owner = epoch, document = key(), active = () => owner === epoch && document === key()
    state.busy = true; state.error = ''
    try { await action(active) }
    catch (error) { if (active()) { state.error = errorCode(error); state.unknown = !rejected.has(state.error) } }
    finally { if (active()) state.busy = false }
  }
  async function importFile(file: File, selection: Selection, selectedKey: string, generation: number) {
    if (selectedKey !== key() || generation !== notes.state.generation) { state.error = 'MOBILE_EDITOR_CONTENT_CHANGED'; return }
    if (state.pending || state.unknown) { state.error = 'PENDING_IMAGE_REQUIRES_REVIEW'; return }
    return perform(async active => {
      if (!file.size || file.size > 5 * 1024 * 1024) throw Error('MOBILE_IMAGE_TOO_LARGE')
      if (notes.state.composing) throw Error('INPUT_COMPOSITION_ACTIVE')
      await notes.flush()
      const bytes = new Uint8Array(await file.arrayBuffer())
      let binary = ''
      for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192))
      if (!active() || generation !== notes.state.generation) return
      const draft = notes.state.view!.draft
      const intent = await rpc<ImageIntent>({ kind: 'prepare_image', draft_id: draft.id, edit_session: draft.edit_session, generation, ...selection, content_base64: btoa(binary) })
      if (!active()) return
      state.pending = intent
      const applied = intent.state === 'committed' ? intent : await rpc<ImageIntent>({ kind: 'apply_image', operation_id: intent.operation_id })
      if (active()) await accept(applied)
    })
  }
  async function resolve(action: 'check' | 'retry' | 'cancel') {
    return perform(async active => {
      let intent: ImageIntent | null = state.pending
      if (!intent) {
        const draft = notes.state.view?.draft
        if (!draft) return
        const result = await rpc<DraftView>({ kind: 'draft_status', draft_id: draft.id })
        if (!active()) return
        intent = result.pending_image ?? null
        state.pending = intent
        if (!intent) { state.unknown = false; return }
      }
      let observed = await rpc<ImageIntent>({ kind: 'observe_image', operation_id: intent.operation_id })
      if (!active()) return
      state.pending = observed; state.unknown = false
      if (observed.state === 'prepared' && action !== 'check') {
        observed = await rpc<ImageIntent>({ kind: action === 'retry' ? 'apply_image' : 'cancel_image', operation_id: observed.operation_id })
      }
      if (active()) await accept(observed)
    })
  }
  return { state, bind, importFile, check: () => resolve('check'), retry: () => resolve('retry'), cancel: () => resolve('cancel'), dispose: () => { epoch++ } }
}
