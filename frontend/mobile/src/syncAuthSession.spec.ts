import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSyncAuthSession } from './syncAuthSession'
import type { AuthIntent, AuthProfiles, AuthRequest, AuthResponse, AuthSelection, AuthState, NativeAuth } from './syncAuthTypes'

const operation = '00000000-0000-4000-8000-000000000001'
function selected(account = 'reader-a', id = operation, revision = 1): AuthSelection {
  return { revision, endpoint: 'https://sync.example', account, operation_id: id, generation: 1 }
}
function intent(selection = selected(), phase: AuthIntent['phase'] = 'active'): AuthIntent {
  return { ...selection, phase, kind: 'login', device_name: 'Owned phone', device_id: 'owned-device', server_logout_confirmed: false,
    previous_operation: null }
}
function current(selection = selected(), extra: Partial<AuthState> = {}): AuthResponse {
  return { kind: 'current', selection, state: { intent: intent(selection), authenticated: true, expired: false,
    credential_change_required: false, expires_at: 1000, rejected: null, ...extra } }
}
function page(selection = selected()): AuthResponse {
  const profiles: AuthProfiles = { items: [{ intent: intent(selection), rejected: null, selected: true }],
    total: 1, offset: 0, limit: 20, selection }
  return { kind: 'profiles', profiles }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => resolve = yes)
  return { promise, resolve }
}
const tick = async () => { for (let i = 0; i < 15; i++) await Promise.resolve() }

