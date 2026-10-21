// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia } from 'pinia'
import { useSettingsStore } from './settings'

const api = vi.hoisted(() => ({ health: vi.fn(), status: vi.fn(), index: vi.fn(), policy: vi.fn() }))
vi.mock('@/services/systemService', () => ({ healthCheck: api.health, getStatus: api.status, getPermissionPolicy: api.policy }))
vi.mock('@/services/indexService', () => ({ getIndexStatus: api.index, rebuildIndex: vi.fn() }))

function deferred() {
  let resolve!: (value: unknown) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<unknown>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

function batch() {
  const health = deferred(), status = deferred(), index = deferred(), policy = deferred()
  api.health.mockReturnValueOnce(health.promise)
  api.status.mockReturnValueOnce(status.promise)
  api.index.mockReturnValueOnce(index.promise)
  api.policy.mockReturnValueOnce(policy.promise)
  return {
    healthy(version: string, notes: number) {
      health.resolve({ status: 'ok' }); status.resolve({ version })
      index.resolve({ status: 'idle', pending_jobs: 0, total_notes: notes, total_blocks: notes * 2 })
      policy.resolve({ 'attachments.read': 'allow' })
    },
    offline() { for (const item of [health, status, index, policy]) item.reject(new Error('original offline request')) },
  }
}

beforeEach(() => { localStorage.clear(); vi.clearAllMocks() })

it('keeps a recovered connection when an earlier startup check fails late', async () => {
  const store = useSettingsStore(createPinia())
  const initial = batch(), recovered = batch()
  const oldRequest = store.loadDiagnostics(), currentRequest = store.loadDiagnostics()
  recovered.healthy('current-core', 17); await currentRequest
  initial.offline(); await oldRequest
  expect(store.aiCoreStatus).toBe('running')
  expect(store.aiCoreVersion).toBe('current-core')
  expect(store.indexStatus.total_notes).toBe(17)
  expect(store.permissionPolicy).toEqual({ 'attachments.read': 'allow' })
  expect(store.diagnosticsError).toBeNull()
})

it('keeps the latest offline result when an earlier healthy response arrives late', async () => {
  const store = useSettingsStore(createPinia())
  const initial = batch(), current = batch()
  const oldRequest = store.loadDiagnostics(), currentRequest = store.loadDiagnostics()
  current.offline(); await currentRequest
  initial.healthy('outdated-core', 99); await oldRequest
  expect(store.aiCoreStatus).toBe('error')
  expect(store.aiCoreVersion).toBe('—')
  expect(store.indexStatus.status).toBe('unknown')
  expect(store.indexStatus.total_notes).toBeNull()
  expect(store.permissionPolicy).toEqual({})
  expect(store.diagnosticsError).toContain('original offline request')
})

it('applies a single current check normally', async () => {
  const store = useSettingsStore(createPinia()), current = batch()
  const request = store.loadDiagnostics()
  current.healthy('only-core', 3); await request
  expect(store.aiCoreStatus).toBe('running')
  expect(store.aiCoreVersion).toBe('only-core')
  expect(store.indexStatus.total_notes).toBe(3)
  expect(store.diagnosticsError).toBeNull()
})
