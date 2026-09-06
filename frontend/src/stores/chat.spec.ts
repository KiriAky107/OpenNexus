import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from './chat'
import {
  createConversation,
  listConversationMessages,
  listConversations,
  removeConversation,
  streamChat,
} from '@/services/chatService'
import type { ChatMessage, Conversation } from '@/contracts'
import type { SseClient } from '@/services/sseClient'
import { useChatPreferences } from './chatPreferences'

vi.mock('@/services/chatService', () => ({
  createConversation: vi.fn(),
  listConversationMessages: vi.fn(),
  listConversations: vi.fn(),
  removeConversation: vi.fn(),
  streamChat: vi.fn(),
  selectMessageVersion: vi.fn().mockResolvedValue({ status: 'completed' }),
}))

const page = { total: 0, limit: 100, offset: 0 }

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(streamChat).mockReset().mockReturnValue({ cancel: vi.fn() } as unknown as SseClient)
  vi.mocked(listConversations).mockReset().mockResolvedValue({ items: [], page })
  vi.mocked(listConversationMessages).mockReset().mockResolvedValue({ items: [], page: { ...page, limit: 1000 } })
  vi.mocked(createConversation).mockReset().mockImplementation(async value => ({
    ...value, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), message_count: 0,
  }))
  vi.mocked(removeConversation).mockReset().mockResolvedValue(undefined)
})

it('keeps reasoning and tools ordered and retries only the selected branch prefix', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  await store.sendMessage('original')
  const first = vi.mocked(streamChat).mock.calls[0]![1]
  const event = (name: string, data: Record<string, unknown>) => first.onEvent?.({ event: name as 'ThinkingDelta', sequence: 0, data, timestamp: new Date().toISOString() })
  event('ThinkingDelta', { text: 'before' })
  event('ToolCallStart', { tool_call_id: 'tool', name: 'rag.search' })
  event('ThinkingDelta', { text: 'after' })
  event('TextDelta', { text: 'answer' })
  expect(store.messages[1]!.activity).toEqual([{ type: 'thinking', text: 'before' }, { type: 'tool', tool_call_id: 'tool' }, { type: 'thinking', text: 'after' }])
  first.onDone?.()
  const originalUser = store.messages[0]!.message_id
  const originalAnswer = store.messages[1]!.message_id
  await store.retryMessage(originalAnswer)
  const second = vi.mocked(streamChat).mock.calls[1]!
  expect(second[0].retry_message_id).toBe(originalAnswer)
  expect(second[0].user_message_id).toBe(originalUser)
  expect(second[0].messages).toEqual([{ role: 'user', content: 'original' }])
  expect(store.messages[1]!.versions).toContain(originalAnswer)
  second[1].onDone?.()
  await store.retryMessage(originalUser, 'edited')
  expect(vi.mocked(streamChat).mock.calls[2]![0].messages).toEqual([{ role: 'user', content: 'edited' }])
  expect(store.messages[0]!.versions).toContain(originalUser)
  expect(store.messages[0]!.message_id).not.toBe(originalUser)
})

it('sends persistent message ids and restores messages from the backend', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'configured-model'
  await store.sendMessage('user input')
  const [request, handlers] = vi.mocked(streamChat).mock.calls[0]!
  expect(request.use_rag).toBe(true)
  expect(request.user_message_id).toBe(store.messages[0]?.message_id)
  expect(request.assistant_message_id).toBe(store.messages[1]?.message_id)
  expect(request.messages).toEqual([{ role: 'user', content: 'user input' }])
  handlers.onEvent?.({ event: 'Citation', sequence: 0, timestamp: '', data: { note_id: 'note', block_id: 'block', file_path: 'note.md', heading_path: ['Heading'], content: 'real evidence' } })
  handlers.onEvent?.({ event: 'TextDelta', sequence: 1, timestamp: '', data: { text: 'real response' } })
  handlers.onDone?.()

  const persisted = store.messages.map(message => ({ ...message })) as ChatMessage[]
  vi.mocked(listConversationMessages).mockResolvedValueOnce({ items: persisted, page: { total: 2, limit: 1000, offset: 0 } })
  const id = store.activeConversationId!
  await store.createNewConversation()
  expect(store.messages).toEqual([])
  await store.setActiveConversation(id)
  expect(store.messages.map(message => message.content)).toEqual(['user input', 'real response'])
  expect(store.messages[1]?.citations?.[0]?.heading_path).toBe('Heading')
})

