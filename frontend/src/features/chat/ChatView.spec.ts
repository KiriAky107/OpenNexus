// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import { useProviderStore } from '@/stores/provider'
import { useSkillStore } from '@/stores/skill'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import ChatView from './ChatView.vue'
import ConversationSearch from './ConversationSearch.vue'

vi.mock('@/services/agentService', () => ({ listTools: vi.fn().mockResolvedValue([]) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/stores/editor', () => ({ useEditorStore: () => ({}) }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({}) }))
vi.mock('@/components/common/MarkdownContent.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/services/chatService', () => ({
  listConversations: vi.fn().mockResolvedValue({ items: [], page: { total: 0, limit: 100, offset: 0 } }),
  loadConversationWindow: vi.fn(), createConversation: vi.fn(), removeConversation: vi.fn(), streamChat: vi.fn(), decideChatBudget: vi.fn(),
}))

beforeEach(() => {
  localStorage.removeItem('chat-sidebar-collapsed')
  localStorage.removeItem('chat-settings-collapsed')
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

it('collapses chat settings independently and hides them during global focus', async () => {
  const wrapper = mount(ChatView, { attachTo: document.body })
  await flushPromises()
  expect(wrapper.get('#chat-settings').isVisible()).toBe(true)
  await wrapper.get('[aria-controls="chat-settings"]').trigger('click')
  expect(wrapper.get('#chat-settings').isVisible()).toBe(false)
  const layout = useLayoutPreferencesStore()
  layout.toggleFocusMode()
  await flushPromises()
  expect(wrapper.get('#chat-settings').isVisible()).toBe(false)
  expect(wrapper.text()).toContain('退出专注模式')
  layout.toggleFocusMode()
  await flushPromises()
  await wrapper.get('[aria-controls="chat-settings"]').trigger('click')
  expect(wrapper.get('#chat-settings').isVisible()).toBe(true)
  wrapper.unmount()
})

it('opens a continuation dialog for the chat coordination budget', async () => {
  const wrapper = mount(ChatView, { global: { stubs: { AppDialog: { template: '<div data-dialog><slot /></div>' } } } })
  await flushPromises()
  const chat = useChatStore()
  chat.pendingBudget = { requestId: 'chat_budget_1', conversationId: 'chat', assistantMessageId: 'answer',
    usage: 51000, budget: 48000, minimumAdditional: 3001, estimated: true, reason: 'agent_reservation' }
  await flushPromises()
  expect(wrapper.get('[data-dialog]').text()).toContain('协作用量已达到上限')
  expect((wrapper.get('#chat-additional-budget').element as HTMLInputElement).value).toBe('8000')
  expect(wrapper.get('[data-dialog]').text()).toContain('停止并保留已有结果')
  wrapper.unmount()
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

it('animates only the empty active reply and interleaves reasoning with tools', async () => {
  const wrapper = mount(ChatView)
  await flushPromises()
  const chat = useChatStore()
  const base = { conversation_id: 'test', role: 'assistant' as const, content: '', created_at: new Date().toISOString() }
  chat.messages = [{ ...base, message_id: 'old' }, { ...base, message_id: 'active', tool_calls: [{ tool_call_id: 'search', name: 'rag.search', parameters: { query: 'Python' }, status: 'running' }] }]
  chat.isStreaming = true
  chat.liveMessage = chat.messages.at(-1)
  await flushPromises()
  expect(wrapper.findAll('.thinking-typewriter')).toHaveLength(1)
  expect(wrapper.findAll('.message')[0]!.find('.thinking').exists()).toBe(false)
  expect(wrapper.find('details.thinking .tool-calls').exists()).toBe(false)
  expect(wrapper.get('.tool-activity summary').text()).toContain('检索知识库')
  expect(wrapper.get('details.thinking summary').text()).toContain('正在思考')
  chat.messages[1]!.thinking = 'beforeafter'
  chat.messages[1]!.activity = [{ type: 'thinking', text: 'before' }, { type: 'tool', tool_call_id: 'search' }, { type: 'thinking', text: 'after' }]
  await flushPromises()
  expect(wrapper.findAll('details.thinking')).toHaveLength(2)
  expect(wrapper.findAll('details.thinking').map(item => item.element.textContent)).toEqual(['思考过程before', '思考过程after'])
  expect([...wrapper.element.querySelectorAll('.message:last-child .thinking, .message:last-child .tool-activity')].map(item => item.classList.contains('thinking') ? 'thinking' : 'tool')).toEqual(['thinking', 'tool', 'thinking'])
  chat.messages[1]!.content = 'Answer'
  chat.isStreaming = false
  await flushPromises()
  expect(wrapper.find('.thinking-typewriter').exists()).toBe(false)
  expect(wrapper.get('details.thinking summary').text()).toBe('思考过程')
  expect(wrapper.find('details.thinking .tool-calls').exists()).toBe(false)
  expect(wrapper.find('.tool-activity').exists()).toBe(true)
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

it('locates an old message in a bounded window and restores the original reading position', async () => {
  const wrapper = mount(ChatView)
  await flushPromises()
  const chat = useChatStore()
  chat.activeConversationId = 'search'
  chat.messages = Array.from({ length: 110 }, (_, i) => ({ message_id: `m${i}`, conversation_id: 'search', role: 'user' as const, content: `needle ${i}`, created_at: '2026-09-30T00:00:00Z' }))
  chat.messages[80]!.role = 'assistant'; chat.messages[80]!.thinking = 'Preserved reasoning disclosure'
  await flushPromises()
  const timeline = wrapper.get('.message-timeline').element as HTMLElement
  timeline.scrollTop = 123
  expect(wrapper.findAll('.message')).toHaveLength(30)
  const disclosure = wrapper.get('details.thinking')
  ;(disclosure.element as HTMLDetailsElement).open = true
  await disclosure.trigger('toggle')
  wrapper.getComponent(ConversationSearch).vm.$emit('locate', { message_id: 'm10', position: 11, entry_index: 0, kind: 'text', role: 'user', snippet: 'needle 10', created_at: '' }, 'needle')
  await flushPromises()
  expect(wrapper.get('.search-selected').attributes('data-message-id')).toBe('m10')
  expect(wrapper.findAll('.message')).toHaveLength(30)
  wrapper.getComponent(ConversationSearch).vm.$emit('clear')
  await flushPromises()
  expect(wrapper.find('.search-selected').exists()).toBe(false)
  expect(wrapper.findAll('.message')[0]!.attributes('data-message-id')).toBe('m80')
  expect(timeline.scrollTop).toBe(123)
  expect((wrapper.get('details.thinking').element as HTMLDetailsElement).open).toBe(true)
  wrapper.unmount()
})

it('restores the selected answer branch after locating an earlier attempt', async () => {
  const wrapper = mount(ChatView)
  await flushPromises()
  const chat = useChatStore()
  chat.activeConversationId = 'search'
  const message = (id: string) => ({ message_id: id, conversation_id: 'search', role: 'assistant' as const, content: id, created_at: '' })
  chat.messages = [message('current')]
  chat.messageWindow.branch_leaf = 'current'; chat.messageWindow.active_leaf = 'current'
  const locate = vi.spyOn(chat, 'locateMessage').mockImplementation(async id => { chat.messages = [message(id)]; return true })
  const restore = vi.spyOn(chat, 'restoreReading').mockImplementation(async snapshot => { chat.messages = snapshot.window.items; return true })
  await flushPromises()
  wrapper.getComponent(ConversationSearch).vm.$emit('locate', { message_id: 'previous', position: 2, entry_index: 0, kind: 'text', role: 'assistant', snippet: 'previous', created_at: '' }, 'previous')
  await flushPromises()
  expect(locate).toHaveBeenCalledWith('previous')
  expect(wrapper.get('.search-selected').attributes('data-message-id')).toBe('previous')
  wrapper.getComponent(ConversationSearch).vm.$emit('clear')
  await flushPromises()
  expect(restore).toHaveBeenCalledWith(expect.objectContaining({window: expect.objectContaining({branch_leaf: 'current'})}))
  expect(chat.messages[0]!.message_id).toBe('current')
  wrapper.unmount()
})
