import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from './chat'
import {
  createConversation,
  loadConversationWindow,
  listConversations,
  removeConversation,
  streamChat,
  decideChatBudget,
  selectMessageVersion,
} from '@/services/chatService'
import type { ChatMessage, Conversation } from '@/contracts'
import type { SseClient } from '@/services/sseClient'
import { useChatPreferences } from './chatPreferences'

vi.mock('@/services/chatService', () => ({
  createConversation: vi.fn(),
  loadConversationWindow: vi.fn(),
  listConversations: vi.fn(),
  removeConversation: vi.fn(),
  streamChat: vi.fn(),
  decideChatBudget: vi.fn().mockResolvedValue({ status: 'accepted' }),
  selectMessageVersion: vi.fn().mockResolvedValue({ status: 'completed' }),
}))

const page = { total: 0, limit: 100, offset: 0 }
function windowOf(items: ChatMessage[] = [], total = items.length) {
  return { items, total, start: Math.max(0,total-items.length), branch_leaf: items.at(-1)?.message_id || null,
    active_leaf: items.at(-1)?.message_id || null, before: total > items.length ? 'before' : null, after: null }
}

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
  vi.mocked(loadConversationWindow).mockReset().mockImplementation(async () => windowOf(JSON.parse(JSON.stringify(useChatStore().messages))))
  vi.mocked(createConversation).mockReset().mockImplementation(async value => ({
    ...value, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), message_count: 0,
  }))
  vi.mocked(removeConversation).mockReset().mockResolvedValue(undefined)
  vi.mocked(decideChatBudget).mockReset().mockResolvedValue({ status: 'accepted' })
  vi.mocked(selectMessageVersion).mockReset().mockResolvedValue({ status: 'completed' })
})

it('pauses for chat budget approval and continues the same response stream', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  await store.sendMessage('create a study plan')
  const handlers = vi.mocked(streamChat).mock.calls[0]![1]
  handlers.onEvent?.({ event: 'BudgetRequired', sequence: 4, timestamp: '', data: {
    request_id: 'chat_budget_1', token_usage: 51000, token_budget: 48000,
    minimum_additional_tokens: 3001, estimated: true, reason: 'agent_reservation',
  } })
  expect(store.pendingBudget?.minimumAdditional).toBe(3001)
  expect(store.isStreaming).toBe(true)
  await store.resolveBudget(8000)
  expect(decideChatBudget).toHaveBeenCalledWith('chat_budget_1', store.activeConversationId, store.messages[1]!.message_id, 8000)
  expect(streamChat).toHaveBeenCalledTimes(1)
  expect(store.isStreaming).toBe(true)
  handlers.onEvent?.({ event: 'BudgetResolved', sequence: 5, timestamp: '', data: { request_id: 'chat_budget_1' } })
  handlers.onDone?.()
  expect(store.isStreaming).toBe(false)
  expect(store.pendingBudget).toBeNull()
})

