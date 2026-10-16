// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest'
import { createImageSession } from './imageSession'
import { createNoteSession } from './noteSession'
import type { Draft, DraftView, ImageIntent, MobileRequest, NativeRequest } from './types'

const cleanups: (() => void)[] = []
afterEach(() => cleanups.splice(0).forEach(fn => fn()))
const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T
function harness(mode: 'normal' | 'lost-prepare' | 'lost-apply' = 'normal', canvas = false) {
  let draft: Draft = { id: 'a', path: canvas ? 'canvas/a.canvas' : 'notes/a.md', file_id: 'file-a', edit_session: 'lease-a', generation: 1, saved_generation: 1, base_hash: 'hash', content: canvas ? '{"nodes":[],"edges":[],"original":"中文 😀"}\r\n' : '😀\r\n原文' }
  let intent: ImageIntent | null = null, drop = true
  const calls: MobileRequest[] = []
  const snapshot = (): DraftView => clone({ draft, current: null, current_error: null, pending_save: null, pending_mutation: null, pending_image: intent?.state === 'prepared' ? intent : null })
  const rpc = (async (request: MobileRequest) => {
    calls.push(clone(request))
    switch (request.kind) {
      case 'open_draft': return snapshot()
      case 'persist_draft': draft = { ...draft, content: request.content, generation: request.generation }; return clone(draft)
      case 'prepare_canvas_image':
      case 'prepare_image':
        intent = { operation_id: 'original-image-id', draft_id: draft.id, path: draft.path, generation: draft.generation,
          asset_path: 'attachments/ab/abcd.png', reference: canvas ? 'attachments/ab/abcd.png' : '../attachments/ab/abcd.png', hash: 'abcd', size: 4,
          content_before: draft.content, content_after: request.kind === 'prepare_canvas_image'
            ? JSON.stringify({ ...JSON.parse(draft.content), nodes: [{ id: 'original-image-node', type: 'file', file: 'attachments/ab/abcd.png', x: request.x, y: request.y, width: 320, height: 240 }] }) + '\n'
            : draft.content.slice(0, request.from) + '![](../attachments/ab/abcd.png)' + draft.content.slice(request.to), state: 'prepared', error: null }
        if (mode === 'lost-prepare' && drop) { drop = false; throw Error('MOBILE_HOST_INTERRUPTED') }
        return clone(intent)
      case 'apply_image':
        intent = { ...intent!, state: 'committed' }; draft = { ...draft, generation: intent.generation + 1, content: intent.content_after }
        if (mode === 'lost-apply' && drop) { drop = false; throw Error('MOBILE_HOST_INTERRUPTED') }
        return clone(intent)
      case 'observe_image': return clone(intent)
      case 'cancel_image': intent = { ...intent!, state: 'cancelled' }; return clone(intent)
      case 'draft_status': return snapshot()
      default: throw Error(`Unexpected ${request.kind}`)
    }
  }) as NativeRequest
  const notes = createNoteSession(rpc)
  let inserted = 0
  const image = createImageSession(notes, () => ({ acceptImage(before, after) {
    if (notes.state.content !== before) return false
    notes.edit(after); inserted++; return true
  } }), rpc)
  cleanups.push(() => { image.dispose(); notes.dispose() })
  const file = new File([new Uint8Array([1, 2, 3, 4])], 'selected.png', { type: 'image/png' })
  return { notes, image, calls, file, key: 'a:lease-a', inserted: () => inserted, draft: () => draft }
}

it('freezes exact selected bytes and UTF-16 range before one insertion, keeping Markdown unsaved', async () => {
  const h = harness(); await h.notes.openDraft('a'); h.image.bind()
  await h.image.importFile(h.file, { from: 4, to: 6 }, h.key, 1)
  expect(h.calls.find(r => r.kind === 'prepare_image')).toEqual({ kind: 'prepare_image', draft_id: 'a', edit_session: 'lease-a', generation: 1, from: 4, to: 6, content_base64: 'AQIDBA==' })
  expect(h.inserted()).toBe(1); expect(h.notes.state.content).toBe('😀\r\n![](../attachments/ab/abcd.png)')
  expect(h.notes.state.generation).toBe(2); expect(h.notes.state.persistedGeneration).toBe(2); expect(h.notes.state.savedGeneration).toBe(1)
  expect(h.image.state.pending).toBeNull(); expect(h.image.state.error).toBe('')
})

