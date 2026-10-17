import { reactive } from 'vue'
import type { AuthSelection } from './syncAuthTypes'
import type { AssociationRequest, AssociationResponse, BindingIntent, BindingPage, BindingPreview, BindingRequest, BindingStatus, MobileBinding, NativeAssociation } from './syncAssociationTypes'

const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const uuid = (value: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
const digest = (value: string) => /^[0-9a-f]{64}$/.test(value)
const identifier = (value: string) => /^[A-Za-z0-9-]{1,80}$/.test(value)
const integer = (value: number, minimum = 0) => Number.isSafeInteger(value) && value >= minimum
const sameSelection = (a: AuthSelection, b: AuthSelection) => a.revision === b.revision && a.endpoint === b.endpoint
  && a.account === b.account && a.operation_id === b.operation_id && a.generation === b.generation
const sameRequest = (a: BindingRequest, b: BindingRequest) => a.operation_id === b.operation_id && a.local_vault === b.local_vault
  && a.remote_vault === b.remote_vault && a.preview_fingerprint === b.preview_fingerprint && sameSelection(a.selection, b.selection)
const invalid = () => { throw new Error('SYNC_RESPONSE_INVALID') }
function selected(value: AuthSelection): AuthSelection {
  if (!integer(value.revision, 1) || !integer(value.generation, 1) || !uuid(value.operation_id)
    || typeof value.endpoint !== 'string' || typeof value.account !== 'string') return invalid()
  return { revision: value.revision, endpoint: value.endpoint, account: value.account, operation_id: value.operation_id, generation: value.generation }
}
function request(value: BindingRequest, scope: AuthSelection, local: string): BindingRequest {
  const selection = selected(value.selection)
  if (!uuid(value.operation_id) || value.local_vault !== local || !identifier(value.remote_vault)
    || !digest(value.preview_fingerprint) || selection.endpoint !== scope.endpoint || selection.account !== scope.account) return invalid()
  return { operation_id: value.operation_id, local_vault: local, remote_vault: value.remote_vault, preview_fingerprint: value.preview_fingerprint, selection }
}
function binding(value: MobileBinding, selection: AuthSelection): MobileBinding {
  if (!uuid(value.id) || !identifier(value.remote_vault) || !integer(value.cursor)
    || value.endpoint !== selection.endpoint || value.account !== selection.account) return invalid()
  return { id: value.id, endpoint: value.endpoint, account: value.account, remote_vault: value.remote_vault, cursor: value.cursor }
}
function intent(value: BindingIntent, scope: AuthSelection, local: string): BindingIntent {
  const original = request(value.request, scope, local)
  if (!integer(value.attempt_revision, 1) || !['prepared', 'bound'].includes(value.phase)) return invalid()
  const result = value.binding ? binding(value.binding, original.selection) : null
  if (value.phase === 'prepared' && result || value.phase === 'bound' && (!result || result.remote_vault !== original.remote_vault)) return invalid()
  return { request: original, phase: value.phase, attempt_revision: value.attempt_revision, binding: result }
}
const safeCodes = new Set(['SYNC_RESPONSE_INVALID', 'SYNC_NETWORK_ERROR', 'SYNC_CONNECTION_FAILED', 'SYNC_AUTH_SELECTION_CHANGED',
  'SYNC_AUTH_IDENTITY_CHANGED', 'SYNC_AUTH_RECONCILIATION_REQUIRED', 'SYNC_SESSION_EXPIRED', 'SYNC_LOGIN_REQUIRED',
  'SYNC_FOREGROUND_CHANGED', 'SYNC_FOREGROUND_REQUIRED', 'SYNC_ALREADY_BOUND', 'SYNC_PREVIEW_CHANGED', 'SYNC_PREVIEW_PAGE_INVALID',
  'SYNC_BINDING_REQUEST_INVALID', 'SYNC_BINDING_PAGE_INVALID', 'SYNC_BINDING_INTENT_INVALID', 'SYNC_BINDING_INTENT_NOT_FOUND',
  'SYNC_BINDING_INTENT_CHANGED', 'SYNC_OPERATION_MISMATCH', 'SYNC_WORKER_INTERRUPTED', 'VAULT_CHANGED', 'HOST_BUSY',
  'MOBILE_STORAGE_UNAVAILABLE', 'MOBILE_HOST_REQUIRED', 'SYNC_PLATFORM_UNSUPPORTED', 'QUOTA_EXCEEDED', 'FILESYSTEM_ERROR', 'DATABASE_ERROR'])
interface Model {
  busy: boolean; error: string; status: BindingStatus | null; preview: BindingPreview | null; pending: BindingPage | null
  originalId: string; original: BindingRequest | null; receipt: BindingIntent | null; originalChecked: boolean; originalMissing: boolean
}
const blank = (): Model => ({ busy: false, error: '', status: null, preview: null, pending: null, originalId: '', original: null, receipt: null, originalChecked: false, originalMissing: false })

export function createAssociationSession(native: NativeAssociation, newId: () => string = () => crypto.randomUUID()) {
  const state = reactive<Model>(blank())
  let selection: AuthSelection | null = null, remote = '', epoch = 0, disposed = false
  function context(next: AuthSelection, nextRemote: string) {
    if (selection && sameSelection(selection, next) && remote === nextRemote) return
    const changedIdentity = !selection || !sameSelection(selection, next)
    selection = selected(next); remote = nextRemote; epoch++
    if (changedIdentity) Object.assign(state, blank())
    else { state.busy = false; state.preview = null; state.error = '' }
  }
  async function perform(command: AssociationRequest, accept: (reply: AssociationResponse) => void): Promise<boolean> {
    if (disposed || state.busy || !selection) return false
    const version = ++epoch; state.busy = true; state.error = ''
    try {
      const reply = await native(copy(command))
      if (disposed || version !== epoch) return false
      accept(reply); return true
    } catch (error) {
      if (!disposed && version === epoch) {
        const code = error instanceof Error ? error.message : typeof error === 'string' ? error : ''
        state.error = safeCodes.has(code) ? code : 'SYNC_CONNECTION_FAILED'
        if (code === 'SYNC_PREVIEW_CHANGED') state.preview = null
      }
      return false
    } finally { if (!disposed && version === epoch) state.busy = false }
  }
  function status() {
    if (!selection) return Promise.resolve(false)
    const expected = copy(selection)
    return perform({ action: 'status', expected }, reply => {
      if (reply.kind !== 'status' || !uuid(reply.status.vault_id) || typeof reply.status.another_connection !== 'boolean') return invalid()
      const result = reply.status.binding ? binding(reply.status.binding, expected) : null
      if (reply.status.another_connection && result || reply.status.original_operation_id !== null && (!result || !uuid(reply.status.original_operation_id))) return invalid()
      state.status = { vault_id: reply.status.vault_id, binding: result, original_operation_id: reply.status.original_operation_id, another_connection: reply.status.another_connection }
    })
  }
  function preview(offset = 0) {
    if (!selection || !remote || !state.status || state.status.binding || state.status.another_connection || state.originalId) return Promise.resolve(false)
    const expected = copy(selection), local = state.status.vault_id, target = remote, fingerprint = offset ? state.preview?.fingerprint : null
    return perform({ action: 'preview', expected, local_vault: local, remote_vault: target, offset, limit: 100 }, reply => {
      if (reply.kind !== 'preview') return invalid()
      const page = reply.page
      if (!sameSelection(selected(page.selection), expected) || page.local_vault !== local || page.remote_vault !== target
        || !digest(page.fingerprint) || !integer(page.boundary) || !integer(page.total) || page.offset !== offset || page.limit !== 100
        || page.items.length !== Math.min(100, Math.max(0, page.total - offset))
        || page.items.some(item => typeof item.path !== 'string' || !['upload', 'download', 'identical', 'conflict'].includes(item.action))) return invalid()
      if (fingerprint && page.fingerprint !== fingerprint) throw new Error('SYNC_PREVIEW_CHANGED')
      state.preview = { selection: selected(page.selection), local_vault: local, remote_vault: target, fingerprint: page.fingerprint,
        boundary: page.boundary, total: page.total, offset, limit: 100, items: page.items.map(item => ({ path: item.path, action: item.action })) }
    })
  }
  function freeze(): BindingRequest | null {
    const page = state.preview
    if (disposed || state.busy || state.originalId || !selection || !page || !sameSelection(page.selection, selection) || page.remote_vault !== remote) return null
    return Object.freeze({ operation_id: newId(), local_vault: page.local_vault, remote_vault: page.remote_vault,
      preview_fingerprint: page.fingerprint, selection: Object.freeze(copy(selection)) })
  }
  function receive(reply: AssociationResponse, id: string, original?: BindingRequest, requireFound = false) {
    if (reply.kind !== 'operation' || !state.status || !selection) return invalid()
    if (!reply.intent) {
      if (requireFound) return invalid()
      state.receipt = null; state.originalChecked = true; state.originalMissing = true; return
    }
    const value = intent(reply.intent, selection, state.status.vault_id)
    if (value.request.operation_id !== id || original && !sameRequest(value.request, original)) return invalid()
    state.receipt = value; state.original = copy(value.request); state.originalChecked = true; state.originalMissing = false
  }
  function confirm(frozen: BindingRequest) {
    if (!selection || !state.preview || state.originalId || !sameSelection(frozen.selection, selection)
      || frozen.preview_fingerprint !== state.preview.fingerprint || frozen.remote_vault !== remote || frozen.local_vault !== state.status?.vault_id
      || state.status.binding || state.status.another_connection || state.busy) return Promise.resolve(false)
    state.originalId = frozen.operation_id; state.original = copy(frozen); state.originalChecked = false; state.originalMissing = false; state.receipt = null
    return perform({ action: 'bind', request: copy(frozen) }, reply => receive(reply, frozen.operation_id, frozen, true))
  }
  function readOriginal(id = state.originalId) {
    if (!selection || !state.status || !uuid(id) || state.busy) return Promise.resolve(false)
    state.originalId = id
    const expected = copy(selection), original = state.original?.operation_id === id ? copy(state.original) : undefined
    return perform({ action: 'operation', expected, operation_id: id }, reply => receive(reply, id, original))
  }
  function resume(frozen: BindingIntent) {
    if (!selection || state.busy || state.status?.binding || state.status?.another_connection || frozen.phase !== 'prepared'
      || !state.originalChecked || !state.receipt || !sameRequest(frozen.request, state.receipt.request)
      || frozen.attempt_revision !== state.receipt.attempt_revision || !sameSelection(frozen.request.selection, selection)) return Promise.resolve(false)
    const original = copy(frozen.request); state.originalId = original.operation_id; state.original = original; state.originalChecked = false
    return perform({ action: 'resume', request: original, observed_revision: frozen.attempt_revision }, reply => receive(reply, original.operation_id, original, true))
  }
  function retryMissing(frozen: BindingRequest) {
    if (!selection || state.busy || !state.originalChecked || !state.originalMissing || !state.original
      || !sameRequest(frozen, state.original) || !sameSelection(frozen.selection, selection)
      || state.status?.binding || state.status?.another_connection) return Promise.resolve(false)
    state.originalChecked = false
    return perform({ action: 'bind', request: copy(frozen) }, reply => receive(reply, frozen.operation_id, frozen, true))
  }
  function pending(after: string | null = null) {
    if (!selection || !state.status) return Promise.resolve(false)
    const expected = copy(selection), local = state.status.vault_id
    return perform({ action: 'pending', expected, after, limit: 20 }, reply => {
      if (reply.kind !== 'pending' || reply.page.items.length > 20 || reply.page.next_after !== null && !uuid(reply.page.next_after)) return invalid()
      const items = reply.page.items.map(value => intent(value, expected, local))
      const ids = items.map(value => value.request.operation_id)
      if (items.some(value => value.phase !== 'prepared') || new Set(ids).size !== ids.length
        || ids.some((id, index) => id <= (index ? ids[index - 1]! : after ?? ''))
        || reply.page.next_after !== null && (items.length !== 20 || reply.page.next_after !== ids.at(-1))) return invalid()
      state.pending = { items, next_after: reply.page.next_after }
    })
  }
  function keepOriginal() {
    if (!state.originalChecked || state.busy) return false
    state.originalId = ''; state.original = null; state.receipt = null; state.originalMissing = false; state.originalChecked = false; state.preview = null
    return true
  }
  function clear() { epoch++; Object.assign(state, blank()) }
  function dispose() { disposed = true; clear() }
  return { state, context, status, preview, freeze, confirm, readOriginal, resume, retryMissing, pending, keepOriginal, clear, dispose }
}
