import { reactive } from 'vue'
import type { AuthChoice, AuthIntent, AuthProfile, AuthProfiles, AuthRequest, AuthResponse, AuthSelection, AuthState, NativeAuth } from './syncAuthTypes'

type ChoiceRequest = Extract<AuthRequest, { action: 'choose' }>
interface Original {
  kind: AuthIntent['kind'] | 'choose'; operation_id: string; endpoint: string; account: string
  choice?: ChoiceRequest
}
interface Model {
  busy: boolean; error: string; current: AuthState | null; selection: AuthSelection | null
  profiles: AuthProfiles | null; pending: Original | null; receipt: AuthState | null; choice: AuthChoice | null
  originalMissing: boolean
}
const codes = new Set(['SYNC_AUTH_IDENTITY_CHANGED', 'SYNC_AUTH_INDEX_INVALID', 'SYNC_AUTH_INTENT_INVALID',
  'SYNC_AUTH_INTENT_MISMATCH', 'SYNC_AUTH_INTENT_NOT_FOUND', 'SYNC_AUTH_PROFILE_PAGE_INVALID', 'SYNC_AUTH_RECONCILIATION_REQUIRED',
  'SYNC_AUTH_RESPONSE_MISMATCH', 'SYNC_AUTH_SELECTION_CHANGED', 'SYNC_AUTH_SELECTION_INVALID', 'SYNC_CREDENTIAL_CHANGE_REQUIRED',
  'SYNC_CREDENTIALS_INVALID', 'SYNC_CREDENTIALS_UNAVAILABLE', 'SYNC_ENDPOINT_INVALID', 'SYNC_FOREGROUND_CHANGED',
  'SYNC_FOREGROUND_REQUIRED', 'SYNC_LOGIN_INVALID', 'SYNC_LOGIN_REQUIRED', 'SYNC_NETWORK_ERROR', 'SYNC_REDIRECT_BLOCKED',
  'SYNC_RESPONSE_INVALID', 'SYNC_RESPONSE_TOO_LARGE', 'SYNC_SESSION_EXPIRED', 'SYNC_SESSION_INVALID', 'SYNC_TIME_INVALID',
  'SYNC_TLS_UNAVAILABLE', 'SYNC_PLATFORM_UNSUPPORTED', 'SYNC_CONNECTION_FAILED', 'LOGIN_FAILED', 'RATE_LIMITED',
  'INVALID_REQUEST', 'SESSION_EXPIRED', 'MOBILE_HOST_REQUIRED', 'MOBILE_WINDOW_DENIED'])
function safeError(error: unknown): string {
  const code = typeof error === 'string' ? error : error instanceof Error ? error.message : ''
  return codes.has(code) ? code : 'SYNC_CONNECTION_FAILED'
}
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const blank = (): Model => ({ busy: false, error: '', current: null, selection: null, profiles: null,
  pending: null, receipt: null, choice: null, originalMissing: false })

// Only a metadata-only cancellation promise survives a settings component.
// A freshly mounted view must wait until its predecessor's cancellation has
// reached the native gate. No identity, password or session is stored here.
let cancelledBeforeNextView: Promise<unknown> = Promise.resolve()

