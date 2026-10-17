import { describe, expect, it, vi } from 'vitest'
import { createCycleSession } from './syncCycleSession'
import type { CycleCommand, CycleReply, CycleSnapshot, CycleStatus, NativeCycle, Resolution, SyncDecision, SyncTarget } from './syncCycleTypes'
import type { SyncConflictReview } from '../../src/contracts/sync'

const ids = Array.from({ length: 12 }, (_, index) => `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`)
const owner: SyncTarget = { vault_id: ids[0]!, binding_id: ids[1]!, expected: {
  revision: 1, generation: 1, endpoint: 'https://sync.example/', account: 'owned-reader', operation_id: ids[2]!,
} }
const originalStatus = (): CycleStatus => ({ vault_id: owner.vault_id,
  binding: { id: owner.binding_id, endpoint: owner.expected.endpoint, account: owner.expected.account, remote_vault: 'owned-remote', cursor: 1 },
  paused: false, incoming_boundary: null, pending_outbox: 3, queued_jobs: 2, open_conflicts: 1, pending_decisions: 0,
  attempts: [], conflicts: [], next_after: null, last_cycle_success: null })
const statusReply = (value = originalStatus()): CycleReply => ({ kind: 'result', response: { kind: 'status', status: value } })
const content = (value: string) => ({ exists: true, hash: 'a'.repeat(64), byte_size: new TextEncoder().encode(value).length,
  text: value, preview_bytes: new TextEncoder().encode(value).length, truncated: false })
const originalReview = (): SyncConflictReview => ({ vault_id: owner.vault_id, binding_id: owner.binding_id, sequence: 3,
  file_id: ids[3]!, local_file_id: ids[3]!, local_path: 'notes/中文.md', remote: { sequence: 3, base_revision: 1, path: 'notes/中文.md', operation: 'put' },
  local: content('本地\r\n\0'), incoming: content('<script>原文</script>'), base: content('原始'), related: [], fingerprint: 'b'.repeat(64) })
function originalDecision(frozen: Resolution): SyncDecision {
  return { sequence: frozen.sequence, choice: frozen.choice, destination: frozen.destination, expected_hash: frozen.expected_hash,
    fingerprint: frozen.fingerprint, operation_id: ids[4]!, rename_id: ids[5]!, copy_id: ids[6]!, retire_id: ids[7]!, restore_id: ids[8]!, state: 'pending' }
}
const decisionReply = (value: SyncDecision | null): CycleReply => ({ kind: 'result', response: { kind: 'decision', decision: value } })
function driver(extra?: NativeCycle) {
  return vi.fn<NativeCycle>(async request => {
    if (extra) return extra(request)
    if (request.action !== 'run') throw new Error('Unexpected native command')
    switch (request.request.action) {
      case 'status': case 'now': return statusReply()
      case 'decision': return decisionReply(null)
      case 'review': return { kind: 'result', response: { kind: 'review', review: originalReview() } }
      default: throw new Error('Unexpected native request')
    }
  })
}
async function ready(native = driver()) {
  const newId = vi.fn(() => ids[9]!), session = createCycleSession(native, newId)
  session.context(owner); expect(await session.readStatus()).toBe(true)
  return { native, newId, session }
}
const snapshot = (running = true, generation = 1): CycleSnapshot => ({ state: 'known', observation: { target: owner,
  invocation_id: ids[9]!, generation, error: null, activity: { running, phase: running ? 'pull' : 'complete', transfer: null,
    uploaded_revisions: 1, received_revisions: 2, started_at: 100, finished_at: running ? null : 101 } } })

