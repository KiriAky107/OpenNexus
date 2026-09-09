// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { RecordBinding, type RecordDocument } from './recordBinding'
const h1 = '1'.repeat(64), h2 = '2'.repeat(64), h3 = '3'.repeat(64)
function document(value: number, hash = h1): RecordDocument<{ value: number }> { return { record: { schema: 1, kind: 'preferences', id: 'editor', data: { value } }, hash, file_id: 'file' } }
function setup(invoke: (command: string, args: Record<string, unknown>) => Promise<unknown>, vaultId = 'vault') {
  let local = { value: 1 }
  const apply = vi.fn((data: { value: number }) => { local = data })
  const binding = new RecordBinding({ vaultId, kind: 'preferences', id: 'editor', read: () => local, apply, invoke: async <R>(command: string, args: Record<string, unknown>) => await invoke(command, args) as R, storage: localStorage })
  return { binding, apply, edit: (value: number) => { local = { value }; binding.capture() }, read: () => local }
}
beforeEach(() => { localStorage.clear() })
it('reopens a conflicting draft without overwriting it and only rebases after a decision', async () => {
  const invoke = vi.fn().mockResolvedValue(document(1))
  const original = setup(invoke); await original.binding.poll(); original.edit(2)
  const firstDraft = JSON.parse(localStorage.getItem('opennexus-record-draft:vault:preferences:editor')!)
  original.binding.stop()
  invoke.mockImplementation(async command => { if (command === 'record_get') return document(3, h3); throw new Error('REVISION_CONFLICT') })
  const reopened = setup(invoke); await reopened.binding.poll()
  expect(reopened.read()).toEqual({ value: 2 }); expect(reopened.binding.conflicted).toBe(true)
  expect(JSON.parse(localStorage.getItem('opennexus-record-draft:vault:preferences:editor')!)).toEqual(firstDraft)
  invoke.mockImplementation(async (command, args) => command === 'record_get' ? document(3, h3) : { ...document(2, h2), record: args.request.record })
  await reopened.binding.keepLocal()
  const write = invoke.mock.calls.filter(([command]) => command === 'record_write').at(-1)![1].request
  expect(write.expected).toBe(h3); expect(write.operation_id).not.toBe(firstDraft.operation_id)
  expect(reopened.binding.hasDraft).toBe(false)
})
it('keeps drafts separated by Vault and does not seed an empty Vault during polling', async () => {
  const invoke = vi.fn().mockResolvedValue(null), first = setup(invoke)
  await first.binding.poll(); expect(invoke.mock.calls.some(([command]) => command === 'record_write')).toBe(false)
  first.edit(2); first.binding.stop()
  const second = setup(invoke, 'second'); await second.binding.poll()
  expect(second.binding.hasDraft).toBe(false); expect(second.read()).toEqual({ value: 1 })
})
it('orders edits made during an in-flight commit against its confirmed hash', async () => {
  let release!: (value: RecordDocument<{ value: number }>) => void
  const invoke = vi.fn().mockResolvedValue(document(1)), state = setup(invoke)
  await state.binding.poll(); state.edit(2)
  invoke.mockImplementation(async command => command === 'record_get' ? document(1) : new Promise(resolve => { release = resolve }))
  const pending = state.binding.poll(); await vi.waitFor(() => expect(release).toBeTypeOf('function'))
  state.edit(3); release(document(2, h2)); await pending
  const draft = JSON.parse(localStorage.getItem('opennexus-record-draft:vault:preferences:editor')!)
  expect(draft.expected).toBe(h2); expect(draft.record.data).toEqual({ value: 3 })
})
it('rejects a remote-choice response that would discard a newer local edit', async () => {
  const invoke = vi.fn().mockResolvedValue(document(1)), state = setup(invoke)
  await state.binding.poll(); state.edit(2)
  let release!: (value: RecordDocument<{ value: number }>) => void
  invoke.mockImplementation(() => new Promise(resolve => { release = resolve }))
  const choice = state.binding.useRemote(); await Promise.resolve(); state.edit(4); release(document(3, h3)); await choice
  expect(state.binding.error).toBe('PREFERENCE_CHANGED'); expect(state.read()).toEqual({ value: 4 }); expect(state.binding.hasDraft).toBe(true)
})
it('waits for a live poll before seeding and reuses its result', async () => {
  let release!: (value: null) => void
  const invoke = vi.fn().mockImplementationOnce(() => new Promise(resolve => { release = resolve })).mockImplementation(async command => command === 'record_get' ? null : document(1))
  const state = setup(invoke), poll = state.binding.poll(), seed = state.binding.seed()
  release(null); await poll; await seed
  expect(invoke.mock.calls.filter(([command]) => command === 'record_write')).toHaveLength(1)
})

it('retains the committed draft when removing its durable receipt fails', async () => {
  const invoke = vi.fn().mockResolvedValue(document(1)), state = setup(invoke)
  await state.binding.poll(); state.edit(2)
  invoke.mockImplementation(async command => command === 'record_get' ? document(1) : document(2, h2))
  const remove = vi.spyOn(localStorage, 'removeItem').mockImplementation(() => { throw new Error('disk unavailable') })
  await state.binding.poll()
  expect(state.binding.hasDraft).toBe(true)
  expect(state.binding.error).toBe('PREFERENCE_DRAFT_STORE_FAILED')
  expect(JSON.parse(localStorage.getItem('opennexus-record-draft:vault:preferences:editor')!).record.data).toEqual({ value: 2 })
  remove.mockRestore()
  state.edit(3); await state.binding.poll()
  expect(state.binding.hasDraft).toBe(false)
})