it('shows the delegated run while the chat turn is still open', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  await store.sendMessage('write the note')
  const handlers = vi.mocked(streamChat).mock.calls[0]![1]
  const emit = (event: string, sequence: number, data: Record<string, unknown>) =>
    handlers.onEvent?.({ event: event as 'ToolCallStart', sequence, timestamp: '', data })
  emit('ToolCallStart', 0, { tool_call_id: 'create', name: 'agent.create' })
  emit('ToolCallDelta', 1, { tool_call_id: 'create', result: { run_id: 'run_live', status: 'queued' } })
  expect(JSON.parse(store.messages[1]!.tool_calls![0]!.result!)).toEqual({ run_id: 'run_live', status: 'queued' })
  expect(store.messages[1]!.tool_calls![0]!.status).toBe('running')
  expect(store.isStreaming).toBe(true)
  emit('ToolCallEnd', 2, { tool_call_id: 'create', status: 'completed', result: { run_id: 'run_live', status: 'completed', output: 'saved' } })
  expect(JSON.parse(store.messages[1]!.tool_calls![0]!.result!).output).toBe('saved')
  handlers.onDone?.()
  expect(store.isStreaming).toBe(false)
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
  expect(store.messages[1]!.activity).toEqual([{ type: 'thinking', text: 'before', sequence: 0 }, { type: 'tool', tool_call_id: 'tool', sequence: 0 }, { type: 'thinking', text: 'after', sequence: 0 }, { type: 'text', text: 'answer', sequence: 0 }])
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
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(windowOf(persisted))
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
  vi.mocked(loadConversationWindow).mockResolvedValue(windowOf([{ message_id: 'm1', conversation_id: 'persisted', role: 'user', content: 'saved text', created_at: '2026-01-01T00:00:00Z' }]))
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

it('blocks sends until history is loaded, then requests saved branch context', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  await store.createNewConversation()
  const id = store.activeConversationId!
  const history = deferred<Awaited<ReturnType<typeof loadConversationWindow>>>()
  vi.mocked(loadConversationWindow).mockReturnValueOnce(history.promise)
  const loading = store.setActiveConversation(id)
  store.inputText = 'followup'
  expect(store.canSend).toBe(false)
  await store.sendMessage(store.inputText)
  expect(streamChat).not.toHaveBeenCalled()
  expect(store.inputText).toBe('followup')
  history.resolve(windowOf([{ message_id: 'old', conversation_id: id, role: 'user', content: 'previous context', created_at: '' }]))
  await loading
  expect(store.canSend).toBe(true)
  await store.sendMessage(store.inputText)
  expect(vi.mocked(streamChat).mock.calls[0]![0].messages).toEqual([
    { role: 'user', content: 'followup' },
  ])
  expect(store.messages.map(m => m.content)).toEqual(['previous context', 'followup', ''])
})

