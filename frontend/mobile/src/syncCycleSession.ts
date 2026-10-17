import { reactive } from 'vue'
import type { SyncActivity, SyncConflictReview, SyncContent } from '../../src/contracts/sync'
import type { CycleCommand, CycleObservation, CycleReply, CycleSnapshot, CycleStatus, NativeCycle, OriginalDecisionPage, Resolution, SyncDecision, SyncTarget } from './syncCycleTypes'

const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const integer = (value: number, minimum = 0) => Number.isSafeInteger(value) && value >= minimum
const uuid = (value: string) => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
const digest = (value: string) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
const text = (value: string, max = 1024) => typeof value === 'string' && new TextEncoder().encode(value).length <= max && !value.includes('\0')
const invalid = (): never => { throw new Error('SYNC_RESPONSE_INVALID') }
const safeCodes = new Set(['SYNC_CONNECTION_FAILED', 'SYNC_RESPONSE_INVALID', 'SYNC_NETWORK_ERROR', 'SYNC_TLS_UNAVAILABLE',
  'SYNC_REDIRECT_BLOCKED', 'SYNC_FOREGROUND_CHANGED', 'SYNC_FOREGROUND_REQUIRED', 'SYNC_CANCELLED', 'SYNC_PAUSED',
  'SYNC_BINDING_CHANGED', 'SYNC_NOT_BOUND', 'SYNC_TARGET_INVALID', 'SYNC_PAGE_INVALID', 'SYNC_AUTH_SELECTION_CHANGED',
  'SYNC_AUTH_SELECTION_INVALID', 'SYNC_AUTH_IDENTITY_CHANGED', 'SYNC_AUTH_RECONCILIATION_REQUIRED', 'SYNC_LOGIN_REQUIRED',
  'SYNC_SESSION_EXPIRED', 'SESSION_EXPIRED', 'SYNC_CREDENTIALS_UNAVAILABLE', 'SYNC_CREDENTIALS_INVALID',
  'SYNC_ACTIVITY_BUSY', 'SYNC_ACTIVITY_LIMIT', 'SYNC_ACTIVITY_UNAVAILABLE', 'SYNC_ACTIVITY_RECONCILIATION_REQUIRED',
  'SYNC_ACTIVITY_REQUEST_INVALID', 'SYNC_ACTIVITY_ID_INVALID', 'SYNC_ACTIVITY_OWNER_CHANGED', 'SYNC_WORKER_INTERRUPTED',
  'SYNC_RESOLUTION_INVALID', 'SYNC_RESOLUTION_CHANGED', 'SYNC_CONFLICT_CHANGED', 'SYNC_REVIEW_UNSAFE_FILE',
  'SYNC_RESPONSE_TOO_LARGE', 'SYNC_TIME_INVALID', 'SYNC_ENDPOINT_INVALID', 'SYNC_LOGIN_INVALID', 'SYNC_LOGIN_REQUIRED',
  'MOBILE_HOST_REQUIRED', 'SYNC_PLATFORM_UNSUPPORTED', 'MOBILE_STORAGE_UNAVAILABLE', 'HOST_BUSY', 'VAULT_CHANGED',
  'QUOTA_EXCEEDED', 'FILESYSTEM_ERROR', 'DATABASE_ERROR', 'SYNC_REMOTE_ERROR', 'REVISION_CONFLICT', 'RATE_LIMITED'])
const machine = (value: unknown): string => typeof value === 'string' && safeCodes.has(value) ? value : 'SYNC_CONNECTION_FAILED'
export const sameTarget = (a: SyncTarget, b: SyncTarget) => a.vault_id === b.vault_id && a.binding_id === b.binding_id
  && a.expected.revision === b.expected.revision && a.expected.operation_id === b.expected.operation_id
  && a.expected.generation === b.expected.generation && a.expected.endpoint === b.expected.endpoint && a.expected.account === b.expected.account
