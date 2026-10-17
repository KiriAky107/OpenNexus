import { describe, expect, it, vi } from 'vitest'
import { createAssociationSession } from './syncAssociationSession'
import type { AssociationRequest, AssociationResponse, BindingIntent, BindingPreview, BindingRequest, BindingStatus, NativeAssociation } from './syncAssociationTypes'
import type { AuthSelection } from './syncAuthTypes'

const selection: AuthSelection = { revision: 1, endpoint: 'https://sync.example/', account: 'owned-a', operation_id: '00000000-0000-4000-8000-000000000001', generation: 1 }
const vault = '10000000-0000-4000-8000-000000000001', operation = '20000000-0000-4000-8000-000000000001'
const empty: BindingStatus = { vault_id: vault, binding: null, original_operation_id: null, another_connection: false }
const page: BindingPreview = { selection, local_vault: vault, remote_vault: 'remote-a', fingerprint: 'a'.repeat(64), boundary: 0,
  total: 1, offset: 0, limit: 100, items: [{ path: '<script>原文.md', action: 'upload' }] }
const original: BindingRequest = { operation_id: operation, local_vault: vault, remote_vault: 'remote-a', selection, preview_fingerprint: page.fingerprint }
const prepared: BindingIntent = { request: original, phase: 'prepared', attempt_revision: 1, binding: null }
const bound: BindingIntent = { ...prepared, phase: 'bound', attempt_revision: 2,
  binding: { id: '30000000-0000-4000-8000-000000000001', endpoint: selection.endpoint, account: selection.account, remote_vault: 'remote-a', cursor: 0 } }
function driver(action?: (request: AssociationRequest) => Promise<AssociationResponse>) {
  return vi.fn<NativeAssociation>(async request => {
    if (request.action === 'status') return { kind: 'status', status: empty }
    if (request.action === 'preview') return { kind: 'preview', page }
    if (action) return action(request)
    throw new Error('Unexpected native action')
  })
}
async function ready(native = driver()) {
  const id = vi.fn(() => operation), session = createAssociationSession(native, id)
  session.context(selection, 'remote-a'); await session.status(); await session.preview()
  return { native, id, session }
}
describe('native association view session', () => {
  it('only reads previews, freezes once, and keeps raw names and the complete original selection', async () => {
    const { native, id, session } = await ready()
    expect(native.mock.calls.map(([value]) => value.action)).toEqual(['status', 'preview'])
    expect(session.state.preview?.items[0]?.path).toBe('<script>原文.md')
    expect(id).not.toHaveBeenCalled()
    expect(session.freeze()).toEqual(original)
    expect(id).toHaveBeenCalledOnce()
  })
  it('holds a lost confirmation and only explicitly resumes the read original body and revision', async () => {
    const native = driver(async request => {
      if (request.action === 'bind') throw new Error('SYNC_NETWORK_ERROR')
      if (request.action === 'operation') return { kind: 'operation', intent: prepared }
      if (request.action === 'resume') return { kind: 'operation', intent: bound }
      throw new Error('Unexpected action')
    })
    const { session } = await ready(native), frozen = session.freeze()!
    expect(await session.confirm(frozen)).toBe(false)
    expect(session.state.original).toEqual(original)
    expect(await session.confirm(frozen)).toBe(false)
    expect(native.mock.calls.filter(([value]) => value.action === 'bind')).toHaveLength(1)
    expect(await session.readOriginal()).toBe(true)
    expect(native.mock.calls.at(-1)?.[0]).toEqual({ action: 'operation', expected: selection, operation_id: operation })
    expect(await session.resume({ ...prepared, attempt_revision: 2 })).toBe(false)
    expect(await session.resume(prepared)).toBe(true)
    expect(native.mock.calls.at(-1)?.[0]).toEqual({ action: 'resume', request: original, observed_revision: 1 })
    expect(session.state.receipt?.binding?.id).toBe(bound.binding!.id)
    expect(session.state.status?.binding).toBeNull() // Historical receipt is not a refreshed current binding.
  })
  it('retries an absent intent only after a read and with its exact original body and ID', async () => {
    let sends = 0
    const native = driver(async request => {
      if (request.action === 'bind') { if (++sends === 1) throw new Error('SYNC_NETWORK_ERROR'); return { kind: 'operation', intent: bound } }
      if (request.action === 'operation') return { kind: 'operation', intent: null }
      throw new Error('Unexpected action')
    })
    const { session } = await ready(native), frozen = session.freeze()!
    await session.confirm(frozen)
    expect(await session.retryMissing(frozen)).toBe(false)
    await session.readOriginal()
    expect(session.state.originalMissing).toBe(true)
    expect(await session.retryMissing({ ...frozen, remote_vault: 'different' })).toBe(false)
    expect(await session.retryMissing(frozen)).toBe(true)
    expect(native.mock.calls.filter(([value]) => value.action === 'bind').map(([value]) => value)).toEqual([{ action: 'bind', request: original }, { action: 'bind', request: original }])
  })
  it('ignores late responses after account changes and clears metadata without sending another action', async () => {
    let resolve!: (value: AssociationResponse) => void
    const native = driver(); native.mockImplementationOnce(() => new Promise(accept => { resolve = accept }))
    const session = createAssociationSession(native)
    session.context(selection, 'remote-a'); const pending = session.status()
    session.context({ ...selection, revision: 2, account: 'owned-b' }, 'remote-a')
    resolve({ kind: 'status', status: empty }); expect(await pending).toBe(false)
    expect(session.state.status).toBeNull(); expect(native).toHaveBeenCalledOnce()
  })
  it('recovers a committed original ID through cold status and reads it without a bind or resume', async () => {
    const native = vi.fn<NativeAssociation>(async request => request.action === 'status'
      ? { kind: 'status', status: { ...empty, binding: bound.binding, original_operation_id: operation } }
      : { kind: 'operation', intent: bound })
    const session = createAssociationSession(native)
    session.context(selection, 'remote-a'); await session.status()
    await session.readOriginal(session.state.status!.original_operation_id!)
    expect(session.state.receipt?.request).toEqual(original)
    expect(native.mock.calls.map(([value]) => value.action)).toEqual(['status', 'operation'])
  })
  it('rejects another account result and drops a changed fingerprint instead of combining frozen pages', async () => {
    const { native, session } = await ready()
    native.mockResolvedValueOnce({ kind: 'status', status: { ...empty, binding: { ...bound.binding!, account: 'other' } } })
    expect(await session.status()).toBe(false); expect(session.state.status?.binding).toBeNull()
    native.mockResolvedValueOnce({ kind: 'preview', page: { ...page, fingerprint: 'b'.repeat(64), offset: 100, items: [] } })
    expect(await session.preview(100)).toBe(false)
    expect(session.state.error).toBe('SYNC_PREVIEW_CHANGED'); expect(session.state.preview).toBeNull()
  })
  it('reads old prepared metadata but never resumes it under a changed native selection', async () => {
    const native = driver(async () => ({ kind: 'pending', page: { items: [prepared], next_after: null } }))
    const { session } = await ready(native)
    session.context({ ...selection, revision: 2, generation: 2 }, 'remote-a'); await session.status(); await session.pending()
    expect(session.state.pending?.items[0]?.request).toEqual(original)
    expect(await session.resume(prepared)).toBe(false)
    expect(native.mock.calls.some(([value]) => value.action === 'resume')).toBe(false)
  })
})
