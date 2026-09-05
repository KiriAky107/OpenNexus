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

vi.mock('@/services/chatService', () => ({
  createConversation: vi.fn(),
  listConversationMessages: vi.fn(),
  listConversations: vi.fn(),
  removeConversation: vi.fn(),
  streamChat: vi.fn(),
}))

const page = { total: 0, limit: 100, offset: 0 }

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
