// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as pluginService from '@/services/pluginService'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import CommandPalette from './CommandPalette.vue'

vi.mock('@/services/pluginService', async (loadOriginal) => {
  const original = await loadOriginal<typeof import('@/services/pluginService')>()
  return { ...original, listPluginCommands: vi.fn(), executePluginCommand: vi.fn() }
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(pluginService.listPluginCommands).mockResolvedValue([{
    command_id: 'demo.selection',
    plugin_id: 'demo',
    title: '处理选区',
    description: '',
    icon: null,
    locations: ['command_palette'],
    when: ['workspace.has_vault', 'editor.has_note', 'editor.has_selection'],
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    enabled: true,
  }])
  vi.mocked(pluginService.executePluginCommand).mockResolvedValue({
    command_id: 'demo.selection',
    status: 'completed',
    effect: { type: 'notification', payload: { level: 'success', message: '完成' } },
  })
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('CommandPalette Plugin Command', () => {
  it('filters by when context and sends stable backend identities plus the captured selection', async () => {
    const workspace = useWorkspaceStore()
    workspace.hasVault = true
    workspace.vaultId = 'vault-default'
    const editor = useEditorStore()
    editor.currentNoteId = 'note-1'
    editor.currentFilePath = '/note.md'

    vi.spyOn(window, 'getSelection').mockReturnValue({
      toString: () => 'selected text',
    } as Selection)

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<div />' } }],
    })
    await router.push('/')
    const wrapper = mount(CommandPalette, { attachTo: document.body, global: { plugins: [router] } })

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'p', ctrlKey: true }))
    await flushPromises()
    const command = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.includes('处理选区'))
    expect(command).toBeTruthy()
    command!.click()
    await flushPromises()

    expect(pluginService.executePluginCommand).toHaveBeenCalledWith('demo.selection', {}, {
      vault_id: 'vault-default',
      note_id: 'note-1',
      file_path: '/note.md',
      selection: 'selected text',
    })
    expect(document.body.textContent).toContain('完成')
    wrapper.unmount()
  })
})
