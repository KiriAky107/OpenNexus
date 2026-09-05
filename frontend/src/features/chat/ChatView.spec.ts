// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import { useProviderStore } from '@/stores/provider'
import { useSkillStore } from '@/stores/skill'
import ChatView from './ChatView.vue'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/stores/editor', () => ({ useEditorStore: () => ({}) }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({}) }))
vi.mock('@/components/common/MarkdownContent.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/services/chatService', () => ({
  listConversations: vi.fn().mockResolvedValue({ items: [], page: { total: 0, limit: 100, offset: 0 } }),
  listConversationMessages: vi.fn(), createConversation: vi.fn(), removeConversation: vi.fn(), streamChat: vi.fn(),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  const providers = useProviderStore()
  providers.providers = ['a', 'b'].map(id => ({
    provider_id: id, provider_type: 'openai_compatible', name: id,
    default_model: `${id}-default`, enabled: true, capabilities: { chat: true }, has_credential: false,
  }))
  providers.defaultProviderId = 'a'
  vi.spyOn(providers, 'loadProviders').mockResolvedValue(undefined)
  vi.spyOn(providers, 'loadModels').mockResolvedValue([])
  vi.spyOn(useSkillStore(), 'loadSkills').mockResolvedValue(undefined)
})

it('preserves the selected provider and manual model after leaving and returning to chat', async () => {
  const chat = useChatStore()
  const first = mount(ChatView)
  await flushPromises()
  await first.get('select').setValue('b')
  await first.get('input[list="chat-models"]').setValue('b-manual')
  first.unmount()
  const returned = mount(ChatView)
  await flushPromises()
  expect(chat.selectedProviderId).toBe('b')
  expect(chat.selectedModel).toBe('b-manual')
  expect(useProviderStore().loadModels).toHaveBeenLastCalledWith('b')
  returned.unmount()
})

it.each(['missing', 'disabled', 'unselected'])('uses the default when the selected provider is %s', async state => {
  const chat = useChatStore()
  chat.selectedProviderId = state === 'unselected' ? '' : state === 'missing' ? 'deleted' : 'b'
  chat.selectedModel = 'old-model'
  if (state === 'disabled') useProviderStore().providers[1]!.enabled = false
  const wrapper = mount(ChatView)
  await flushPromises()
  expect(chat.selectedProviderId).toBe('a')
  expect(chat.selectedModel).toBe('a-default')
  wrapper.unmount()
})

it('preserves the selection when provider discovery fails', async () => {
  const chat = useChatStore()
  chat.selectedProviderId = 'b'
  chat.selectedModel = 'b-manual'
  useProviderStore().error = 'offline'
  const wrapper = mount(ChatView)
  await flushPromises()
  expect(chat.selectedProviderId).toBe('b')
  expect(chat.selectedModel).toBe('b-manual')
  expect(wrapper.get('.error-banner').text()).toBe('offline')
  wrapper.unmount()
})

it.each(['providers', 'skills'])('ignores initialization after unmount while %s are loading', async source => {
  const chat = useChatStore()
  let finish!: () => void
  const pending = new Promise<void>(resolve => { finish = resolve })
  if (source === 'providers') vi.mocked(useProviderStore().loadProviders).mockReturnValueOnce(pending)
  else vi.mocked(useSkillStore().loadSkills).mockReturnValueOnce(pending)
  const first = mount(ChatView)
  first.unmount()
  finish()
  await flushPromises()
  expect(chat.selectedProviderId).toBe('')
  expect(chat.selectedModel).toBe('')
  expect(useProviderStore().loadModels).not.toHaveBeenCalled()

  const returned = mount(ChatView)
  await flushPromises()
  expect(chat.selectedProviderId).toBe('a')
  expect(chat.selectedModel).toBe('a-default')
  await returned.get('textarea').setValue('hello')
  expect(returned.get('button.button-primary').attributes('disabled')).toBeUndefined()
  returned.unmount()
})