function target(value: SyncTarget): SyncTarget {
  if (!uuid(value.vault_id) || !uuid(value.binding_id) || !uuid(value.expected.operation_id)
    || !integer(value.expected.revision, 1) || !integer(value.expected.generation, 1)
    || !text(value.expected.endpoint) || !value.expected.endpoint.startsWith('https://') || !text(value.expected.account, 320)) return invalid()
  return copy(value)
}
function status(value: CycleStatus, owner: SyncTarget, after: number): CycleStatus {
  if (value.vault_id !== owner.vault_id || value.binding.id !== owner.binding_id
    || value.binding.endpoint !== owner.expected.endpoint || value.binding.account !== owner.expected.account
    || !text(value.binding.remote_vault, 80) || !integer(value.binding.cursor) || typeof value.paused !== 'boolean'
    || ![value.pending_outbox, value.queued_jobs, value.open_conflicts, value.pending_decisions].every(n => integer(n))
    || value.incoming_boundary !== null && !integer(value.incoming_boundary)
    || value.last_cycle_success !== null && !integer(value.last_cycle_success)
    || !Array.isArray(value.conflicts) || value.conflicts.length > 20 || !Array.isArray(value.attempts) || value.attempts.length > 20) return invalid()
  let previous = after
  for (const conflict of value.conflicts) {
    const remote = conflict.remote
    if (!integer(conflict.sequence, previous + 1) || !text(conflict.file_id, 80) || !text(conflict.local_path)
      || conflict.recorded_local_hash !== '' && !digest(conflict.recorded_local_hash)
      || remote.sequence !== conflict.sequence || remote.file_id !== conflict.file_id || remote.vault_id !== value.binding.remote_vault
      || !integer(remote.base_revision) || remote.base_revision >= remote.sequence || !text(remote.path)
      || !integer(remote.size) || remote.size > 104857600 || !text(remote.operation_id, 80)
      || !['put', 'delete'].includes(remote.operation)
      || remote.operation === 'put' && !digest(remote.hash ?? '')
      || remote.operation === 'delete' && (remote.hash !== null || remote.size !== 0)) return invalid()
    previous = conflict.sequence
  }
  if (value.next_after !== null && (value.conflicts.length !== 20 || value.next_after !== previous)) return invalid()
  for (const attempt of value.attempts) {
    if (!uuid(attempt.operation_id) || !text(attempt.path) || !integer(attempt.attempts, 1)
      || !['running', 'interrupted', 'failed', 'succeeded'].includes(attempt.outcome) || !text(attempt.state, 32)
      || attempt.error !== null && (typeof attempt.error !== 'string' || attempt.error.length > 80)) return invalid()
  }
  const result = copy(value)
  result.attempts = result.attempts.map(item => ({ ...item, error: item.error === null ? null : machine(item.error) }))
  return result
}
function content(value: SyncContent): void {
  if (typeof value.exists !== 'boolean' || value.hash !== '' && !digest(value.hash) || !integer(value.byte_size)
    || value.byte_size > 104857600 || !integer(value.preview_bytes) || value.preview_bytes > 65536 || value.preview_bytes > value.byte_size
    || typeof value.truncated !== 'boolean' || value.text !== null && (typeof value.text !== 'string'
      || new TextEncoder().encode(value.text).length !== value.preview_bytes)) return invalid()
}
function review(value: SyncConflictReview, owner: SyncTarget, sequence: number): SyncConflictReview {
  if (value.vault_id !== owner.vault_id || value.binding_id !== owner.binding_id || value.sequence !== sequence
    || !text(value.local_path) || !digest(value.fingerprint) || !text(value.file_id, 80)
    || value.local_file_id !== null && !text(value.local_file_id, 80)
    || value.remote.sequence !== sequence || !text(value.remote.path) || !['put', 'delete'].includes(value.remote.operation)
    || !integer(value.remote.base_revision) || value.remote.base_revision >= sequence
    || !Array.isArray(value.related) || value.related.length > 8) return invalid()
  content(value.local); content(value.incoming)
  if (value.base) content(value.base)
  for (const item of value.related) {
    if (!text(item.path) || item.file_id !== null && !text(item.file_id, 80)) return invalid()
    content(item.content)
  }
  return copy(value)
}
function decision(value: SyncDecision | null, sequence: number): SyncDecision | null {
  if (value === null) return null
  const ids = [value.operation_id, value.rename_id, value.copy_id, value.retire_id, value.restore_id]
  if (value.sequence !== sequence || !['local', 'remote', 'copy'].includes(value.choice) || !text(value.destination)
    || value.choice !== 'copy' && value.destination !== '' || value.choice === 'copy' && !value.destination
    || value.expected_hash !== '' && !digest(value.expected_hash) || value.fingerprint !== null && !digest(value.fingerprint)
    || !['pending', 'completed'].includes(value.state) || !ids.every(uuid) || new Set(ids).size !== 5) return invalid()
  return copy(value)
}
function originalPage(value: OriginalDecisionPage, expected: SyncTarget, before: number | null): OriginalDecisionPage {
  if (value.vault_id !== expected.vault_id || value.binding_id !== expected.binding_id || value.before !== before
    || value.limit !== 20 || !Array.isArray(value.items) || value.items.length > 20
    || value.next_before !== null && (!integer(value.next_before, 1) || value.items.length !== 20 || value.next_before !== value.items.at(-1)?.sequence)) return invalid()
  const items = value.items.map((item, index) => {
    if (!item || !integer(item.sequence, 1) || before !== null && item.sequence >= before
      || index > 0 && item.sequence >= value.items[index - 1]!.sequence) return invalid()
    return decision(item, item.sequence)!
  })
  return { ...copy(value), items }
}
function activity(value: SyncActivity): SyncActivity {
  if (typeof value.running !== 'boolean' || !['idle', 'handshake', 'discover', 'pull', 'push', 'complete', 'failed', 'cancelled'].includes(value.phase)
    || !integer(value.uploaded_revisions) || !integer(value.received_revisions)
    || value.started_at !== null && !integer(value.started_at) || value.finished_at !== null && !integer(value.finished_at)) return invalid()
  const transfer = value.transfer
  if (transfer && (!text(transfer.path) || !['upload', 'download'].includes(transfer.direction)
    || !['sending', 'receiving', 'verified', 'cached', 'committing'].includes(transfer.phase)
    || ![transfer.total_bytes, transfer.bytes_done, transfer.resumed_bytes, transfer.transferred_bytes].every(n => integer(n))
    || transfer.total_bytes > 104857600 || transfer.bytes_done > transfer.total_bytes
    || transfer.resumed_bytes > transfer.bytes_done || transfer.transferred_bytes > transfer.bytes_done)) return invalid()
  return copy(value)
}
const sameResolution = (a: Resolution, b: Resolution) => sameTarget(a.target, b.target) && a.sequence === b.sequence
  && a.choice === b.choice && a.destination === b.destination && a.expected_hash === b.expected_hash && a.fingerprint === b.fingerprint
