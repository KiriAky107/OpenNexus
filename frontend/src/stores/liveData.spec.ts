// @vitest-environment happy-dom
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAgentStore } from './agent'
import { useChatStore } from './chat'
import { useTaskStore } from './task'
import { usePluginStore } from './plugin'
import { useSkillStore } from './skill'
import { useProviderStore } from './provider'
import { useSettingsStore } from './settings'
import { listProviders } from '@/services/providerService'
import { getStatus } from '@/services/systemService'

beforeEach(() => { setActivePinia(createPinia()); localStorage.clear() })
afterEach(() => vi.unstubAllGlobals())

describe('runtime data sources', () => {
  it('starts with no fabricated domain records or healthy diagnostics', () => {
    expect(useAgentStore().runs).toEqual([])
    expect(useAgentStore().events).toEqual([])
    expect(useAgentStore().tools).toEqual([])
    expect(useAgentStore().permissionRequest).toBeNull()
    expect(useChatStore().conversations).toEqual([])
    expect(useChatStore().messages).toEqual([])
    expect(useTaskStore().tasks).toEqual([])
    expect(usePluginStore().plugins).toEqual([])
    expect(useSkillStore().skills).toEqual([])
    expect(useProviderStore().providers).toEqual([])
    expect(useProviderStore().defaultProviderId).toBe('')
    expect(useSettingsStore().aiCoreStatus).toBe('unknown')
    expect(useSettingsStore().indexStatus.total_notes).toBeNull()
    expect(useSettingsStore().permissionPolicy).toEqual({})
  })

  it('keeps initial collections empty and exposes errors when the API is offline', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    const stores = [useTaskStore(), usePluginStore(), useSkillStore(), useProviderStore()] as const
    await Promise.all([stores[0].loadTasks(), stores[1].loadPlugins(), stores[2].loadSkills(), stores[3].loadProviders()])
    expect(stores.every(store => store.error)).toBe(true)
    await useSettingsStore().loadDiagnostics()
    expect(useSettingsStore().aiCoreStatus).toBe('error')
    expect(useSettingsStore().indexStatus.total_blocks).toBeNull()
    expect(useSettingsStore().diagnosticsError).toBeTruthy()
    await expect(getStatus()).rejects.toThrow()
  })

  it('renders backend counts and effective permissions and excludes the test provider', async () => {
    const data: Record<string, unknown> = {
      '/health': { status: 'ok' }, '/api/status': { version: '9.2.1' },
      '/api/index/status': { status: 'idle', pending_jobs: 0, total_notes: 7, total_blocks: 19 },
      '/api/permissions/policy': { 'attachments.read': 'allow' },
      '/api/providers': { items: [
        { provider_id: 'mock', provider_type: 'mock', capabilities: [] },
        { provider_id: 'real', name: 'Real', provider_type: 'ollama', capabilities: [], enabled: true, default_model: 'installed-model' },
      ] },
    }
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(data[url]), { status: 200, headers: { "content-type": "application/json" } })))
    expect((await listProviders()).map(p => p.provider_id)).toEqual(['real'])
    await useProviderStore().loadProviders()
    expect(useProviderStore().defaultProviderId).toBe('real')
    await useSettingsStore().loadDiagnostics()
    expect(useSettingsStore().indexStatus.total_notes).toBe(7)
    expect(useSettingsStore().indexStatus.total_blocks).toBe(19)
    expect(useSettingsStore().aiCoreVersion).toBe('9.2.1')
    expect(useSettingsStore().permissionPolicy).toEqual({ 'attachments.read': 'allow' })
  })
})