describe('native foreground cycle view session', () => {
  it('requires a current read before a manual cycle and never turns reads or language rerenders into network actions', async () => {
    const native = driver(), newId = vi.fn(() => ids[9]!), session = createCycleSession(native, newId)
    session.context(owner); expect(await session.now()).toBe(false); expect(newId).not.toHaveBeenCalled()
    await session.readStatus(); session.context(JSON.parse(JSON.stringify(owner))); await session.now()
    expect(native.mock.calls.map(([request]) => request)).toEqual([
      { action: 'run', request: { action: 'status', target: owner, after: 0, limit: 20 } },
      { action: 'run', request: { action: 'now', target: owner }, invocation_id: ids[9] },
    ])
    expect(session.state.status?.pending_outbox).toBe(3)
    expect(session.state.cycleChecked).toBe(true)
  })

  it('retains a lost cycle, reads activity and durable queue before an explicit new manual cycle', async () => {
    const native = driver(async command => {
      if (command.action === 'activity') return { kind: 'activity', snapshot: { state: 'inactive' } }
      if (command.action === 'run' && command.request.action === 'status') return statusReply()
      throw new Error('SYNC_NETWORK_ERROR')
    })
    const { session, newId } = await ready(native)
    expect(await session.now()).toBe(false); expect(session.state.cycleUnknown).toBe(true)
    expect(await session.now()).toBe(false); expect(newId).toHaveBeenCalledOnce()
    await session.readActivity(); expect(native.mock.calls.at(-1)?.[0]).toEqual({ action: 'activity', target: owner, invocation_id: ids[9] })
    expect(await session.now()).toBe(false)
    await session.readStatus(); expect(session.state.cycleChecked).toBe(true)
    expect(native.mock.calls.filter(([command]) => command.action === 'run' && command.request.action === 'now')).toHaveLength(1)
  })

  it('allows original Stop while Now is pending and rejects late progress, completion and another identity', async () => {
    let finish!: (reply: CycleReply) => void
    const native = driver(async command => {
      if (command.action === 'stop') return { kind: 'stopped', state: 'cancelled' }
      if (command.action === 'run' && command.request.action === 'now') return new Promise(resolve => { finish = resolve })
      return statusReply()
    })
    const { session } = await ready(native), pending = session.now()
    expect(session.event(snapshot())).toBe(true); expect(session.state.cycling).toBe(true)
    expect(await session.stop()).toBe(true); expect(session.state.activity?.phase).toBe('cancelled')
    expect(session.event(snapshot())).toBe(false)
    finish(statusReply()); expect(await pending).toBe(false)
    expect(session.state.cycleChecked).toBe(false); expect(session.state.cycling).toBe(false)
    const changed = { ...owner, expected: { ...owner.expected, revision: 3 } }
    session.context(changed); expect(session.event(snapshot())).toBe(false)
    expect(session.state.status).toBeNull(); expect(session.state.activity).toBeNull()
    expect(native.mock.calls.filter(([command]) => command.action === 'stop')).toEqual([[{ action: 'stop', target: owner, invocation_id: ids[9] }]])
  })

  it('does not regress generations or reopen terminal activity and ignores ownerless events', async () => {
    let finish!: (reply: CycleReply) => void
    const native = driver(async command => command.action === 'run' && command.request.action === 'now'
      ? new Promise(resolve => { finish = resolve }) : statusReply())
    const { session } = await ready(native), pending = session.now()
    expect(session.event(snapshot(false, 4))).toBe(true)
    expect(session.event(snapshot(true, 4))).toBe(false)
    expect(session.event(snapshot(true, 3))).toBe(false)
    expect(session.event({ state: 'inactive' })).toBe(false)
    expect(session.state.activity?.phase).toBe('complete')
    finish(statusReply()); await pending
    expect(session.state.cycling).toBe(false)
  })

  it('isolates late requests through account A/B/A, background clearing and disposal without sending stop or writes', async () => {
    let answer!: (reply: CycleReply) => void
    const native = driver(() => new Promise(resolve => { answer = resolve })), session = createCycleSession(native)
    session.context(owner); const pending = session.readStatus()
    session.context({ ...owner, expected: { ...owner.expected, revision: 2 } }); session.context(owner)
    answer(statusReply()); expect(await pending).toBe(false); expect(session.state.status).toBeNull()
    const again = session.readStatus(); session.clear(); answer(statusReply()); expect(await again).toBe(false)
    const final = session.readStatus(); session.dispose(); answer(statusReply()); expect(await final).toBe(false)
    expect(native.mock.calls.every(([command]) => command.action === 'run' && command.request.action === 'status')).toBe(true)
  })

  it('looks up the original decision before showing verified raw content and freezes exact bytes and scope', async () => {
    const { session, native, newId } = await ready()
    expect(await session.readReview(3)).toBe(true)
    expect(native.mock.calls.slice(-2).map(([command]) => command.action === 'run' && command.request.action)).toEqual(['decision', 'review'])
    expect(session.state.review?.local.text).toBe('本地\r\n\0')
    const frozen = session.freeze('copy', 'attachments/原样副本.md')!
    expect(frozen).toEqual({ target: owner, sequence: 3, choice: 'copy', destination: 'attachments/原样副本.md', expected_hash: 'a'.repeat(64), fingerprint: 'b'.repeat(64) })
    expect(Object.isFrozen(frozen.target.expected)).toBe(true); expect(newId).not.toHaveBeenCalled()
    for (const path of ['', '/outside', '../outside', 'notes//copy.md', 'notes/./copy.md']) expect(session.freeze('copy', path)).toBeNull()
    session.context(JSON.parse(JSON.stringify(owner))); expect(session.freeze('copy', frozen.destination)).toEqual(frozen)
  })

  it('preserves an unknown frozen resolution and only resumes its read original five IDs and body', async () => {
    let frozen!: Resolution, original!: SyncDecision, sent = 0
    const base = driver(), native = driver(async command => {
      if (command.action === 'run' && command.request.action === 'resolve') {
        sent++; if (sent === 1) throw new Error('SYNC_NETWORK_ERROR')
        return decisionReply({ ...original, state: 'completed' })
      }
      if (command.action === 'run' && command.request.action === 'decision' && original) return decisionReply(original)
      return base(command)
    })
    const { session } = await ready(native); await session.readReview(3)
    frozen = session.freeze('remote')!; original = originalDecision(frozen)
    expect(await session.confirm(frozen)).toBe(false)
    expect(session.state.original).toEqual(frozen); expect(await session.confirm(frozen)).toBe(false)
    expect(await session.resume(frozen)).toBe(false); expect(await session.readReview(3)).toBe(false)
    await session.readDecision(); expect(session.state.decision).toEqual(original)
    const changed = { ...frozen, destination: 'another.md' }
    expect(await session.resume(changed)).toBe(false); expect(await session.resume(frozen)).toBe(true)
    expect(session.state.decision?.state).toBe('completed')
    expect(native.mock.calls.filter(([command]) => command.action === 'run' && command.request.action === 'resolve').map(([command]) => command)).toEqual([
      { action: 'run', request: { action: 'resolve', ...frozen } }, { action: 'run', request: { action: 'resolve', ...frozen } },
    ])
    expect(session.state.decision?.operation_id).toBe(ids[4]); expect(session.state.decision?.restore_id).toBe(ids[8])
  })

  it('only retries the original frozen body after a read proves its decision is missing', async () => {
    let writes = 0, saved: Resolution | undefined
    const base = driver(), native = driver(async command => {
      if (command.action === 'run' && command.request.action === 'resolve') {
        writes++; saved = command.request
        if (writes === 1) throw new Error('SYNC_NETWORK_ERROR')
        return decisionReply(originalDecision(command.request))
      }
      return base(command)
    })
    const { session } = await ready(native); await session.readReview(3)
    const frozen = session.freeze('local')!; await session.confirm(frozen)
    expect(await session.resume(frozen)).toBe(false)
    await session.readDecision(); expect(session.state.originalMissing).toBe(true)
    expect(saved).toEqual({ action: 'resolve', ...frozen })
    await session.resume(frozen); expect(writes).toBe(2)
  })

  it('preserves a known stale review rejection and requires a read and new explicit review for changed content', async () => {
    let changed = false, writes = 0
    const base = driver(), native = driver(async command => {
      if (command.action === 'run' && command.request.action === 'resolve') {
        writes++
        if (writes === 1) throw new Error('SYNC_REVIEW_CHANGED')
        return decisionReply({ ...originalDecision(command.request), state: 'completed' })
      }
      if (changed && command.action === 'run' && command.request.action === 'review') {
        const value = originalReview()
        value.local = { ...content('Concurrent source'), hash: 'c'.repeat(64) }; value.fingerprint = 'd'.repeat(64)
        return { kind: 'result', response: { kind: 'review', review: value } }
      }
      return base(command)
    })
    const { session } = await ready(native); await session.readReview(3)
    const frozen = session.freeze('copy', 'notes/copy.md')!
    expect(await session.confirm(frozen)).toBe(false)
    expect(session.state.error).toBe('SYNC_REVIEW_CHANGED')
    expect(session.state.original).toEqual(frozen); expect(session.state.decision).toBeNull()
    expect(session.keepOriginal()).toBe(false); expect(await session.confirm(frozen)).toBe(false)
    expect(await session.readReview(3)).toBe(false); expect(writes).toBe(1)
    await session.readDecision(); expect(session.state.originalMissing).toBe(true)
    expect(session.keepOriginal()).toBe(true); changed = true
    expect(await session.readReview(3)).toBe(true)
    const current = session.freeze('copy', frozen.destination)!
    expect(current.expected_hash).toBe('c'.repeat(64)); expect(current.fingerprint).toBe('d'.repeat(64))
    expect(frozen.expected_hash).toBe('a'.repeat(64)); expect(frozen.fingerprint).toBe('b'.repeat(64))
    expect(await session.confirm(frozen)).toBe(false); expect(writes).toBe(1)
    expect(await session.confirm(current)).toBe(true); expect(writes).toBe(2)
    expect(native.mock.calls.filter(([command]) => command.action === 'run' && command.request.action === 'resolve')
      .map(([command]) => command)).toEqual([
      { action: 'run', request: { action: 'resolve', ...frozen } },
      { action: 'run', request: { action: 'resolve', ...current } },
    ])
  })

  it('finds an existing pending decision on cold inspection and never replaces a legacy missing fingerprint', async () => {
    const frozen: Resolution = { target: owner, sequence: 3, choice: 'remote', destination: '', expected_hash: 'a'.repeat(64), fingerprint: 'b'.repeat(64) }
    const original = { ...originalDecision(frozen), fingerprint: null }
    const native = driver(async command => command.action === 'run' && command.request.action === 'decision' ? decisionReply(original) : statusReply())
    const { session } = await ready(native)
    expect(await session.readReview(3)).toBe(false)
    expect(session.state.decision).toEqual(original); expect(session.state.review).toBeNull(); expect(session.state.original).toBeNull()
    expect(await session.resume(frozen)).toBe(false); expect(native.mock.calls).toHaveLength(2)
  })

  it('refuses changed original decision IDs or reviewed content and clears private provider errors', async () => {
    const base = driver(), native = driver(async command => {
      if (command.action === 'run' && command.request.action === 'resolve') throw new Error('PRIVATE_PROVIDER_SECRET')
      return base(command)
    })
    const { session } = await ready(native); await session.readReview(3)
    const frozen = session.freeze('remote')!
    expect(await session.confirm({ ...frozen, fingerprint: 'c'.repeat(64) })).toBe(false)
    await session.confirm(frozen); expect(session.state.error).toBe('SYNC_CONNECTION_FAILED')
    const old = originalDecision(frozen)
    native.mockResolvedValueOnce(decisionReply(old)); await session.readDecision()
    native.mockResolvedValueOnce(decisionReply({ ...old, copy_id: ids[10]! })); expect(await session.readDecision()).toBe(false)
    expect(session.state.decision).toEqual(old); expect(session.state.error).toBe('SYNC_RESPONSE_INVALID')
  })

  it('rejects wrong target status, oversized pages and fabricated progress before replacing valid data', async () => {
    const { session, native } = await ready(), valid = session.state.status
    for (const value of [ { ...originalStatus(), vault_id: ids[11]! }, { ...originalStatus(), queued_jobs: -1 }, { ...originalStatus(), next_after: 9 } ]) {
      native.mockResolvedValueOnce(statusReply(value)); expect(await session.readStatus()).toBe(false)
      expect(session.state.status).toEqual(valid)
    }
    await session.readStatus()
    const pending = session.now(); const bad = snapshot()
    if (bad.state === 'known') bad.observation.activity.transfer = { direction: 'download', path: 'notes/a.md', total_bytes: 4, bytes_done: 5, resumed_bytes: 0, transferred_bytes: 5, phase: 'receiving' }
    expect(session.event(bad)).toBe(false); await pending
    expect(session.state.activity).toBeNull()
  })

  it('cancels only view listeners and ignores late activity reads without performing writes on disposal', async () => {
    let answer!: (reply: CycleReply) => void
    const base = driver(), native = driver(async command => command.action === 'activity' ? new Promise(resolve => { answer = resolve }) : base(command))
    const { session } = await ready(native); await session.now()
    const pending = session.readActivity(); session.dispose(); answer({ kind: 'activity', snapshot: snapshot() })
    expect(await pending).toBe(false); expect(session.state.activity).toBeNull()
    expect(native.mock.calls.at(-1)?.[0].action).toBe('activity')
    expect(native.mock.calls.some(([command]) => command.action === 'stop' || command.action === 'run' && command.request.action === 'resolve')).toBe(false)
  })
})