it('leaves global persona assembly to the backend', async () => {
  useChatPreferences().settings = {persona:'stale browser persona',presetDialogue:'old example',aiAvatar:'',userAvatar:''}
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'configured-model'
  await store.sendMessage('hello')
  const request = vi.mocked(streamChat).mock.calls[0]![0]
  expect(request).not.toHaveProperty('system')
  expect(request).not.toHaveProperty('aiAvatar')
})

it('loads the newest persisted conversation on initialization', async () => {
  const conversation: Conversation = {
    conversation_id: 'persisted', title: 'Saved', created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z', message_count: 1,
  }
  vi.mocked(listConversations).mockResolvedValue({ items: [conversation], page: { ...page, total: 1 } })
  vi.mocked(listConversationMessages).mockResolvedValue({
    items: [{ message_id: 'm1', conversation_id: 'persisted', role: 'user', content: 'saved text', created_at: '2026-01-01T00:00:00Z' }],
    page: { total: 1, limit: 1000, offset: 0 },
  })
  const store = useChatStore()
  await store.loadConversations()
  expect(store.activeConversationId).toBe('persisted')
  expect(store.messages[0]?.content).toBe('saved text')
})

it('does not send without a provider and ignores callbacks from a cancelled conversation', async () => {
  const store = useChatStore()
  await store.sendMessage('no provider')
  expect(streamChat).not.toHaveBeenCalled()
  store.selectedProviderId = 'real'
  store.selectedModel = 'configured-model'
  await store.sendMessage('first')
  const old = vi.mocked(streamChat).mock.calls[0]![1]
  await store.createNewConversation()
  await store.sendMessage('second')
  old.onDone?.()
  expect(store.isStreaming).toBe(true)
  expect(store.messages[0]?.content).toBe('second')
})

it('keeps a conversation visible when backend deletion fails', async () => {
  const store = useChatStore()
  await store.createNewConversation()
  const id = store.activeConversationId!
  vi.mocked(removeConversation).mockRejectedValueOnce(new Error('offline'))
  await store.deleteConversation(id)
  expect(store.conversations.some(item => item.conversation_id === id)).toBe(true)
  expect(store.historyError).toBe('offline')
})

it('blocks sends until history is loaded, then includes that history', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  await store.createNewConversation()
  const id = store.activeConversationId!
  const history = deferred<Awaited<ReturnType<typeof listConversationMessages>>>()
  vi.mocked(listConversationMessages).mockReturnValueOnce(history.promise)
  const loading = store.setActiveConversation(id)
  store.inputText = 'followup'
  expect(store.canSend).toBe(false)
  await store.sendMessage(store.inputText)
  expect(streamChat).not.toHaveBeenCalled()
  expect(store.inputText).toBe('followup')
  history.resolve({ items: [{ message_id: 'old', conversation_id: id, role: 'user', content: 'previous context', created_at: '' }], page: { ...page, total: 1 } })
  await loading
  expect(store.canSend).toBe(true)
  await store.sendMessage(store.inputText)
  expect(vi.mocked(streamChat).mock.calls[0]![0].messages).toEqual([
    { role: 'user', content: 'previous context' }, { role: 'user', content: 'followup' },
  ])
  expect(store.messages.map(m => m.content)).toEqual(['previous context', 'followup', ''])
})

it('keeps sending blocked after history failure until a successful retry', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  await store.createNewConversation()
  const id = store.activeConversationId!
  vi.mocked(listConversationMessages).mockRejectedValueOnce(new Error('offline'))
  await store.setActiveConversation(id)
  await store.sendMessage('followup')
  expect(streamChat).not.toHaveBeenCalled()
  expect(store.historyError).toBe('offline')
  expect(store.canSend).toBe(false)
  await store.setActiveConversation(id)
  expect(store.canSend).toBe(true)
})

it.each(['switch', 'stop', 'delete'] as const)('cancels a pending send on %s without touching another send', async action => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  await store.createNewConversation()
  const b = store.activeConversationId!
  const creation = deferred<Conversation>()
  vi.mocked(createConversation).mockReturnValueOnce(creation.promise)
  const creating = store.createNewConversation()
  const a = store.activeConversationId!
  const saved = { ...store.activeConversation! }
  const sending = store.sendMessage('belongs to a')
  expect(store.isPreparing).toBe(true)
  const deleting = action === 'delete' ? store.deleteConversation(a) : undefined
  if (action === 'stop') store.stopGeneration()
  await store.setActiveConversation(b)
  await store.sendMessage('belongs to b')
  creation.resolve(saved)
  await Promise.all([creating, sending, deleting])
  expect(store.activeConversationId).toBe(b)
  expect(store.messages.every(m => m.conversation_id === b)).toBe(true)
  expect(store.messages[0]?.content).toBe('belongs to b')
  expect(streamChat).toHaveBeenCalledTimes(1)
  expect(store.isStreaming).toBe(true)
})