it('keeps sending blocked after history failure until a successful retry', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'
  store.selectedModel = 'model'
  await store.createNewConversation()
  const id = store.activeConversationId!
  vi.mocked(loadConversationWindow).mockRejectedValueOnce(new Error('offline'))
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
  const history = deferred<Awaited<ReturnType<typeof loadConversationWindow>>>()
  vi.mocked(loadConversationWindow).mockReturnValueOnce(history.promise)
  const loading = store.setActiveConversation(id)
  await store.createNewConversation()
  await store.sendMessage('new question')
  history.resolve(windowOf())
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

it('captures fresh workspace contents each send and restores the saved context for page continuation', async () => {
  const store = useChatStore()
  store.selectedProviderId = 'real'; store.selectedModel = 'model'; store.allowAgent = true
  const context = { file_path: 'note.md', content: 'unsaved first' }
  await store.sendMessage('first', undefined, context)
  context.content = 'unsaved second'
  expect(vi.mocked(streamChat).mock.calls[0]![0].workspace_context?.content).toBe('unsaved first')
  expect(store.messages[0]?.workspace_context?.content).toBe('unsaved first')
  vi.mocked(streamChat).mock.calls[0]![1].onDone?.()
  await store.sendMessage('second', undefined, context)
  expect(vi.mocked(streamChat).mock.calls[1]![0].workspace_context?.content).toBe('unsaved second')
  expect(vi.mocked(streamChat).mock.calls[1]![0].allow_agent).toBe(true)
  vi.mocked(streamChat).mock.calls[1]![1].onDone?.()
  await store.sendMessage('continue on chat page')
  expect(vi.mocked(streamChat).mock.calls[2]![0].workspace_context?.content).toBe('unsaved second')
  vi.mocked(streamChat).mock.calls[2]![1].onDone?.()
  await store.sendMessage('no active file', undefined, null)
  expect(vi.mocked(streamChat).mock.calls[3]![0].workspace_context).toBeUndefined()
  expect(vi.mocked(createConversation)).toHaveBeenCalledTimes(1)
})

it('uploads attachments and includes their durable IDs in an attachment-only message', async () => {
  const { mediaService } = await import('@/services/mediaService')
  const upload = vi.spyOn(mediaService,'upload').mockResolvedValue({attachment_id:'media_test.docx'})
  const store=useChatStore(); store.selectedProviderId='real'; store.selectedModel='model'
  await store.uploadFiles([new File(['document'],'test.docx')])
  expect(store.pendingAttachments[0]?.name).toBe('test.docx')
  await store.sendMessage('')
  expect(vi.mocked(streamChat).mock.calls[0]![0].attachments).toEqual(['media_test.docx'])
  expect(store.messages[0]?.attachments).toEqual(['media_test.docx'])
  expect(store.pendingAttachments).toEqual([])
  upload.mockRestore()
})

it.each(['user', 'assistant'] as const)('retries older %s messages with their attachments, preserving pending uploads', async role => {
  const s=useChatStore(); s.selectedProviderId='real'; s.selectedModel='model'
  s.pendingAttachments=[{attachment_id:'first.md',name:'first.md'}]
  await s.sendMessage('first'); vi.mocked(streamChat).mock.calls.at(-1)![1].onDone?.()
  const old=s.messages[role === 'user' ? 0 : 1]!.message_id
  s.pendingAttachments=[{attachment_id:'later.md',name:'later.md'}]
  await s.sendMessage('later'); vi.mocked(streamChat).mock.calls.at(-1)![1].onDone?.()
  s.pendingAttachments=[{attachment_id:'draft.md',name:'draft.md'}]
  await s.retryMessage(old, role === 'user' ? 'edited first' : undefined)
  expect(vi.mocked(streamChat).mock.calls.at(-1)![0].attachments).toEqual(['first.md'])
  expect(s.pendingAttachments.map(a=>a.attachment_id)).toEqual(['draft.md'])
})

it('restores each answer context after history reload, including explicitly absent workspace context', async () => {
  const s=useChatStore(); s.selectedProviderId='real'; s.selectedModel='model'
  const first={file_path:'a.md',content:'A'}; const second={file_path:'b.md',content:'B'}
  await s.sendMessage('explain',undefined,first); vi.mocked(streamChat).mock.calls.at(-1)![1].onDone?.()
  const original=s.messages[1]!.message_id
  await s.retryMessage(original,undefined,second); vi.mocked(streamChat).mock.calls.at(-1)![1].onDone?.()
  expect(s.messages[0]!.workspace_context).toEqual(first)
  expect(s.messages[1]!.workspace_context).toEqual(second)
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(windowOf(JSON.parse(JSON.stringify(s.messages))))
  await s.setActiveConversation(s.activeConversationId!)
  await s.retryMessage(s.messages[1]!.message_id)
  expect(vi.mocked(streamChat).mock.calls.at(-1)![0].workspace_context).toEqual(second)
  vi.mocked(streamChat).mock.calls.at(-1)![1].onDone?.()
  await s.retryMessage(s.messages[1]!.message_id,undefined,null)
  vi.mocked(streamChat).mock.calls.at(-1)![1].onDone?.()
  // API 将缺失的捕获上下文序列化为 null；不要回退到原始用户快照。
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(windowOf(JSON.parse(JSON.stringify(s.messages))))
  await s.setActiveConversation(s.activeConversationId!)
  await s.sendMessage('continue')
  expect(vi.mocked(streamChat).mock.calls.at(-1)![0].workspace_context).toBeUndefined()
})


function largeWindow(start: number, total = 10000) {
  const items: ChatMessage[] = Array.from({length: Math.min(60,total-start)}, (_, offset) => ({
    message_id: `m${start+offset}`, conversation_id: 'large', role: (start+offset)%2 ? 'assistant' : 'user',
    content: `body ${start+offset}`, created_at: '', parent_message_id: start+offset ? `m${start+offset-1}` : null,
  }))
  return {...windowOf(items,total), start, branch_leaf: `m${total-1}`, active_leaf: `m${total-1}`,
    before: start ? `before-${start}` : null, after: start+items.length < total ? `after-${start+items.length-1}` : null}
}

it('opens a 10000-message conversation with one bounded request and pages with overlap', async () => {
  const s = useChatStore()
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(largeWindow(9940))
  await s.setActiveConversation('large')
  expect(loadConversationWindow).toHaveBeenCalledTimes(1)
  expect(s.messages).toHaveLength(60)
  expect(s.messageWindow.total).toBe(10000)
  const original = s.captureReading()
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(largeWindow(9910))
  await s.loadEarlier()
  expect(loadConversationWindow).toHaveBeenLastCalledWith('large',{cursor:'before-9940'})
  expect(s.messages).toHaveLength(60)
  expect(s.atLatest).toBe(false)
  expect(s.messages[30]!.message_id).toBe(original.window.items[0]!.message_id)
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(largeWindow(9940))
  await s.loadLater()
  expect(loadConversationWindow).toHaveBeenLastCalledWith('large',{cursor:'after-9969'})
  expect(s.atLatest).toBe(true)
  expect(await s.restoreReading(original)).toBe(true)
  expect(loadConversationWindow).toHaveBeenCalledTimes(3)
})

it('locates a message by ID, selects an alternate branch only when needed, and restores the bounded snapshot', async () => {
  const s=useChatStore()
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(largeWindow(9940))
  await s.setActiveConversation('large')
  const original=s.captureReading()
  const outside=Object.assign(new Error('other answer'),{code:'CHAT_MESSAGE_OUTSIDE_BRANCH'})
  vi.mocked(loadConversationWindow).mockRejectedValueOnce(outside).mockResolvedValueOnce({
    ...largeWindow(4970), branch_leaf:'old-leaf', active_leaf:'old-leaf', items:[{
      message_id:'old-answer',conversation_id:'large',role:'assistant',content:'older answer',created_at:'',
    }],
  })
  expect(await s.locateMessage('old-answer')).toBe(true)
  expect(selectMessageVersion).toHaveBeenCalledWith('large','old-answer')
  expect(loadConversationWindow).toHaveBeenLastCalledWith('large',{around:'old-answer'})
  expect(await s.restoreReading(original)).toBe(true)
  expect(selectMessageVersion).toHaveBeenLastCalledWith('large','m9999')
  expect(s.messages.map(m=>m.message_id)).toEqual(original.window.items.map(m=>m.message_id))
  expect(loadConversationWindow).toHaveBeenCalledTimes(3)
})

it('keeps live deltas while paging older messages and sends only the new turn with a saved-context guard', async () => {
  const s=useChatStore(); s.selectedProviderId='real'; s.selectedModel='model'
  await s.createNewConversation()
  s.activeConversationId='large'
  s.conversations=[{conversation_id:'large',title:'Large',message_count:10000,created_at:'',updated_at:''}]
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(largeWindow(9940))
  await s.setActiveConversation('large')
  await s.sendMessage('continue')
  const [request,handlers]=vi.mocked(streamChat).mock.calls.at(-1)!
  expect(request.messages).toEqual([{role:'user',content:'continue'}])
  expect(request.use_saved_history).toBe(true)
  expect(request.expected_branch_leaf).toBe('m9999')
  expect(s.activeConversation!.message_count).toBe(10002)
  const live=s.liveMessage!
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(largeWindow(9910))
  await s.loadEarlier()
  expect(s.messages.some(m=>m.message_id===live.message_id)).toBe(false)
  handlers.onEvent?.({event:'TextDelta',sequence:0,timestamp:'',data:{text:'still streaming'}})
  expect(s.liveMessage!.content).toBe('still streaming')
  const tail=largeWindow(9941,10001)
  tail.items[59]={...s.captureReading().window.items[0]!,message_id:request.user_message_id!,role:'user',content:'continue'}
  vi.mocked(loadConversationWindow).mockResolvedValueOnce(tail)
  await s.showLatest()
  expect(s.messages).toHaveLength(60)
  expect(s.messages.at(-1)!.message_id).toBe(live.message_id)
  expect(s.messages.at(-1)!.content).toBe('still streaming')
  handlers.onEvent?.({event:'TextDelta',sequence:1,timestamp:'',data:{text:' done'}})
  expect(s.messages.at(-1)!.content).toBe('still streaming done')
  handlers.onDone?.()
  expect(s.activeConversation!.message_count).toBe(10002)
})

it('rejects a late window after changing vaults even when the conversation ID is reused', async () => {
  const {useWorkspaceStore}=await import('./workspace')
  const w=useWorkspaceStore(); w.vaultId='first'
  const s=useChatStore(), old=deferred<Awaited<ReturnType<typeof loadConversationWindow>>>()
  vi.mocked(loadConversationWindow).mockReturnValueOnce(old.promise)
  const pending=s.setActiveConversation('large')
  w.vaultId='second'
  vi.mocked(loadConversationWindow).mockResolvedValueOnce({...largeWindow(9940),items:[{message_id:'second-vault',conversation_id:'large',role:'user',content:'second',created_at:''}]})
  await s.setActiveConversation('large')
  old.resolve(largeWindow(9940)); await pending
  expect(s.messages.map(m=>m.message_id)).toEqual(['second-vault'])
  expect(s.windowBusy).toBe(false)
})

it.each(['stop', 'error', 'done'] as const)('refreshes the retry target and branch guard after stream %s', async outcome => {
  const s = useChatStore(); s.selectedProviderId = 'real'; s.selectedModel = 'model'
  await s.sendMessage('original')
  const user = { ...s.messages[0]! }
  const handlers = vi.mocked(streamChat).mock.calls[0]![1]
  if (outcome === 'stop') s.stopGeneration()
  else if (outcome === 'error') handlers.onError?.(new Error('SSE connection failed: 409'))
  else handlers.onDone?.()
  vi.mocked(loadConversationWindow).mockResolvedValueOnce({
    ...windowOf([user]), branch_leaf: user.message_id, active_leaf: 'server-current-leaf',
  })
  await s.retryMessage(user.message_id, 'edited')
  expect(loadConversationWindow).toHaveBeenLastCalledWith(s.activeConversationId, {
    around: user.message_id, branch_leaf: user.message_id,
  })
  const retry = vi.mocked(streamChat).mock.calls[1]![0]
  expect(retry.expected_branch_leaf).toBe('server-current-leaf')
  expect(retry.retry_message_id).toBe(user.message_id)
  expect(retry.messages).toEqual([{ role: 'user', content: 'edited' }])
})

it('regenerates an older answer after refresh without switching to the latest reading window', async () => {
  const s = useChatStore(); s.selectedProviderId = 'real'; s.selectedModel = 'model'
  await s.sendMessage('old question')
  const [user, answer] = s.messages.map(message => ({ ...message }))
  vi.mocked(streamChat).mock.calls[0]![1].onDone?.()
  vi.mocked(loadConversationWindow).mockResolvedValueOnce({
    ...windowOf([user!, answer!]), branch_leaf: answer!.message_id, active_leaf: 'much-later-leaf',
  })
  await s.retryMessage(answer!.message_id)
  const retry = vi.mocked(streamChat).mock.calls[1]![0]
  expect(retry.expected_branch_leaf).toBe('much-later-leaf')
  expect(retry.user_message_id).toBe(user!.message_id)
  expect(retry.retry_message_id).toBe(answer!.message_id)
  expect(retry.messages).toEqual([{ role: 'user', content: 'old question' }])
  expect(selectMessageVersion).not.toHaveBeenCalled()
})

it.each(['stop', 'switch', 'vault'] as const)('cancels retry while refreshing its guard on %s', async action => {
  const { useWorkspaceStore } = await import('./workspace')
  const s = useChatStore(); s.selectedProviderId = 'real'; s.selectedModel = 'model'
  await s.sendMessage('original')
  const user = { ...s.messages[0]! }
  vi.mocked(streamChat).mock.calls[0]![1].onDone?.()
  const pending = deferred<Awaited<ReturnType<typeof loadConversationWindow>>>()
  vi.mocked(loadConversationWindow).mockReturnValueOnce(pending.promise)
  const retrying = s.retryMessage(user.message_id, 'edited')
  expect(s.windowBusy).toBe(true)
  if (action === 'stop') s.stopGeneration()
  else if (action === 'switch') await s.createNewConversation()
  else useWorkspaceStore().vaultId = 'another-vault'
  pending.resolve(windowOf([user]))
  await retrying
  expect(streamChat).toHaveBeenCalledTimes(1)
  expect(s.isStreaming).toBe(false)
})

it('does not retry with the stale guard when its refresh fails', async () => {
  const s = useChatStore(); s.selectedProviderId = 'real'; s.selectedModel = 'model'
  await s.sendMessage('original')
  const userId = s.messages[0]!.message_id
  vi.mocked(streamChat).mock.calls[0]![1].onDone?.()
  vi.mocked(loadConversationWindow).mockRejectedValueOnce(new Error('history unavailable'))
  await s.retryMessage(userId, 'edited')
  expect(streamChat).toHaveBeenCalledTimes(1)
  expect(s.historyError).toBe('history unavailable')
  expect(s.canSend).toBe(true)
})