export function createSyncAuthSession(native: NativeAuth, uuid: () => string = () => crypto.randomUUID()) {
  const state = reactive<Model>(blank())
  let epoch = 0, disposed = false
  const live = (version: number) => !disposed && version === epoch
  function requireIdle() { return !disposed && !state.busy }
  function matches(selection: AuthSelection | null, intent: AuthIntent | null): boolean {
    return Boolean(selection && intent && selection.endpoint === intent.endpoint && selection.account === intent.account
      && selection.operation_id === intent.operation_id && selection.generation === intent.generation)
  }
  const sameSelection = (a: AuthSelection, b: AuthSelection) => a.revision === b.revision
    && a.endpoint === b.endpoint && a.account === b.account && a.operation_id === b.operation_id && a.generation === b.generation
  function current(reply: Extract<AuthResponse, { kind: 'current' }>) {
    if (reply.state.authenticated && (!matches(reply.selection, reply.state.intent) || reply.state.intent?.phase !== 'active'
      || reply.state.rejected || reply.state.expired)) throw new Error('SYNC_RESPONSE_INVALID')
    state.current = copy(reply.state); state.selection = copy(reply.selection)
  }
  async function perform(request: AuthRequest, accept: (reply: AuthResponse) => void, mutation?: Original): Promise<boolean> {
    if (!requireIdle()) return false
    const version = ++epoch; state.busy = true; state.error = ''
    const action = request.action
    if (mutation) {
      state.pending = copy(mutation); state.receipt = null; state.choice = null; state.originalMissing = false
      state.current = null; state.profiles = null
    }
    try {
      await cancelledBeforeNextView
      if (!live(version)) return false
      // The component clears its password input before this call. The request
      // is never copied into state, a receipt, diagnostics or WebView storage.
      const result = native(request)
      request = { action: 'current' }
      const reply = await result
      if (!live(version)) return false
      accept(reply)
      return true
    } catch (error) {
      if (live(version)) {
        state.error = safeError(error)
        if (action === 'operation' && state.error === 'SYNC_AUTH_INTENT_NOT_FOUND') state.originalMissing = true
      }
      return false
    } finally { if (live(version)) state.busy = false }
  }
  async function reload() {
    if (!(await perform({ action: 'current' }, reply => {
      if (reply.kind !== 'current') throw new Error('SYNC_RESPONSE_INVALID')
      current(reply)
    }))) return false
    return profiles(0)
  }
  function profiles(offset: number) {
    return perform({ action: 'profiles', offset, limit: 20 }, reply => {
      if (reply.kind !== 'profiles' || reply.profiles.offset !== offset || reply.profiles.limit !== 20
        || reply.profiles.items.length > 20) throw new Error('SYNC_RESPONSE_INVALID')
      state.profiles = copy(reply.profiles)
    })
  }
  function mutationReply(reply: AuthResponse) {
    const original = state.pending
    if (!original) throw new Error('SYNC_RESPONSE_INVALID')
    if (reply.kind === 'operation') {
      if (reply.state.intent?.operation_id !== original.operation_id) throw new Error('SYNC_RESPONSE_INVALID')
      // A historical receipt never grants a current connection in the UI.
      state.receipt = copy({ ...reply.state, authenticated: false })
    } else if (reply.kind === 'current') {
      if (reply.state.intent?.operation_id !== original.operation_id) throw new Error('SYNC_RESPONSE_INVALID')
      current(reply); state.receipt = copy(reply.state); state.pending = null
    } else throw new Error('SYNC_RESPONSE_INVALID')
  }
  function login(endpoint: string, account: string, deviceName: string, password: string) {
    if (!requireIdle() || state.pending) return Promise.resolve(false)
    const characters = password.length <= 1024 ? [...password].length : 0
    if (characters < 12 || characters > 256
      || new TextEncoder().encode(password).byteLength > 1024) {
      state.error = 'SYNC_LOGIN_INVALID'; return Promise.resolve(false)
    }
    const original: Original = { kind: 'login', operation_id: uuid(), endpoint, account }
    return perform({ action: 'login', operation_id: original.operation_id, endpoint, account, device_name: deviceName,
      password, expected: copy(state.selection) }, mutationReply, original)
  }
  function existing(action: 'refresh' | 'logout' | 'disconnect', frozen?: AuthSelection) {
    const selected = state.selection, previous = state.current?.intent
    if (!requireIdle() || state.pending || !selected || !previous || !matches(selected, previous)) return Promise.resolve(false)
    if (frozen && !sameSelection(frozen, selected)) { state.error = 'SYNC_AUTH_SELECTION_CHANGED'; return Promise.resolve(false) }
    const original: Original = { kind: action === 'disconnect' ? 'local_disconnect' : action,
      operation_id: uuid(), endpoint: selected.endpoint, account: selected.account }
    return perform({ action, operation_id: original.operation_id, previous_operation: previous.operation_id,
      endpoint: selected.endpoint, account: selected.account, expected: copy(selected) }, mutationReply, original)
  }
  function freezeProfile(profile: AuthProfile): ChoiceRequest {
    return Object.freeze({ action: 'choose', operation_id: uuid(), target_operation: profile.intent.operation_id,
      generation: profile.intent.generation, expected_revision: state.selection?.revision ?? null })
  }
  function choose(profile: AuthProfile, frozen: ChoiceRequest) {
    if (!requireIdle() || state.pending || frozen.target_operation !== profile.intent.operation_id
      || frozen.generation !== profile.intent.generation) return Promise.resolve(false)
    const original: Original = { kind: 'choose', operation_id: frozen.operation_id,
      endpoint: profile.intent.endpoint, account: profile.intent.account, choice: copy(frozen) }
    return perform(copy(frozen), reply => {
      if (reply.kind !== 'choice' || reply.choice?.operation_id !== frozen.operation_id) throw new Error('SYNC_RESPONSE_INVALID')
      state.choice = copy(reply.choice); state.pending = null
      // Re-read Current explicitly; even an old successful choice receipt must
      // not activate the account it used to select.
    }, original)
  }
  function checkOriginal() {
    const original = state.pending
    if (!original || !requireIdle()) return Promise.resolve(false)
    const request: AuthRequest = original.kind === 'choose' ? { action: 'choice_receipt', operation_id: original.operation_id }
      : { action: 'operation', operation_id: original.operation_id, endpoint: original.endpoint, account: original.account }
    return perform(request, reply => {
      if (original.kind === 'choose') {
        if (reply.kind !== 'choice' || reply.choice && reply.choice.operation_id !== original.operation_id) throw new Error('SYNC_RESPONSE_INVALID')
        state.choice = copy(reply.choice); state.originalMissing = reply.choice === null
      } else {
        if (reply.kind !== 'operation' || reply.state.intent?.operation_id !== original.operation_id) throw new Error('SYNC_RESPONSE_INVALID')
        state.receipt = copy({ ...reply.state, authenticated: false }); state.originalMissing = false
      }
    })
  }
  function resumeErasure(frozen?: AuthSelection) {
    const selection = state.selection, intent = state.current?.intent
    if (!requireIdle() || !selection || !intent || !matches(selection, intent) || intent.phase !== 'erase_pending'
      || !(intent.kind === 'local_disconnect' || intent.kind === 'logout' && intent.server_logout_confirmed)) return Promise.resolve(false)
    if (frozen && !sameSelection(frozen, selection)) { state.error = 'SYNC_AUTH_SELECTION_CHANGED'; return Promise.resolve(false) }
    return perform({ action: 'resume_erasure', operation_id: intent.operation_id, endpoint: selection.endpoint,
      account: selection.account, expected: copy(selection) }, reply => {
      if (reply.kind !== 'current') throw new Error('SYNC_RESPONSE_INVALID')
      current(reply); state.pending = null; state.receipt = copy(reply.state)
    })
  }
  function acknowledgeOriginal() {
    if (!requireIdle() || !(state.receipt || state.choice || state.originalMissing)) return false
    state.pending = null; return true
  }
  function cancel() {
    if (disposed) return Promise.resolve()
    const version = ++epoch
    Object.assign(state, blank(), { busy: true })
    const previous = cancelledBeforeNextView
    const next = previous.then(() => native({ action: 'cancel' })).catch(() => undefined)
    cancelledBeforeNextView = next
    return next.then(() => { if (live(version)) state.busy = false })
  }
  function dispose() {
    const finishing = cancel(); disposed = true; epoch++; Object.assign(state, blank()); return finishing
  }
  return { state, reload, profiles, login, existing, freezeProfile, choose, checkOriginal, resumeErasure,
    acknowledgeOriginal, cancel, dispose }
}