it('locks the initial send while creating its conversation and allows retry after failure', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  const creation = deferred<Conversation>()
  vi.mocked(createConversation).mockReturnValueOnce(creation.promise)
  const sending = store.sendMessage('first')
  await store.sendMessage('duplicate')
  expect(createConversation).toHaveBeenCalledTimes(1)
  expect(streamChat).not.toHaveBeenCalled()
  creation.resolve({ ...store.activeConversation! })
  await sending
  expect(streamChat).toHaveBeenCalledTimes(1)
  store.stopGeneration()
  store.activeConversationId = null
  vi.mocked(createConversation).mockRejectedValueOnce(new Error('offline'))
  await store.sendMessage('retry')
  expect(store.isPreparing).toBe(false)
  expect(store.canSend).toBe(true)
  await store.sendMessage('retry')
  expect(streamChat).toHaveBeenCalledTimes(2)
})

it('stops the first send before creation completes without switching conversations', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  const creation = deferred<Conversation>()
  vi.mocked(createConversation).mockReturnValueOnce(creation.promise)
  const sending = store.sendMessage('cancelled')
  const saved = { ...store.activeConversation! }
  store.stopGeneration()
  creation.resolve(saved)
  await sending
  expect(streamChat).not.toHaveBeenCalled()
  expect(store.messages).toEqual([])
  expect(store.canSend).toBe(true)
})

it('ignores old history after switching to a new conversation and sending', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  await store.createNewConversation()
  const id = store.activeConversationId!
  const history = deferred<Awaited<ReturnType<typeof listConversationMessages>>>()
  vi.mocked(listConversationMessages).mockReturnValueOnce(history.promise)
  const loading = store.setActiveConversation(id)
  await store.createNewConversation()
  await store.sendMessage('new question')
  history.resolve({ items: [], page })
  await loading
  expect(store.messages.map(m => m.content)).toEqual(['new question', ''])
  expect(store.isStreaming).toBe(true)
})

it.each(['success', 'failure'])('blocks sends and duplicate deletes until deletion ends with %s', async outcome => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  await store.createNewConversation()
  const id = store.activeConversationId!
  const removal = deferred<Awaited<ReturnType<typeof removeConversation>>>()
  vi.mocked(removeConversation).mockReturnValueOnce(removal.promise)
  const deleting = store.deleteConversation(id)
  store.inputText = 'keep this draft'
  expect(store.canSend).toBe(false)
  await store.sendMessage(store.inputText)
  await store.deleteConversation(id)
  expect(streamChat).not.toHaveBeenCalled()
  expect(removeConversation).toHaveBeenCalledTimes(1)
  expect(store.inputText).toBe('keep this draft')
  if (outcome === 'success') removal.resolve(undefined)
  else removal.reject(new Error('offline'))
  await deleting
  expect(store.isStreaming).toBe(false)
  expect(store.canSend).toBe(true)
  expect(store.activeConversationId).toBe(outcome === 'success' ? null : id)
  await store.sendMessage(store.inputText)
  expect(streamChat).toHaveBeenCalledTimes(1)
  const request = vi.mocked(streamChat).mock.calls[0]![0]
  if (outcome === 'success') expect(request.conversation_id).not.toBe(id)
  else expect(request.conversation_id).toBe(id)
})

it('keeps a deleting conversation blocked after reselecting it without blocking other conversations', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  await store.createNewConversation()
  const a = store.activeConversationId!
  await store.createNewConversation()
  const b = store.activeConversationId!
  const removal = deferred<Awaited<ReturnType<typeof removeConversation>>>()
  vi.mocked(removeConversation).mockReturnValueOnce(removal.promise)
  const deleting = store.deleteConversation(a)
  await store.setActiveConversation(a)
  expect(store.canSend).toBe(false)
  await store.sendMessage('blocked')
  expect(streamChat).not.toHaveBeenCalled()
  await store.setActiveConversation(b)
  expect(store.canSend).toBe(true)
  await store.sendMessage('belongs to b')
  const client = vi.mocked(streamChat).mock.results[0]!.value as SseClient
  removal.resolve(undefined)
  await deleting
  expect(store.activeConversationId).toBe(b)
  expect(store.messages[0]?.content).toBe('belongs to b')
  expect(store.isStreaming).toBe(true)
  expect(client.cancel).not.toHaveBeenCalled()
})
