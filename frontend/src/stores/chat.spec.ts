import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from './chat'
import { streamChat } from '@/services/chatService'
import type { SseClient } from '@/services/sseClient'

vi.mock('@/services/chatService', () => ({ streamChat: vi.fn() }))
beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(streamChat).mockReset().mockReturnValue({ cancel: vi.fn() } as unknown as SseClient)
})

it('sends real user history, applies streaming changes, and restores it when switching conversations', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'configured-model'
  await store.sendMessage('user input')
  const [request, handlers] = vi.mocked(streamChat).mock.calls[0]!
  expect(request.messages).toEqual([{ role: 'user', content: 'user input' }])
  handlers.onEvent?.({ event: 'TextDelta', sequence: 0, timestamp: '', data: { text: 'real response' } })
  expect(store.messages[1]?.content).toBe('real response')
  handlers.onDone?.()
  const id = store.activeConversationId!
  store.createNewConversation()
  expect(store.messages).toEqual([])
  await store.setActiveConversation(id)
  expect(store.messages.map(m => m.content)).toEqual(['user input', 'real response'])
})

it('does not send without a provider and ignores late callbacks from a cancelled conversation', async () => {
  const store = useChatStore()
  await store.sendMessage('no provider')
  expect(streamChat).not.toHaveBeenCalled()
  store.selectedProviderId = 'real'
  store.selectedModel = 'configured-model'
  await store.sendMessage('first')
  const old = vi.mocked(streamChat).mock.calls[0]![1]
  store.createNewConversation()
  await store.sendMessage('second')
  old.onDone?.()
  expect(store.isStreaming).toBe(true)
  expect(store.messages[0]?.content).toBe('second')
})