const blank = () => ({ busy: false, cycling: false, stopping: false, error: '', status: null as CycleStatus | null,
  activity: null as SyncActivity | null, invocationId: '', nativeGeneration: 0, cycleUnknown: false, cycleChecked: false,
  review: null as SyncConflictReview | null, decision: null as SyncDecision | null, original: null as Resolution | null,
  originalChecked: false, originalMissing: false, originals: null as OriginalDecisionPage | null })

export function createCycleSession(native: NativeCycle, newId: () => string = () => crypto.randomUUID()) {
  const state = reactive(blank())
  let owner: SyncTarget | null = null, epoch = 0, disposed = false, activityRead = 0, stoppedInvocation = ''
  function context(next: SyncTarget) {
    const value = target(next)
    if (owner && sameTarget(owner, value)) return
    owner = value; epoch++; activityRead++; stoppedInvocation = ''; Object.assign(state, blank())
  }
  const current = (version: number) => !disposed && version === epoch
  function failure(error: unknown) { state.error = machine(error instanceof Error ? error.message : error) }
  async function read<T>(request: CycleCommand, accept: (reply: CycleReply) => T): Promise<T | false> {
    if (disposed || !owner || state.busy || state.cycling) return false
    const version = epoch; state.busy = true; state.error = ''
    try { const reply = await native(copy(request)); return current(version) ? accept(reply) : false }
    catch (error) { if (current(version)) failure(error); return false }
    finally { if (current(version)) state.busy = false }
  }
  function readStatus(after = 0) {
    if (!owner || disposed || state.busy || state.cycling || !integer(after)) return Promise.resolve(false)
    state.cycleChecked = false
    const expected = copy(owner)
    return read({ action: 'run', request: { action: 'status', target: expected, after, limit: 20 } }, reply => {
      if (reply.kind !== 'result' || reply.response.kind !== 'status') return invalid()
      state.status = status(reply.response.status, expected, after); state.cycleChecked = true
      return true
    })
  }
  function readOriginals(before: number | null = null) {
    if (!owner || disposed || state.busy || state.cycling || state.stopping || before !== null && !integer(before, 1)
      || state.original && !state.originalChecked) return Promise.resolve(false)
    const expected = copy(owner)
    return read({ action: 'run', request: { action: 'originals', target: expected, before, limit: 20 } }, reply => {
      if (reply.kind !== 'result' || reply.response.kind !== 'originals') return invalid()
      state.originals = originalPage(reply.response.page, expected, before)
      return true
    })
  }
  function observe(snapshot: CycleSnapshot) {
    if (disposed || !owner || !state.invocationId) return false
    if (snapshot.state === 'inactive') { state.activity = null; state.cycling = false; state.cycleUnknown = true; state.cycleChecked = false; return true }
    if (snapshot.state !== 'known') return invalid()
    const value: CycleObservation = snapshot.observation
    if (!sameTarget(target(value.target), owner) || value.invocation_id !== state.invocationId) return false
    if (!integer(value.generation, 1)) return invalid()
    if (stoppedInvocation === value.invocation_id) return false
    if (value.generation < state.nativeGeneration || value.generation === state.nativeGeneration && state.activity && !state.activity.running && value.activity.running) return false
    state.activity = activity(value.activity); state.nativeGeneration = value.generation; state.cycling = value.activity.running
    if (value.error) state.error = machine(value.error)
    return true
  }
  function event(snapshot: CycleSnapshot) {
    // Native events must carry an original owner. An unscoped inactive event
    // cannot erase a newly selected view; only its explicit Activity read may.
    if (snapshot.state !== 'known') return false
    try { return observe(snapshot) } catch (error) { failure(error); return false }
  }
  async function readActivity() {
    if (!owner || !state.invocationId || disposed) return false
    const expected = copy(owner), id = state.invocationId, version = epoch, serial = ++activityRead
    try {
      const reply = await native({ action: 'activity', target: expected, invocation_id: id })
      if (!current(version) || id !== state.invocationId || serial !== activityRead) return false
      if (reply.kind !== 'activity') return invalid()
      return observe(reply.snapshot)
    } catch (error) { if (current(version) && id === state.invocationId && serial === activityRead) failure(error); return false }
  }
  async function now() {
    if (disposed || !owner || state.busy || state.cycling || state.stopping || !state.cycleChecked || state.status?.paused
      || state.original && !state.originalChecked) return false
    const expected = copy(owner), id = newId(), version = epoch
    if (!uuid(id)) { state.error = 'SYNC_ACTIVITY_ID_INVALID'; return false }
    state.invocationId = id; stoppedInvocation = ''; state.nativeGeneration = 0; state.activity = null; state.cycleChecked = false
    state.busy = true; state.cycling = true; state.error = ''; state.review = null; activityRead++
    try {
      const reply = await native({ action: 'run', request: { action: 'now', target: expected }, invocation_id: id })
      if (!current(version) || id !== state.invocationId || stoppedInvocation === id) return false
      if (reply.kind === 'activity') { observe(reply.snapshot); state.cycleUnknown = true; return true }
      if (reply.kind !== 'result' || reply.response.kind !== 'status') return invalid()
      state.status = status(reply.response.status, expected, 0); state.cycleUnknown = false; state.cycleChecked = true
      const observed = state.activity as SyncActivity | null // Native events may arrive while invoke is pending.
      if (observed) state.activity = { ...observed, running: false, phase: 'complete' }
      return true
    } catch (error) {
      if (current(version) && id === state.invocationId) { failure(error); state.cycleUnknown = true; state.cycleChecked = false }
      return false
    } finally { if (current(version) && id === state.invocationId) { state.busy = false; state.cycling = stoppedInvocation !== id && (state.activity?.running ?? false) } }
  }
  async function stop() {
    if (!owner || !state.invocationId || disposed || state.stopping) return false
    const expected = copy(owner), id = state.invocationId, version = epoch
    state.stopping = true
    try {
      const reply = await native({ action: 'stop', target: expected, invocation_id: id })
      if (!current(version) || id !== state.invocationId) return false
      if (reply.kind !== 'stopped' || !['cancelled', 'finished', 'inactive'].includes(reply.state)) return invalid()
      if (reply.state !== 'finished') stoppedInvocation = id
      if (reply.state === 'cancelled' && state.activity) state.activity = { ...state.activity, running: false, phase: 'cancelled' }
      else if (reply.state === 'inactive') state.activity = null
      state.cycling = false; state.cycleUnknown = true; state.cycleChecked = false; return true
    } catch (error) { if (current(version) && id === state.invocationId) failure(error); return false }
    finally { if (current(version) && id === state.invocationId) state.stopping = false }
  }
  function receiveDecision(reply: CycleReply, sequence: number, expected: SyncTarget) {
    if (reply.kind !== 'result' || reply.response.kind !== 'decision') return invalid()
    const value = decision(reply.response.decision, sequence)
    if (value && state.original && (!value.fingerprint || !sameResolution(state.original, { target: expected, ...value, fingerprint: value.fingerprint }))) return invalid()
    if (value && state.decision && ['operation_id', 'rename_id', 'copy_id', 'retire_id', 'restore_id'].some(key => value[key as keyof SyncDecision] !== state.decision?.[key as keyof SyncDecision])) return invalid()
    state.decision = value; state.originalChecked = true; state.originalMissing = !value
    if (value?.fingerprint) state.original = { target: expected, sequence, choice: value.choice, destination: value.destination, expected_hash: value.expected_hash, fingerprint: value.fingerprint }
    return value
  }
  function readDecision(sequence = state.original?.sequence ?? state.decision?.sequence ?? state.review?.sequence ?? 0) {
    if (!owner || !integer(sequence, 1) || state.original && sequence !== state.original.sequence) return Promise.resolve(false)
    const expected = copy(owner)
    return read({ action: 'run', request: { action: 'decision', target: expected, sequence } }, reply => {
      receiveDecision(reply, sequence, expected); return true
    })
  }
  async function readReview(sequence: number) {
    if (!owner || disposed || state.busy || state.cycling || state.stopping || !integer(sequence, 1) || state.original) return false
    const expected = copy(owner), version = epoch
    state.review = null; state.decision = null; state.original = null; state.originalChecked = false; state.originalMissing = false
    const result = await read({ action: 'run', request: { action: 'decision', target: expected, sequence } }, reply => receiveDecision(reply, sequence, expected))
    if (result !== null || !current(version)) return false
    return read({ action: 'run', request: { action: 'review', target: expected, sequence } }, reply => {
      if (reply.kind !== 'result' || reply.response.kind !== 'review') return invalid()
      state.review = review(reply.response.review, expected, sequence); return true
    })
  }
  function freeze(choice: Resolution['choice'], destination = ''): Resolution | null {
    if (!owner || disposed || state.busy || state.cycling || !state.review || state.original || !['local', 'remote', 'copy'].includes(choice)
      || choice === 'copy' && (!destination || !text(destination) || /^[\\/]/.test(destination) || destination.split(/[\\/]/).some(part => !part || part === '.' || part === '..'))) return null
    const frozen = { target: copy(owner), sequence: state.review.sequence, choice, destination: choice === 'copy' ? destination : '', expected_hash: state.review.local.hash, fingerprint: state.review.fingerprint }
    Object.freeze(frozen.target.expected); Object.freeze(frozen.target); return Object.freeze(frozen)
  }
  function send(original: Resolution) {
    if (!owner) return Promise.resolve(false)
    const expected = copy(owner), frozen = copy(original)
    state.original = frozen; state.originalChecked = false; state.originalMissing = false; state.review = null
    return read({ action: 'run', request: { action: 'resolve', ...frozen } }, reply => {
      if (!receiveDecision(reply, frozen.sequence, expected)) return invalid()
      return true
    })
  }
  function confirm(frozen: Resolution) {
    if (!owner || disposed || state.busy || state.cycling || state.original || !sameTarget(frozen.target, owner)
      || !state.review || state.review.sequence !== frozen.sequence || state.review.fingerprint !== frozen.fingerprint
      || state.review.local.hash !== frozen.expected_hash) return Promise.resolve(false)
    const expected = freeze(frozen.choice, frozen.destination)
    if (!expected || !sameResolution(expected, frozen)) return Promise.resolve(false)
    return send(frozen)
  }
  function resume(frozen: Resolution) {
    if (!owner || disposed || state.busy || state.cycling || !state.original || !state.originalChecked
      || state.decision?.state === 'completed' || !sameTarget(frozen.target, owner) || !sameResolution(frozen, state.original)
      || !state.originalMissing && state.decision?.fingerprint !== frozen.fingerprint) return Promise.resolve(false)
    return send(frozen)
  }
  function keepOriginal() {
    if (state.busy || state.cycling || state.original && !state.originalChecked) return false
    state.review = null; state.original = null; state.decision = null; state.originalChecked = false; state.originalMissing = false
    return true
  }
  function clear() { epoch++; activityRead++; stoppedInvocation = ''; Object.assign(state, blank()) }
  function dispose() { clear(); disposed = true; owner = null }
  return { state, context, readStatus, readOriginals, now, stop, readActivity, event, readReview, readDecision, freeze, confirm, resume, keepOriginal, clear, dispose }
}
