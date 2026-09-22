// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import NotificationCenter from './NotificationCenter.vue'
import { publishAppNotification } from '@/services/notificationCenter'
import { useWorkspaceStore } from '@/stores/workspace'

function createSubject() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/tasks', name: 'tasks', component: { template: '<div />' } },
      { path: '/agent/:runId?', name: 'agent', component: { template: '<div />' } },
    ],
  })
  return { router, wrapper: mount(NotificationCenter, { global: { plugins: [router] } }) }
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  useWorkspaceStore().vaultId = 'vault-a'
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('NotificationCenter', () => {
  it('isolates notifications by Vault and deduplicates a repeated run status', async () => {
    const { wrapper } = createSubject()
    publishAppNotification({ level: 'success', title: 'done', message: 'A', vault_id: 'vault-a', dedupe_key: 'same' })
    publishAppNotification({ level: 'success', title: 'done', message: 'B', vault_id: 'vault-a', dedupe_key: 'same' })
    publishAppNotification({ level: 'error', title: 'other', message: 'hidden', vault_id: 'vault-b' })
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('.app-notification')).toHaveLength(1)
    expect(wrapper.text()).toContain('B')
    expect(wrapper.text()).not.toContain('hidden')
  })

  it('limits visible items, reports the queue and auto-closes success notifications', async () => {
    const { wrapper } = createSubject()
    for (let index = 0; index < 5; index++) {
      publishAppNotification({ level: 'success', title: `done-${index}`, message: 'ok', vault_id: 'vault-a', dedupe_key: `item-${index}` })
    }
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('.app-notification')).toHaveLength(4)
    expect(wrapper.find('.notification-queue').text()).toContain('1')
    await vi.advanceTimersByTimeAsync(5001)
    expect(wrapper.findAll('.app-notification')).toHaveLength(1)
    expect(wrapper.text()).toContain('done-4')
    await vi.advanceTimersByTimeAsync(5001)
    expect(wrapper.findAll('.app-notification')).toHaveLength(0)
  })

  it('keeps failures visible and navigates notification actions', async () => {
    const { router, wrapper } = createSubject()
    publishAppNotification({
      level: 'error', title: 'failed', message: 'reason', vault_id: 'vault-a', persistent: true,
      action: { type: 'agent_run', resource_id: 'run-1' },
    })
    await wrapper.vm.$nextTick()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(wrapper.text()).toContain('failed')
    await wrapper.find('.notification-open').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('agent')
    expect(router.currentRoute.value.params.runId).toBe('run-1')
  })

  it('clears the visible queue when the active Vault changes', async () => {
    const { wrapper } = createSubject()
    publishAppNotification({ level: 'info', title: 'running', message: 'task', vault_id: 'vault-a' })
    await wrapper.vm.$nextTick()
    useWorkspaceStore().vaultId = 'vault-b'
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('.app-notification')).toHaveLength(0)
  })
})
