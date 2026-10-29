import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import type { ChatMessage, ModelEvent } from '@/contracts'
import type { SseClient } from '@/services/sseClient'
import { loadConversationWindow, streamChat } from '@/services/chatService'
import { useChatStore } from './chat'

vi.mock('@/services/chatService', async importOriginal => ({
  ...await importOriginal<typeof import('@/services/chatService')>(),
  createConversation: vi.fn(async value => ({ ...value, created_at: '', updated_at: '', message_count: 0 })),
  loadConversationWindow: vi.fn(),
  streamChat: vi.fn(),
}))

let pinia: ReturnType<typeof createPinia>
const windowOf = (items: ChatMessage[]) => ({ items, total: items.length, start: 0,
  branch_leaf: items.at(-1)?.message_id ?? null, active_leaf: items.at(-1)?.message_id ?? null, before: null, after: null })
const emit = (handlers: Parameters<typeof streamChat>[1], event: ModelEvent['event'], data: Record<string, unknown>) =>
  handlers.onEvent?.({ event, sequence: 1, data, timestamp: '' })

beforeEach(() => {
  pinia = createPinia(); setActivePinia(pinia)
  vi.mocked(streamChat).mockReset().mockImplementation(() => ({ cancel: vi.fn() } as unknown as SseClient))
  vi.mocked(loadConversationWindow).mockReset().mockImplementation(async () => windowOf(JSON.parse(JSON.stringify(useChatStore().messages))))
})
afterEach(() => { disposePinia(pinia); vi.unstubAllGlobals() })

async function start() {
  const store = useChatStore(); store.selectedProviderId = 'configured-provider'; store.selectedModel = 'configured-model'
  await store.sendMessage('original question')
  return { store, handlers: vi.mocked(streamChat).mock.calls[0]![1], client: vi.mocked(streamChat).mock.results[0]!.value as SseClient }
}

it('chat connection recovery: quarantines later text, tools and budget events after an error', async () => {
  const { store, handlers, client } = await start()
  emit(handlers, 'TextDelta', { text: 'partial answer' })
  emit(handlers, 'ToolCallStart', { tool_call_id: 'original-tool', name: 'agent.create' })
  emit(handlers, 'ToolCallDelta', { tool_call_id: 'original-tool', result: { run_id: 'original-run' } })
  handlers.onError?.(new Error('connection interrupted'))
  const failed = JSON.parse(JSON.stringify(store.liveMessage)) as ChatMessage
  emit(handlers, 'TextDelta', { text: 'late answer' })
  emit(handlers, 'ToolCallEnd', { tool_call_id: 'original-tool', status: 'completed', result: { output: 'late result' } })
  emit(handlers, 'BudgetRequired', { request_id: 'late-budget', minimum_additional_tokens: 1 })
  expect(store.liveMessage).toEqual(failed)
  expect(store.liveMessage?.tool_calls?.[0]?.status).toBe('error')
  expect(JSON.parse(store.liveMessage!.tool_calls![0]!.result!).run_id).toBe('original-run')
  expect(store.pendingBudget).toBeNull(); expect(store.isStreaming).toBe(false)
  expect(client.cancel).toHaveBeenCalledTimes(1); expect(streamChat).toHaveBeenCalledTimes(1)
})

it('chat connection recovery: keeps the explicitly recovered window when the old done callback arrives', async () => {
  const { store, handlers } = await start()
  handlers.onError?.(new Error('connection interrupted'))
  const saved = { ...store.messages[1]!, message_id: 'saved-server-answer', content: 'persisted answer', tool_calls: [], activity: [] }
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(windowOf([{ ...store.messages[0]! }, saved]))
  expect(await store.showLatest()).toBe(true)
  handlers.onDone?.()
  expect(store.messageWindow.branch_leaf).toBe('saved-server-answer')
  expect(store.messages.at(-1)?.message_id).toBe('saved-server-answer')
  expect(store.messages.at(-1)?.content).toBe('persisted answer')
  expect(store.canSend).toBe(true); expect(streamChat).toHaveBeenCalledTimes(1)
})

it('chat connection recovery: retains the first failure when the closed stream reports another error', async () => {
  const { store, handlers, client } = await start()
  handlers.onError?.(new Error('first connection failure'))
  const failed = store.liveMessage!.content
  handlers.onError?.(new Error('late connection failure'))
  expect(store.liveMessage!.content).toBe(failed)
  expect(store.liveMessage!.content).toContain('first connection failure')
  expect(client.cancel).toHaveBeenCalledTimes(1)
})

it('chat connection recovery: leaves a newer explicitly sent stream active when old callbacks arrive', async () => {
  const { store, handlers, client } = await start()
  handlers.onError?.(new Error('connection interrupted'))
  const saved = JSON.parse(JSON.stringify(store.messages)) as ChatMessage[]
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(windowOf(saved))
  await store.sendMessage('new explicit question')
  const next = store.liveMessage, nextClient = vi.mocked(streamChat).mock.results[1]!.value as SseClient
  handlers.onError?.(new Error('old failure')); handlers.onDone?.()
  emit(handlers, 'TextDelta', { text: 'old answer' })
  expect(store.liveMessage).toBe(next); expect(store.liveMessage!.content).toBe('')
  expect(store.isStreaming).toBe(true); expect(nextClient.cancel).not.toHaveBeenCalled()
  expect(client.cancel).toHaveBeenCalledTimes(1); expect(streamChat).toHaveBeenCalledTimes(2)
})

it('chat connection recovery: rejects buffered events after malformed data in the real SSE parser', async () => {
  const service = await vi.importActual<typeof import('@/services/chatService')>('@/services/chatService')
  vi.mocked(streamChat).mockImplementationOnce(service.streamChat)
  const fetchMock = vi.fn().mockResolvedValue(new Response(
    'event: TextDelta\ndata: {"sequence":0,"data":{"text":"partial answer"}}\n\n'
    + 'event: TextDelta\ndata: malformed-json\n\n'
    + 'event: TextDelta\ndata: {"sequence":2,"data":{"text":"late answer"}}\n\n'
    + 'event: Done\ndata: {"sequence":3,"data":{}}\n\n',
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
  ))
  vi.stubGlobal('fetch', fetchMock)
  const store = useChatStore(); store.selectedProviderId = 'configured-provider'; store.selectedModel = 'configured-model'
  await store.sendMessage('one explicit question')
  await vi.waitFor(() => expect(store.isStreaming).toBe(false))
  expect(store.liveMessage!.content).toContain('partial answer')
  expect(store.liveMessage!.content).not.toContain('late answer')
  expect(fetchMock).toHaveBeenCalledTimes(1); expect(streamChat).toHaveBeenCalledTimes(1)
  expect(fetchMock.mock.calls[0]![1].signal.aborted).toBe(true)
})
