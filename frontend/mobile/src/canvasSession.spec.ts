import { expect, it } from 'vitest'
import { createNoteSession } from './noteSession'
import type { DraftView, NativeRequest, MobileRequest } from './types'
it('keeps an invalid Canvas draft after a definite validation refusal without inventing an unknown save', async () => {
  const view: DraftView = { draft: { id: 'canvas', file_id: null, path: 'a.canvas', content: '{ invalid', edit_session: 'lease', base_hash: '', generation: 1, saved_generation: 0 }, current: null, current_error: null, pending_save: null, pending_mutation: null }
  const rpc = async (request: MobileRequest) => {
    if (request.kind === 'open_draft') return view
    if (request.kind === 'persist_draft') return { ...view.draft, content: request.content, generation: request.generation }
    if (request.kind === 'prepare_save') throw Error('CANVAS_INVALID')
    throw Error(`Unexpected ${request.kind}`)
  }
  const session = createNoteSession(rpc as NativeRequest)
  await session.openDraft('canvas'); await expect(session.save()).rejects.toThrow('CANVAS_INVALID')
  expect(session.state.content).toBe('{ invalid'); expect(session.state.error).toBe('CANVAS_INVALID')
  expect(session.state.unknownSave).toBe(false); expect(session.state.pendingSave).toBeNull(); session.dispose()
})