describe('mobile native authentication request ownership', () => {
  const sessions: ReturnType<typeof createSyncAuthSession>[] = []
  afterEach(async () => { await Promise.all(sessions.splice(0).map(session => session.dispose())) })
  function make(action: NativeAuth) {
    const rpc = vi.fn((request: AuthRequest): Promise<AuthResponse> => request.action === 'cancel'
      ? Promise.resolve({ kind: 'cancelled' }) : action(request))
    const session = createSyncAuthSession(rpc, () => operation)
    sessions.push(session); return { session, rpc }
  }
  function vaultPage(identity = selected()): Extract<AuthResponse, { kind: 'vaults' }> {
    return { kind: 'vaults', page: { selection: identity, offset: 0, limit: 20, total: 1,
      items: [{ id: 'remote-a', name: '原名称 😀 <script>', sequence: 3, used: 12, quota: 100 }] } }
  }
  it('reads remote vaults only explicitly with the full native selection and keeps only metadata', async () => {
    const response = vaultPage()
    Object.assign(response.page.items[0]!, { private_provider_value: 'not-for-view-state' })
    const { session, rpc } = make(async request => request.action === 'profiles' ? page()
      : request.action === 'vaults' ? response : current())
    expect(await session.vaults()).toBe(false)
    expect(rpc).not.toHaveBeenCalled()
    await session.reload()
    expect(rpc.mock.calls.some(([request]) => request.action === 'vaults')).toBe(false)
    expect(await session.vaults()).toBe(true)
    expect(rpc).toHaveBeenLastCalledWith({ action: 'vaults', expected: selected(), offset: 0, limit: 20 })
    expect(session.state.vaults?.items[0]?.name).toBe('原名称 😀 <script>')
    expect(JSON.stringify(session.state.vaults)).not.toContain('private_provider_value')
    expect(JSON.stringify(session.state.vaults)).not.toContain('not-for-view-state')
    const calls = rpc.mock.calls.length
    expect(await session.vaults(1001)).toBe(false)
    expect(rpc).toHaveBeenCalledTimes(calls)
  })
  it('refuses a vault page with a different frozen identity, unsafe fields or invalid pagination', async () => {
    for (const change of ['revision', 'size', 'duplicate', 'number', 'missing-name']) {
      const response = vaultPage()
      if (change === 'revision') response.page.selection.revision++
      if (change === 'size') response.page.total = 1001
      if (change === 'duplicate') { response.page.total = 2; response.page.items.push(response.page.items[0]!) }
      if (change === 'number') response.page.items[0]!.used = Number.MAX_SAFE_INTEGER + 1
      if (change === 'missing-name') response.page.items[0]!.name = ''
      const { session } = make(async request => request.action === 'profiles' ? page()
        : request.action === 'vaults' ? response : current())
      await session.reload()
      expect(await session.vaults()).toBe(false)
      expect(session.state.vaults).toBeNull()
      expect(session.state.error).toBe('SYNC_RESPONSE_INVALID')
    }
  })
  it('discards a late vault reply after native cancellation and an account switch', async () => {
    const waiting = deferred<AuthResponse>()
    let identity = selected()
    const { session, rpc } = make(async request => request.action === 'profiles' ? page(identity)
      : request.action === 'vaults' ? waiting.promise : current(identity))
    await session.reload()
    const pending = session.vaults(); await tick()
    await session.cancel()
    identity = selected('reader-b', '00000000-0000-4000-8000-000000000002', 2)
    await session.reload()
    waiting.resolve(vaultPage()); expect(await pending).toBe(false)
    expect(session.state.selection?.account).toBe('reader-b')
    expect(session.state.vaults).toBeNull()
    expect(rpc.mock.calls.filter(([request]) => request.action === 'vaults')).toHaveLength(1)
  })
  it('clears an old vault page even when only the current native selection revision changes', async () => {
    let identity = selected()
    const { session } = make(async request => request.action === 'profiles' ? page(identity)
      : request.action === 'vaults' ? vaultPage(identity) : current(identity))
    await session.reload(); await session.vaults()
    expect(session.state.vaults?.total).toBe(1)
    identity = { ...identity, revision: 2 }
    await session.reload()
    expect(session.state.vaults).toBeNull()
  })
  it('retains only original metadata after a lost login and checks without another POST or a password copy', async () => {
    const { session, rpc } = make(async request => {
      if (request.action === 'login') throw new Error('SYNC_NETWORK_ERROR')
      if (request.action === 'operation') return { kind: 'operation', state: {
        intent: intent(selected(), 'unknown'), authenticated: false, expired: false,
        credential_change_required: false, expires_at: null, rejected: null } }
      throw new Error('Unexpected command')
    })
    expect(await session.login('https://sync.example', 'reader-a', 'Owned phone', 'private-original-password')).toBe(false)
    expect(session.state.pending?.operation_id).toBe(operation)
    expect(JSON.stringify(session.state)).not.toContain('private-original-password')
    expect(await session.login('https://sync.example', 'reader-a', 'Owned phone', 'private-original-password')).toBe(false)
    expect(await session.checkOriginal()).toBe(true)
    expect(rpc.mock.calls.map(([r]) => r.action)).toEqual(['login', 'operation'])
    expect(session.state.current).toBeNull()
    expect(session.state.receipt?.authenticated).toBe(false)
    expect(session.acknowledgeOriginal()).toBe(true)
    expect(session.state.pending).toBeNull()
  })
  it('clears a pending view immediately and ignores its late successful login after native cancellation', async () => {
    const login = deferred<AuthResponse>()
    const { session, rpc } = make(() => login.promise)
    const pending = session.login('https://sync.example', 'reader-a', 'Owned phone', 'private-original-password')
    await tick(); expect(session.state.busy).toBe(true)
    await session.cancel()
    login.resolve(current())
    expect(await pending).toBe(false)
    expect(session.state.current).toBeNull(); expect(session.state.pending).toBeNull()
    expect(session.state.error).toBe(''); expect(session.state.busy).toBe(false)
    expect(rpc.mock.calls.map(([r]) => r.action)).toEqual(['login', 'cancel'])
  })
  it('cannot overwrite the newly loaded account with a prior current response or its pagination', async () => {
    const old = deferred<AuthResponse>(); let reads = 0
    const next = selected('reader-b', '00000000-0000-4000-8000-000000000002', 2)
    const { session, rpc } = make(async request => {
      if (request.action === 'current') return ++reads === 1 ? old.promise : current(next)
      return page(next)
    })
    const first = session.reload(); await tick(); await session.cancel()
    expect(await session.reload()).toBe(true)
    old.resolve(current()); expect(await first).toBe(false)
    expect(session.state.current?.intent?.account).toBe('reader-b')
    expect(session.state.profiles?.items[0]?.intent.account).toBe('reader-b')
    expect(rpc.mock.calls.filter(([r]) => r.action === 'profiles')).toHaveLength(1)
  })
  it('freezes the profile generation and original choice, and never treats a choice receipt as authenticated', async () => {
    const { session, rpc } = make(async request => {
      if (request.action === 'current') return current()
      if (request.action === 'profiles') return page()
      if (request.action === 'choose') return { kind: 'choice', choice: { operation_id: request.operation_id,
        expected_revision: request.expected_revision, selection: selected() } }
      throw new Error('Unexpected command')
    })
    await session.reload()
    const profile = session.state.profiles!.items[0]!, frozen = session.freezeProfile(profile)
    expect(Object.isFrozen(frozen)).toBe(true)
    expect(frozen.expected_revision).toBe(1)
    profile.intent.generation = 2
    expect(await session.choose(profile, frozen)).toBe(false)
    profile.intent.generation = 1
    expect(await session.choose(profile, frozen)).toBe(true)
    expect(rpc.mock.calls.filter(([r]) => r.action === 'choose')).toHaveLength(1)
    expect(session.state.choice?.operation_id).toBe(frozen.operation_id)
    expect(session.state.current).toBeNull()
  })
  it('keeps the setup requirement explicit and refuses an authenticated reply for a different selected identity', async () => {
    let wrong = false
    const { session } = make(async request => {
      if (request.action === 'profiles') return page()
      const reply = current(selected(), { credential_change_required: true })
      if (reply.kind === 'current' && wrong) reply.state.intent = intent(selected('other-account'))
      return reply
    })
    expect(await session.reload()).toBe(true)
    expect(session.state.current?.credential_change_required).toBe(true)
    await session.cancel(); wrong = true
    expect(await session.reload()).toBe(false)
    expect(session.state.current).toBeNull(); expect(session.state.error).toBe('SYNC_RESPONSE_INVALID')
  })
  it('allows explicit acknowledgement of an absent original receipt without exposing the raw provider error', async () => {
    const { session } = make(async request => {
      if (request.action === 'operation') throw 'SYNC_AUTH_INTENT_NOT_FOUND'
      throw 'private-provider-message-with-secret'
    })
    await session.login('https://sync.example', 'reader-a', 'Owned phone', 'private-original-password')
    expect(session.state.error).toBe('SYNC_CONNECTION_FAILED')
    expect(session.acknowledgeOriginal()).toBe(false)
    await session.checkOriginal()
    expect(session.state.originalMissing).toBe(true)
    expect(session.acknowledgeOriginal()).toBe(true)
    expect(JSON.stringify(session.state)).not.toContain('private-provider')
  })
  it('waits for the previous view native cancellation before a new view may read or send', async () => {
    const cancellation = deferred<AuthResponse>(), calls: string[] = []
    const first = createSyncAuthSession(async request => {
      calls.push('first:' + request.action)
      return request.action === 'cancel' ? cancellation.promise : current()
    })
    const disposing = first.dispose()
    const { session } = make(async request => {
      calls.push('second:' + request.action)
      return request.action === 'profiles' ? page() : current()
    })
    const next = session.reload(); await tick()
    expect(calls).toEqual(['first:cancel'])
    cancellation.resolve({ kind: 'cancelled' }); await disposing
    expect(await next).toBe(true)
    expect(calls).toEqual(['first:cancel', 'second:current', 'second:profiles'])
  })
  it('refuses a frozen sign-out confirmation after the native selected account changes', async () => {
    let identity = selected()
    const { session, rpc } = make(async request => request.action === 'profiles' ? page(identity) : current(identity))
    await session.reload()
    const frozen = { ...session.state.selection! }
    identity = selected('reader-b', '00000000-0000-4000-8000-000000000002', 2)
    await session.reload()
    expect(await session.existing('logout', frozen)).toBe(false)
    expect(session.state.error).toBe('SYNC_AUTH_SELECTION_CHANGED')
    expect(rpc.mock.calls.some(([request]) => request.action === 'logout')).toBe(false)
    expect(session.state.current?.intent?.account).toBe('reader-b')
  })
  it('rejects an invalid password before creating an intent or sending sensitive IPC', async () => {
    const { session, rpc } = make(async () => current())
    for (const value of ['short', '私'.repeat(257), 'x'.repeat(1025)]) {
      expect(await session.login('https://sync.example', 'reader-a', 'Owned phone', value)).toBe(false)
      expect(session.state.error).toBe('SYNC_LOGIN_INVALID')
      expect(session.state.pending).toBeNull()
    }
    expect(rpc).not.toHaveBeenCalled()
    expect(JSON.stringify(session.state)).not.toContain('short')
  })
})
