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
