// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ChatPersonaDialog from './ChatPersonaDialog.vue'
import * as desktop from '@/services/platform/desktop'
import { useWorkspaceStore } from '@/stores/workspace'
import { useChatPreferences } from '@/stores/chatPreferences'

import { apiClient } from '@/services/apiClient'
vi.mock('@/services/apiClient', () => ({apiClient:{get:vi.fn(),put:vi.fn()}}))
beforeEach(() => { vi.mocked(apiClient.get).mockResolvedValue({version:0,name:'',system_prompt:'',dialogue_pairs:[]}); vi.mocked(apiClient.put).mockImplementation(async (_url, value) => ({...(value as object),version:1})); localStorage.removeItem('chat-persona-preferences-v1'); setActivePinia(createPinia()) })

it('saves global prompt and structured dialogue pairs to the AI Core', async () => {
  const wrapper = mount(ChatPersonaDialog)
  await flushPromises()
  await wrapper.get('.persona-prompt').setValue('耐心的老师')
  await wrapper.findAll('button').find(b => b.text().includes('添加对话对'))!.trigger('click')
  const inputs = wrapper.findAll('.dialogue-pairs textarea')
  await inputs[0]!.setValue('你好')
  await inputs[1]!.setValue('你好，我是老师')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(apiClient.put).toHaveBeenCalledWith('/api/settings/persona', expect.objectContaining({system_prompt:'耐心的老师',dialogue_pairs:[{user:'你好',assistant:'你好，我是老师'}]}))
  expect(wrapper.emitted('close')).toHaveLength(1)
  wrapper.unmount()
})

it('keeps unsaved edits out of active preferences', async () => {
  const wrapper = mount(ChatPersonaDialog)
  await flushPromises()
  await wrapper.findAll('textarea')[0]!.setValue('未保存人设')
  await wrapper.get('dialog').trigger('cancel')
  expect(useChatPreferences().settings.persona).toBe('')
  expect(wrapper.emitted('close')).toHaveLength(1)
  wrapper.unmount()
})

it('persists separate local avatars and rejects remote avatar URLs', () => {
  const preferences = useChatPreferences()
  const aiAvatar = 'data:image/png;base64,aGVsbG8='
  const userAvatar = 'data:image/webp;base64,d29ybGQ='
  preferences.save({...preferences.settings,aiAvatar,userAvatar})
  setActivePinia(createPinia())
  expect(useChatPreferences().settings).toMatchObject({aiAvatar,userAvatar})
  expect(() => useChatPreferences().save({...preferences.settings,aiAvatar:'https://example.com/avatar.png'})).toThrow()
  expect(useChatPreferences().settings.aiAvatar).toBe(aiAvatar)
})


it('sends the loaded persona revision and refuses a form from another Vault', async () => {
  const desktopMode = vi.spyOn(desktop, 'isDesktop').mockReturnValue(true)
  const workspace = useWorkspaceStore()
  workspace.vaultId = 'first'
  vi.mocked(apiClient.get).mockResolvedValue({ version: 2, revision: 'a'.repeat(64), name: '', system_prompt: '', dialogue_pairs: [] })
  vi.mocked(apiClient.put).mockClear()
  const wrapper = mount(ChatPersonaDialog)
  try {
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(apiClient.put).toHaveBeenCalledWith('/api/settings/persona', expect.objectContaining({ revision: 'a'.repeat(64) }))
    vi.mocked(apiClient.put).mockClear()
    workspace.vaultId = 'second'
    await wrapper.vm.$nextTick()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(apiClient.put).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('工作区已切换')
  } finally { wrapper.unmount(); desktopMode.mockRestore() }
})


it('previews legacy content without writing and preserves the Vault CAS when copied', async () => {
  const desktopMode = vi.spyOn(desktop, 'isDesktop').mockReturnValue(true)
  useWorkspaceStore().vaultId = 'import-target'
  vi.mocked(apiClient.put).mockClear()
  vi.mocked(apiClient.get).mockImplementation(async url => url.endsWith('/legacy')
    ? { available: true, persona: { version: 90, name: 'Old', system_prompt: 'Legacy prompt', dialogue_pairs: [{ user: 'Question', assistant: 'Answer' }] } }
    : { version: 3, revision: 'c'.repeat(64), name: 'Current', system_prompt: 'Current prompt', dialogue_pairs: [] })
  const wrapper = mount(ChatPersonaDialog)
  try {
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text().includes('查看旧全局人设'))!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Legacy prompt')
    expect((wrapper.get('.persona-prompt').element as HTMLTextAreaElement).value).toBe('Current prompt')
    expect(apiClient.put).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(button => button.text().includes('填入当前表单'))!.trigger('click')
    expect(apiClient.put).not.toHaveBeenCalled()
    expect((wrapper.get('.persona-prompt').element as HTMLTextAreaElement).value).toBe('Legacy prompt')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(apiClient.put).toHaveBeenCalledWith('/api/settings/persona', expect.objectContaining({ version: 3, revision: 'c'.repeat(64), name: 'Old', system_prompt: 'Legacy prompt' }))
  } finally { wrapper.unmount(); desktopMode.mockRestore() }
})

it('discards a late legacy preview after switching Vaults', async () => {
  const desktopMode = vi.spyOn(desktop, 'isDesktop').mockReturnValue(true)
  const workspace = useWorkspaceStore()
  workspace.vaultId = 'before'
  let complete!: (value: unknown) => void
  vi.mocked(apiClient.get).mockImplementation(async url => url.endsWith('/legacy')
    ? await new Promise(resolve => { complete = resolve })
    : { version: 0, revision: '', name: '', system_prompt: '', dialogue_pairs: [] })
  const wrapper = mount(ChatPersonaDialog)
  try {
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text().includes('查看旧全局人设'))!.trigger('click')
    workspace.vaultId = 'after'
    complete({ available: true, persona: { version: 1, name: 'Late', system_prompt: 'Stale source', dialogue_pairs: [] } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Stale source')
    expect(wrapper.findAll('button').some(button => button.text().includes('填入当前表单'))).toBe(false)
  } finally { wrapper.unmount(); desktopMode.mockRestore() }
})
