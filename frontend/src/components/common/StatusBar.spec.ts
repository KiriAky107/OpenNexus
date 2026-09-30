// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkspaceStore } from '@/stores/workspace'
import StatusBar from './StatusBar.vue'

vi.mock('vue-router', () => ({ useRoute: () => ({ name: 'workspace' }) }))
vi.mock('@/features/workspace/VaultReferencesPanel.vue', () => ({ __esModule: true, default: {
  name: 'VaultReferencesPanel', emits: ['navigate'], template: '<button data-reference @click="$emit(\'navigate\')">Reference</button>',
} }))
afterEach(() => { document.body.replaceChildren(); localStorage.clear() })

it('opens backlinks from the status bar, restores focus on Escape and closes on navigation or vault change', async () => {
  setActivePinia(createPinia())
  const workspace = useWorkspaceStore(); workspace.hasVault = true; workspace.vaultId = 'one'; workspace.activeFilePath = '/map.canvas'
  const wrapper = mount(StatusBar, { attachTo: document.body })
  try {
    const trigger = wrapper.get('.references-trigger'); (trigger.element as HTMLButtonElement).focus()
    expect(wrapper.find('dialog').exists()).toBe(false)
    await trigger.trigger('click'); await flushPromises()
    expect(wrapper.get('dialog').attributes('aria-label')).toBe('反向链接与失效链接')
    await wrapper.get('dialog').trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('dialog').exists()).toBe(false); expect(document.activeElement).toBe(trigger.element)
    await trigger.trigger('click'); await flushPromises(); await wrapper.get('[data-reference]').trigger('click')
    expect(wrapper.find('dialog').exists()).toBe(false)
    await trigger.trigger('click'); await flushPromises(); workspace.vaultId = 'two'; await flushPromises()
    expect(wrapper.find('dialog').exists()).toBe(false)
  } finally { wrapper.unmount() }
})
