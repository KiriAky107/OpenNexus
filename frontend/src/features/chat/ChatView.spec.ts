// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import { useProviderStore } from '@/stores/provider'
import { useSkillStore } from '@/stores/skill'
import ChatView from './ChatView.vue'

vi.mock('@/services/agentService', () => ({ listTools: vi.fn().mockResolvedValue([]) }))
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

it('reveals only cited sources as the streamed answer reaches complete markers', async () => {
  const wrapper = mount(ChatView)
  await flushPromises()
  const chat = useChatStore()
  chat.messages = [{ message_id: 'answer', conversation_id: 'test', role: 'assistant', content: '', created_at: new Date().toISOString(),
    citations: [1, 2, 3].map(number => ({ note_id: 'note', block_id: String(number), file_path: 'note.md', heading_path: '', content: `source ${number}` })),
  }]
  await flushPromises()
  expect(wrapper.findAll('.citation-card')).toHaveLength(0)
  chat.messages[0]!.content = '结论 [3'
  await flushPromises()
  expect(wrapper.findAll('.citation-card')).toHaveLength(0)
  chat.messages[0]!.content += ']，补充 [1]，再次 [3]'
  await flushPromises()
  expect(wrapper.findAll('.citation-card .badge').map(item => item.text())).toEqual(['3', '1'])
  wrapper.unmount()
})

it('animates only the active reply and keeps tools inside the reasoning disclosure', async () => {
  const wrapper = mount(ChatView)
  await flushPromises()
  const chat = useChatStore()
  const base = { conversation_id: 'test', role: 'assistant' as const, content: '', created_at: new Date().toISOString() }
  chat.messages = [{ ...base, message_id: 'old' }, { ...base, message_id: 'active', tool_calls: [{ tool_call_id: 'search', name: 'rag.search', parameters: { query: 'Python' }, status: 'running' }] }]
  chat.isStreaming = true
  await flushPromises()
  expect(wrapper.findAll('.thinking-typewriter')).toHaveLength(1)
  expect(wrapper.findAll('.message')[0]!.find('.thinking').exists()).toBe(false)
  expect(wrapper.get('details.thinking .tool-calls').text()).toContain('rag.search')
  expect(wrapper.get('details.thinking summary').text()).toContain('正在思考')
  chat.messages[1]!.thinking = 'beforeafter'
  chat.messages[1]!.activity = [{ type: 'thinking', text: 'before' }, { type: 'tool', tool_call_id: 'search' }, { type: 'thinking', text: 'after' }]
  await flushPromises()
  expect(wrapper.get('details.thinking').element.textContent).toMatch(/before[\s\S]*rag.search[\s\S]*after/)
  chat.messages[1]!.content = 'Answer'
  chat.isStreaming = false
  await flushPromises()
  expect(wrapper.find('.thinking-typewriter').exists()).toBe(false)
  expect(wrapper.get('details.thinking summary').text()).toBe('思考过程')
  expect(wrapper.find('details.thinking .tool-calls').exists()).toBe(true)
  wrapper.unmount()
})

it('reuses the settings model cache and renders the shared select style', async () => {
  const providers = useProviderStore()
  providers.modelsByProvider.a = [{model_id:'a-default',name:'A model',capabilities:{chat:true}}]
  const wrapper = mount(ChatView)
  await flushPromises()
  expect(providers.loadModels).not.toHaveBeenCalled()
  expect(wrapper.get('select#chat-model-select').classes()).toContain('select')
  expect(wrapper.get('select#chat-model-select').text()).toContain('A model')
  wrapper.unmount()
})

it('preserves the selected provider and manual model after leaving and returning to chat', async () => {
  const chat = useChatStore()
  const first = mount(ChatView)
  await flushPromises()
  await first.get('select').setValue('b')
  await first.get('input[data-field="manual-model"]').setValue('b-manual')
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


it('sends on Enter but preserves Shift+Enter and IME confirmation', async () => {
  const chat = useChatStore()
  const send = vi.spyOn(chat, 'sendMessage').mockResolvedValue(undefined)
  const wrapper = mount(ChatView)
  await flushPromises()
  const input = wrapper.get('textarea')
  await input.setValue('问题')
  await input.trigger('keydown', { key: 'Enter', isComposing: true })
  await input.trigger('keydown', { key: 'Enter', shiftKey: true })
  expect(send).not.toHaveBeenCalled()
  await input.trigger('keydown', { key: 'Enter' })
  expect(send).toHaveBeenCalledWith('问题', undefined, undefined)
  await input.trigger('keydown', { key: 'Enter', repeat: true })
  expect(send).toHaveBeenCalledTimes(1)
  wrapper.unmount()
})
