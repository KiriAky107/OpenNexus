import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createConversation, loadConversationWindow } from '@/services/chatService'
import { useChatStore } from './chat'

vi.mock('@/services/chatService', async importOriginal => ({
  ...await importOriginal<typeof import('@/services/chatService')>(),
  createConversation: vi.fn(async value => ({ ...value, created_at: '', updated_at: '', message_count: 0 })),
  loadConversationWindow: vi.fn(),
}))

let pinia: ReturnType<typeof createPinia>
beforeEach(() => { pinia = createPinia(); setActivePinia(pinia) })
afterEach(() => { disposePinia(pinia); vi.unstubAllGlobals(); vi.clearAllMocks() })

const event = (name: string, sequence: number, data: Record<string, unknown>) =>
  `event: ${name}\ndata: ${JSON.stringify({ sequence, data })}\n\n`

it('chat premature EOF preserves partial content and original run as unconfirmed', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(
    event('TextDelta', 0, { text: 'partial answer' })
    + event('ToolCallStart', 1, { tool_call_id: 'original-tool', name: 'agent.create' })
    + event('ToolCallDelta', 2, { tool_call_id: 'original-tool', result: { run_id: 'original-run' } }),
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
  ))
  vi.stubGlobal('fetch', fetchMock)
  const store = useChatStore()
  store.selectedProviderId = 'configured-provider'; store.selectedModel = 'configured-model'
  await store.sendMessage('one explicit question')
  await vi.waitFor(() => expect(store.isStreaming).toBe(false))
  expect(store.liveMessage!.tool_calls![0]!.status).toBe('error')
  expect(store.liveMessage!.content).toContain('partial answer')
  expect(store.liveMessage!.content).toMatch(/响应提前结束|response ended early/)
  expect(JSON.parse(store.liveMessage!.tool_calls![0]!.result!).run_id).toBe('original-run')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(fetchMock.mock.calls[0]![1].signal.aborted).toBe(true)
  expect(createConversation).toHaveBeenCalledTimes(1)
  expect(loadConversationWindow).not.toHaveBeenCalled()
})

it('chat premature EOF recovers only through an explicit history reload without another POST', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(event('TextDelta', 0, { text: 'partial answer' }),
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } }))
  vi.stubGlobal('fetch', fetchMock)
  const store = useChatStore()
  store.selectedProviderId = 'configured-provider'; store.selectedModel = 'configured-model'
  await store.sendMessage('one explicit question')
  await vi.waitFor(() => expect(store.isStreaming).toBe(false))
  expect(store.liveMessage!.content).toMatch(/响应提前结束|response ended early/)
  expect(loadConversationWindow).not.toHaveBeenCalled()
  const saved = { ...store.messages[1]!, message_id: 'original-persisted-answer',
    content: 'server-confirmed answer', tool_calls: [], activity: [] }
  vi.mocked(loadConversationWindow).mockResolvedValueOnce({ items: [store.messages[0]!, saved],
    total: 2, start: 0, branch_leaf: saved.message_id, active_leaf: saved.message_id, before: null, after: null })
  expect(await store.showLatest()).toBe(true)
  expect(store.messages.at(-1)?.message_id).toBe(saved.message_id)
  expect(store.messages.at(-1)?.content).toBe('server-confirmed answer')
  expect(store.canSend).toBe(true)
  expect(fetchMock).toHaveBeenCalledTimes(1)
})