it('recovers a committed import after losing its reply with only the original read and no duplicate insert', async () => {
  const h = harness('lost-apply'); await h.notes.openDraft('a'); h.image.bind()
  await h.image.importFile(h.file, { from: 6, to: 6 }, h.key, 1)
  expect(h.image.state.unknown).toBe(true); expect(h.notes.state.generation).toBe(1)
  await h.image.check()
  expect(h.calls.filter(r => r.kind === 'apply_image')).toHaveLength(1)
  expect(h.calls.filter(r => r.kind === 'observe_image')).toEqual([{ kind: 'observe_image', operation_id: 'original-image-id' }])
  expect(h.inserted()).toBe(1); expect(h.notes.state.generation).toBe(2); expect(h.image.state.unknown).toBe(false)
})

it('freezes a Canvas image node and recovers a lost reply without using Markdown selection offsets', async () => {
  const h = harness('lost-apply', true); await h.notes.openDraft('a'); h.image.bind()
  await h.image.importFile(h.file, { x: -40, y: 120 }, h.key, 1)
  expect(h.calls.find(r => r.kind === 'prepare_canvas_image')).toEqual({ kind: 'prepare_canvas_image', draft_id: 'a', edit_session: 'lease-a', generation: 1, x: -40, y: 120, content_base64: 'AQIDBA==' })
  expect(h.calls.some(r => r.kind === 'prepare_image')).toBe(false); expect(h.image.state.unknown).toBe(true)
  expect(h.inserted()).toBe(0)
  await h.image.check()
  expect(h.calls.filter(r => r.kind === 'apply_image')).toHaveLength(1)
  expect(h.calls.filter(r => r.kind === 'observe_image')).toHaveLength(1)
  expect(h.inserted()).toBe(1); expect(h.image.state.unknown).toBe(false)
  const actual = JSON.parse(h.notes.state.content)
  expect(actual.original).toBe('中文 😀'); expect(actual.nodes[0]).toMatchObject({ file: 'attachments/ab/abcd.png', x: -40, y: 120 })
})

it('finds an unexecuted prepared import read-only, then explicitly retries its original ID', async () => {
  const h = harness('lost-prepare'); await h.notes.openDraft('a'); h.image.bind()
  await h.image.importFile(h.file, { from: 6, to: 6 }, h.key, 1)
  expect(h.image.state.pending).toBeNull(); expect(h.image.state.unknown).toBe(true)
  await h.image.check(); expect(h.calls.some(r => r.kind === 'apply_image')).toBe(false)
  await h.image.retry()
  expect(h.calls.filter(r => r.kind === 'prepare_image')).toHaveLength(1)
  expect(h.calls.filter(r => r.kind === 'apply_image')).toEqual([{ kind: 'apply_image', operation_id: 'original-image-id' }])
  expect(h.inserted()).toBe(1)
})

it('rejects selection from older text and oversize files before any import write', async () => {
  const h = harness(); await h.notes.openDraft('a'); h.image.bind(); h.notes.edit('later text')
  await h.image.importFile(h.file, { from: 0, to: 0 }, h.key, 1)
  expect(h.image.state.error).toBe('MOBILE_EDITOR_CONTENT_CHANGED')
  await h.image.importFile({ size: 5 * 1024 * 1024 + 1 } as File, { from: 0, to: 0 }, h.key, 2)
  expect(h.image.state.error).toBe('MOBILE_IMAGE_TOO_LARGE'); expect(h.calls.some(r => r.kind === 'prepare_image')).toBe(false)
})

it('does not import into another view when file bytes arrive after the original editor was replaced', async () => {
  const h = harness(); await h.notes.openDraft('a'); h.image.bind()
  let resolve!: (value: ArrayBuffer) => void
  const delayed = { size: 4, arrayBuffer: () => new Promise<ArrayBuffer>(yes => { resolve = yes }) } as File
  const pending = h.image.importFile(delayed, { from: 0, to: 0 }, h.key, 1)
  for (let i = 0; i < 20; i++) await Promise.resolve()
  h.image.bind(); resolve(new ArrayBuffer(4)); await pending
  expect(h.calls.some(r => r.kind === 'prepare_image')).toBe(false); expect(h.inserted()).toBe(0); expect(h.image.state.busy).toBe(false)
})
