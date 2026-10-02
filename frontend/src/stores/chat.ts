import { computed, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import type { ChatMessage, Citation, Conversation, WorkspaceContext } from '@/contracts'
import {
  createConversation as createConversationApi,
  loadConversationWindow,
  listConversations as listConversationsApi,
  removeConversation,
  streamChat,
  decideChatBudget,
  selectMessageVersion,
} from '@/services/chatService'
import type { ChatWindow } from '@/services/chatService'
import { useWorkspaceStore } from './workspace'
import type { SseClient } from '@/services/sseClient'
import { t } from '@/i18n'
import { mediaService } from '@/services/mediaService'

const emptyWindow = (): ChatWindow => ({ items: [], total: 0, start: 0, branch_leaf: null, active_leaf: null, before: null, after: null })
export interface ChatReadingSnapshot { conversation: string | null; vault: string | null; selection: number; window: ChatWindow }

export const useChatStore = defineStore('chat', () => {
  const workspace = useWorkspaceStore()
  type PendingBudget = { requestId: string; conversationId: string; assistantMessageId: string;
    usage: number; budget: number; minimumAdditional: number; estimated: boolean; reason: string }
  const conversations = ref<Conversation[]>([])
  const activeConversationId = ref<string | null>(null)
  const messages = ref<ChatMessage[]>([])
  const messageWindow = ref<ChatWindow>(emptyWindow())
  const windowBusy = ref(false)
  const liveMessage = ref<ChatMessage>()
  const atLatest = ref(true)
  const selectedLeaf = ref<string | null>(null)
  let windowVersion = 0, selectionVersion = 0, tailNeedsRefresh = false, completedLive = false
  const isStreaming = ref(false)
  const pendingBudget = ref<PendingBudget | null>(null)
  const budgetDecisionBusy = ref(false)
  const budgetError = ref('')
  const isPreparing = ref(false)
  const messagesReady = ref(true)
  const deletingConversations = reactive(new Set<string>())
  const canSend = computed(() => messagesReady.value && !windowBusy.value && !isPreparing.value && !isStreaming.value && !uploading.value
    && (!activeConversationId.value || !deletingConversations.has(activeConversationId.value)))
  const uploading = ref(false)
  const pendingAttachments = ref<{attachment_id:string;name:string}[]>([])
  const imageFallbackTools = ref<string[]>(['',''])
  async function uploadFiles(files: File[]) {
    if (uploading.value || isStreaming.value) return
    uploading.value=true; historyError.value=''
    const conversationId=activeConversationId.value
    const vault=workspace.vaultId
    try {
      for (const file of files) {
        if (pendingAttachments.value.length >= 8) throw new Error('每次最多上传 8 个附件')
        const saved = await mediaService.upload(file, crypto.randomUUID())
        if (activeConversationId.value !== conversationId || vault !== workspace.vaultId) return
        pendingAttachments.value.push({...saved,name:file.name})
      }
    } catch(error) { historyError.value=error instanceof Error ? error.message : '上传失败' }
    finally { uploading.value=false }
  }
  const inputText = ref('')
  const useRag = ref(true)
  const allowAgent = ref(false)
  const selectedSkillId = ref<string | null>(null)
  const selectedProviderId = ref('')
  const selectedModel = ref('')
  const historyError = ref('')
  const contextNotice = ref('')
  let initialized = false
  let loading: Promise<void> | null = null
  let loadVersion = 0
  let sseClient: SseClient | null = null
  let streamVersion = 0
  const pendingCreates = new Map<string, Promise<void>>()

  const activeConversation = computed(() =>
    conversations.value.find(item => item.conversation_id === activeConversationId.value) || null
  )
  const sortedConversations = computed(() =>
    [...conversations.value].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  )

  function normalizeMessage(message: ChatMessage): ChatMessage {
    return {
      ...message,
      citations: message.citations?.map(citation => ({
        ...citation,
        heading_path: Array.isArray(citation.heading_path)
          ? citation.heading_path.join(' / ')
          : citation.heading_path,
      } as Citation)),
    }
  }

  async function fetchAllConversations() {
    const items: Conversation[] = []
    while (true) {
      const result = await listConversationsApi(items.length, 100)
      items.push(...result.items)
      if (!result.items.length || items.length >= result.page.total) return items
    }
  }

  function applyWindow(result: ChatWindow) {
    const items = result.items.map(normalizeMessage)
    const olderSnapshot = completedLive && liveMessage.value && result.active_leaf === liveMessage.value.parent_message_id
    atLatest.value = !result.after && (result.branch_leaf === result.active_leaf || Boolean(olderSnapshot && result.branch_leaf === liveMessage.value?.parent_message_id))
    if (!olderSnapshot) selectedLeaf.value = result.active_leaf
    if (olderSnapshot) tailNeedsRefresh = true
    if ((isStreaming.value || olderSnapshot) && atLatest.value && liveMessage.value && !items.some(m => m.message_id === liveMessage.value!.message_id)) items.push(liveMessage.value)
    const removed = Math.max(0, items.length - 60)
    messages.value = items.slice(removed)
    messageWindow.value = { ...result, items: [], start: result.start + removed }
    messagesReady.value = true
  }

  async function readWindow(params: Parameters<typeof loadConversationWindow>[1] = {}, propagate = false) {
    const id = activeConversationId.value, vault = workspace.vaultId, load = loadVersion
    if (!id) return false
    const version = ++windowVersion
    windowBusy.value = true
    try {
      const result = await loadConversationWindow(id, params)
      if (version !== windowVersion || load !== loadVersion || id !== activeConversationId.value || vault !== workspace.vaultId) return false
      applyWindow(result)
      historyError.value = ''
      return true
    } catch (error) {
      if (version !== windowVersion || load !== loadVersion || vault !== workspace.vaultId) return false
      if (propagate) throw error
      historyError.value = error instanceof Error ? error.message : t('消息加载失败', 'Failed to load messages')
      return false
    } finally { if (version === windowVersion) windowBusy.value = false }
  }

  async function loadEarlier() {
    if (windowBusy.value || !messageWindow.value.before) return false
    return readWindow({ cursor: messageWindow.value.before })
  }
  async function loadLater() {
    if (windowBusy.value || !messageWindow.value.after) return false
    return readWindow({ cursor: messageWindow.value.after })
  }
  async function showLatest() {
    if (atLatest.value && !tailNeedsRefresh) return true
    const loaded = await readWindow()
    if (loaded) tailNeedsRefresh = false
    return loaded
  }
  function captureReading(): ChatReadingSnapshot {
    return { conversation: activeConversationId.value, vault: workspace.vaultId, selection: selectionVersion,
      window: { ...messageWindow.value, items: [...messages.value] } }
  }
  async function locateMessage(messageId: string) {
    if (messages.value.some(m => m.message_id === messageId)) return true
    if (messageId === liveMessage.value?.message_id) return showLatest()
    try { return await readWindow({ around: messageId, ...(messageWindow.value.branch_leaf ? { branch_leaf: messageWindow.value.branch_leaf } : {}) }, true) }
    catch (error) {
      if ((error as { code?: string }).code !== 'CHAT_MESSAGE_OUTSIDE_BRANCH' || !canSend.value) {
        historyError.value = error instanceof Error ? error.message : 'Message location failed'; return false
      }
      return switchVersion(messageId, true)
    }
  }
  async function restoreReading(snapshot: ChatReadingSnapshot) {
    const id = activeConversationId.value, version = loadVersion
    if (snapshot.conversation !== id || snapshot.vault !== workspace.vaultId || !id) return false
    if (selectionVersion !== snapshot.selection && snapshot.window.branch_leaf) {
      if (!canSend.value) return false
      try { await selectMessageVersion(id, snapshot.window.branch_leaf) }
      catch (error) { historyError.value = error instanceof Error ? error.message : 'Branch restore failed'; return false }
      if (version !== loadVersion || snapshot.vault !== workspace.vaultId) return false
      selectionVersion++
      selectedLeaf.value = snapshot.window.active_leaf
      tailNeedsRefresh = true
    }
    windowVersion++
    windowBusy.value = false
    messages.value = [...snapshot.window.items]
    messageWindow.value = { ...snapshot.window, items: [] }
    atLatest.value = !snapshot.window.after && (snapshot.window.branch_leaf === selectedLeaf.value || Boolean(liveMessage.value && messages.value.some(m => m.message_id === liveMessage.value!.message_id)))
    return true
  }

  async function loadConversations(force = false) {
    if (loading) return loading
    if (initialized && !force) return
    const version = ++loadVersion
    messagesReady.value = false
    let pending: Promise<void> | null = null
    pending = (async () => {
      historyError.value = ''
    contextNotice.value = ''
      try {
        const items = await fetchAllConversations()
        if (version !== loadVersion) return
        conversations.value = items
        initialized = true
        const selected = activeConversationId.value && items.some(item => item.conversation_id === activeConversationId.value)
          ? activeConversationId.value
          : items[0]?.conversation_id || null
        if (selected) await setActiveConversation(selected)
        else { activeConversationId.value = null; messages.value = []; messagesReady.value = true }
      } catch (error) {
        if (version === loadVersion) historyError.value = error instanceof Error ? error.message : t('聊天记录加载失败', 'Failed to load chat history')
      } finally {
        if (loading === pending) loading = null
      }
    })()
    loading = pending
    return loading
  }

  async function setActiveConversation(id: string) {
    stopGeneration()
    pendingAttachments.value=[]
    const version = ++loadVersion
    activeConversationId.value = id
    messagesReady.value = false
    messages.value = []
    messageWindow.value = emptyWindow()
    liveMessage.value = undefined
    completedLive = false
    selectedLeaf.value = null
    tailNeedsRefresh = false
    historyError.value = ''
    contextNotice.value = ''
    try {
      await readWindow()
    } catch (error) {
      if (version === loadVersion) historyError.value = error instanceof Error ? error.message : t('消息加载失败', 'Failed to load messages')
    }
  }

  function addLocalConversation(title: string) {
    loadVersion++
    windowVersion++; windowBusy.value = false
    const now = new Date().toISOString()
    const conversation: Conversation = {
      conversation_id: crypto.randomUUID(), title, created_at: now, updated_at: now, message_count: 0,
    }
    conversations.value.unshift(conversation)
    activeConversationId.value = conversation.conversation_id
    messages.value = []
    messageWindow.value = emptyWindow(); selectedLeaf.value = null; atLatest.value = true; liveMessage.value = undefined; tailNeedsRefresh = false; completedLive = false
    messagesReady.value = true
    return conversation
  }

  async function persistConversation(conversation: Conversation) {
    const vault = workspace.vaultId
    const promise = createConversationApi(conversation).then(saved => {
      if (vault !== workspace.vaultId) return
      const index = conversations.value.findIndex(item => item.conversation_id === saved.conversation_id)
      if (index >= 0) Object.assign(conversations.value[index]!, saved)
    }).catch(error => {
      if (vault !== workspace.vaultId) throw error
      conversations.value = conversations.value.filter(item => item.conversation_id !== conversation.conversation_id)
      if (activeConversationId.value === conversation.conversation_id) {
        activeConversationId.value = null
        messages.value = []
      }
      historyError.value = error instanceof Error ? error.message : t('会话创建失败', 'Failed to create conversation')
      throw error
    }).finally(() => pendingCreates.delete(conversation.conversation_id))
    pendingCreates.set(conversation.conversation_id, promise)
    return promise
  }

  async function createNewConversation() {
    stopGeneration()
    pendingAttachments.value=[]
    historyError.value = ''
    contextNotice.value = ''
    const conversation = addLocalConversation(t('新对话', 'New conversation'))
    try { await persistConversation(conversation) } catch { /* 通过historyError暴露 */ }
  }

  async function sendMessage(text: string, retryMessageId?: string, workspaceContext?: WorkspaceContext | null) {
    const content = text.trim() || (pendingAttachments.value.length ? '请分析附件内容' : '')
    if (!content || !canSend.value || !selectedProviderId.value || !selectedModel.value) return
    const readingConversation = activeConversationId.value, readingVault = workspace.vaultId
    if (readingConversation && !retryMessageId && (!atLatest.value || tailNeedsRefresh) && !await showLatest()) return
    if (readingConversation !== activeConversationId.value || readingVault !== workspace.vaultId || !canSend.value) return
    const targetIndex = retryMessageId ? messages.value.findIndex(m => m.message_id === retryMessageId) : messages.value.length - 1
    if (retryMessageId && targetIndex < 0) return
    const target = messages.value[targetIndex]
    const source = target?.role === 'assistant' && !target.context_captured
      ? messages.value[targetIndex - 1] : target
    const context = workspaceContext === undefined ? source?.workspace_context : workspaceContext
    const snapshot = context ? { ...context } : undefined
    const attachments = !retryMessageId && pendingAttachments.value.length
      ? pendingAttachments.value.map(a => a.attachment_id) : [...(source?.attachments ?? [])]
    const version = ++streamVersion
    isPreparing.value = true
    historyError.value = ''
    contextNotice.value = ''
    let conversation = activeConversation.value
    try {
      if (!conversation) {
        conversation = addLocalConversation(content.slice(0, 30))
        await persistConversation(conversation)
      } else if (pendingCreates.has(conversation.conversation_id)) {
        await pendingCreates.get(conversation.conversation_id)
      }
    } catch { return }
    finally {
      if (version === streamVersion) isPreparing.value = false
    }
    // 切换、停止或删除会取消仍在等待创建的发送。
    if (version !== streamVersion || activeConversationId.value !== conversation.conversation_id) return

    const conversationId = conversation.conversation_id
    if (conversation.message_count === 0) conversation.title = content.slice(0, 30)
    const retryIndex = retryMessageId ? messages.value.findIndex(m => m.message_id === retryMessageId) : -1
    const retryTarget = retryIndex >= 0 ? messages.value[retryIndex] : undefined
    if (retryMessageId && !retryTarget) return
    const originalMessages = retryTarget ? [...messages.value] : null
    const regenerate = retryTarget?.role === 'assistant'
    const userMsg: ChatMessage = regenerate ? messages.value[retryIndex - 1]! : {
      message_id: crypto.randomUUID(), conversation_id: conversationId, role: 'user', content, workspace_context: snapshot, attachments,
      created_at: new Date().toISOString(),
    }
    const aiMsg = reactive<ChatMessage>({
      message_id: crypto.randomUUID(), conversation_id: conversationId, role: 'assistant', content: '',
      created_at: new Date().toISOString(), citations: [], tool_calls: [], activity: [],
      context_captured: true, workspace_context: snapshot, attachments: [...attachments],
      parent_message_id: userMsg.message_id,
    })
    const expectedLeaf = selectedLeaf.value
    if (retryTarget) {
      messages.value = messages.value.slice(0, retryIndex)
      const newVersion = regenerate ? aiMsg : userMsg
      newVersion.versions = [...(retryTarget.versions?.length ? retryTarget.versions : [retryTarget.message_id]), newVersion.message_id]
    }
    if (!regenerate) messages.value.push(userMsg)
    messages.value.push(aiMsg)
    liveMessage.value = aiMsg
    completedLive = false
    atLatest.value = true
    messageWindow.value.total = messageWindow.value.start + messages.value.length
    messageWindow.value.after = null
    const removed = Math.max(0, messages.value.length - 60)
    if (removed) { messages.value = messages.value.slice(removed); messageWindow.value.start += removed }
    inputText.value = ''
    if (!retryMessageId) pendingAttachments.value = []
    isStreaming.value = true
    pendingBudget.value = null
    budgetError.value = ''
    conversation.updated_at = new Date().toISOString()
    conversation.message_count += regenerate ? 1 : 2

    const argumentBuffers = new Map<string, string>()
    const seenEvents = new Set<string>()
    sseClient = streamChat({
      provider_id: selectedProviderId.value,
      ...(retryMessageId ? { retry_message_id: retryMessageId } : {}),
      model: selectedModel.value,
      conversation_id: conversationId,
      user_message_id: userMsg.message_id,
      assistant_message_id: aiMsg.message_id,
      conversation_title: conversation.title,
      use_saved_history: true,
      expected_branch_leaf: expectedLeaf,
      use_rag: useRag.value,
      allow_agent: allowAgent.value,
      attachments, image_fallback_tools: imageFallbackTools.value.filter(Boolean),
      workspace_context: snapshot,
      messages: [{ role: 'user', content: userMsg.content }],
    }, {
      onEvent(event) {
        if (version !== streamVersion) return
        const identity = JSON.stringify([event.sequence, event.event, event.data])
        if (seenEvents.has(identity)) return
        seenEvents.add(identity)
        if (event.event === 'TextDelta') {
          const text = String(event.data.text ?? '')
          aiMsg.content += text
          const last = aiMsg.activity?.at(-1)
          if (last?.type === 'text') last.text += text
          else aiMsg.activity?.push({ type: 'text', text, sequence: event.sequence })
        }
        if (event.event === 'ThinkingDelta') {
          const text = String(event.data.text ?? '')
          aiMsg.thinking = `${aiMsg.thinking ?? ''}${text}`
          const last = aiMsg.activity?.at(-1)
          if (last?.type === 'thinking') last.text += text
          else aiMsg.activity?.push({ type: 'thinking', text, sequence: event.sequence })
        }
        if (event.event === 'ToolCallStart') {
          if (aiMsg.tool_calls?.some(call => call.tool_call_id === event.data.tool_call_id)) return
          aiMsg.activity?.push({ type: 'tool', tool_call_id: String(event.data.tool_call_id ?? ''), sequence: event.sequence })
          aiMsg.tool_calls?.push({
            tool_call_id: String(event.data.tool_call_id ?? ''), name: String(event.data.name ?? 'unknown'),
            parameters: (event.data.arguments ?? {}) as Record<string, unknown>, status: 'running',
          })
        }
        if (event.event === 'ToolCallDelta') {
          const call = aiMsg.tool_calls?.find(item => item.tool_call_id === event.data.tool_call_id)
          if (call && event.data.arguments && typeof event.data.arguments === 'object') call.parameters = event.data.arguments as Record<string, unknown>
          if (call && event.data.result && typeof event.data.result === 'object') call.result = JSON.stringify(event.data.result)
          if (call && typeof event.data.arguments_delta === 'string') {
            const buffer = (argumentBuffers.get(call.tool_call_id) ?? '') + event.data.arguments_delta
            argumentBuffers.set(call.tool_call_id, buffer)
            try { call.parameters = JSON.parse(buffer) } catch { /* 不完整的JSON片段 */ }
          }
          if (call && event.data.arguments && typeof event.data.arguments === 'object') Object.assign(call.parameters, event.data.arguments)
        }
        if (event.event === 'ToolCallEnd') {
          const call = aiMsg.tool_calls?.find(item => item.tool_call_id === event.data.tool_call_id)
          if (call) {
            call.status = event.data.status === 'failed' ? 'error' : 'completed'
            if (event.data.result) call.result = JSON.stringify(event.data.result)
          }
        }
        if (event.event === 'Usage') {
          const input = Number(event.data.input_tokens ?? 0)
          const output = Number(event.data.output_tokens ?? 0)
          aiMsg.usage = { input_tokens: input, output_tokens: output, total_tokens: input + output }
        }
        if (event.event === 'Citation') {
          aiMsg.citations?.push({
            citation_id: String(event.data.citation_id ?? ''), note_id: String(event.data.note_id ?? ''), block_id: String(event.data.block_id ?? ''),
            file_path: String(event.data.file_path ?? ''),
            heading_path: Array.isArray(event.data.heading_path) ? event.data.heading_path.join(' / ') : String(event.data.heading_path ?? ''),
            content: String(event.data.content ?? event.data.snippet ?? ''),
          })
        }
        if (event.event === 'ContextStatus') contextNotice.value = String(event.data.message ?? '')
        if (event.event === 'BudgetRequired') {
          pendingBudget.value = {
            requestId: String(event.data.request_id ?? ''), conversationId,
            assistantMessageId: aiMsg.message_id,
            usage: Number(event.data.token_usage ?? 0), budget: Number(event.data.token_budget ?? 0),
            minimumAdditional: Number(event.data.minimum_additional_tokens ?? 1),
            estimated: Boolean(event.data.estimated), reason: String(event.data.reason ?? 'coordination'),
          }
          budgetError.value = ''
        }
        if (event.event === 'BudgetResolved') pendingBudget.value = null
        if (event.event === 'Error') {
          const text = `\n\n${t('生成失败：', 'Generation failed: ')}${String(event.data.message ?? t('未知错误', 'Unknown error'))}`
          aiMsg.content += text
          aiMsg.activity?.push({ type: 'text', text, sequence: event.sequence })
          aiMsg.tool_calls?.filter(call => call.status === 'running').forEach(call => { call.status = 'error'; call.error_message = t('响应中断，请核对实际运行状态。', 'Response interrupted; check the actual run status.') })
        }
      },
      onError(error) {
        if (version !== streamVersion) return
        const text = `\n\n${t('连接失败：', 'Connection failed: ')}${error.message}`
        aiMsg.content += text
        aiMsg.activity?.push({ type: 'text', text })
        aiMsg.tool_calls?.filter(call => call.status === 'running').forEach(call => { call.status = 'error'; call.error_message = t('连接中断，请核对实际运行状态。', 'Connection interrupted; check the actual run status.') })
        if (originalMessages) historyError.value = t('重试连接失败，可切换版本恢复原回复。', 'Retry connection failed. Switch versions to return to the original reply.')
        isStreaming.value = false
        tailNeedsRefresh = true
        pendingBudget.value = null
        sseClient = null
      },
      onDone() {
        if (version !== streamVersion) return
        conversation!.updated_at = new Date().toISOString()
        selectedLeaf.value = aiMsg.message_id
        completedLive = true
        tailNeedsRefresh = true
        if (atLatest.value) messageWindow.value.branch_leaf = aiMsg.message_id
        else tailNeedsRefresh = true
        isStreaming.value = false
        pendingBudget.value = null
        sseClient = null
      },
    })
  }

  async function retryMessage(messageId: string, editedText?: string, workspaceContext?: WorkspaceContext | null) {
    if (!canSend.value) return
    let index = messages.value.findIndex(m => m.message_id === messageId)
    const message = messages.value[index]
    if (!message) return
    if (message.role === 'assistant' && index === 0 && message.parent_message_id) {
      if (!await readWindow({ around: message.parent_message_id, ...(messageWindow.value.branch_leaf ? { branch_leaf: messageWindow.value.branch_leaf } : {}) })) return
      index = messages.value.findIndex(m => m.message_id === messageId)
    }
    const text = message.role === 'user' ? editedText : messages.value[index - 1]?.content
    if (text?.trim()) await sendMessage(text, messageId, workspaceContext)
  }

  async function switchVersion(messageId: string, around = false) {
    const id = activeConversationId.value
    if (!canSend.value || !id) return false
    const version = loadVersion
    isPreparing.value = true
    try {
      await selectMessageVersion(id, messageId)
      if (activeConversationId.value !== id || loadVersion !== version) return false
      selectionVersion++
      return await readWindow(around ? { around: messageId } : {})
    } catch (error) { historyError.value = error instanceof Error ? error.message : 'Version switch failed' }
    finally { isPreparing.value = false }
    return false
  }

  function stopGeneration() {
    if (isStreaming.value) tailNeedsRefresh = true
    streamVersion++
    isPreparing.value = false
    if (sseClient) { sseClient.cancel(); sseClient = null }
    isStreaming.value = false
    pendingBudget.value = null
  }

  watch(() => workspace.vaultId, () => {
    const reload = initialized
    loadVersion++; windowVersion++; selectionVersion++
    stopGeneration()
    initialized = false; loading = null
    conversations.value = []; activeConversationId.value = null; messages.value = []
    messageWindow.value = emptyWindow(); liveMessage.value = undefined; windowBusy.value = false
    selectedLeaf.value = null; atLatest.value = true; messagesReady.value = true
    pendingAttachments.value = []; historyError.value = ''; contextNotice.value = ''
    if (reload && workspace.vaultId) void loadConversations(true)
  }, { flush: 'sync' })

  async function resolveBudget(additionalTokens: number) {
    const pending = pendingBudget.value
    if (!pending || budgetDecisionBusy.value || !Number.isInteger(additionalTokens) || additionalTokens < 0 || additionalTokens > 1000000) return
    if (additionalTokens > 0 && additionalTokens < pending.minimumAdditional) return
    budgetDecisionBusy.value = true
    budgetError.value = ''
    try {
      await decideChatBudget(pending.requestId, pending.conversationId, pending.assistantMessageId, additionalTokens)
      if (pendingBudget.value?.requestId === pending.requestId) pendingBudget.value = null
    } catch (cause) {
      budgetError.value = cause instanceof Error ? cause.message : String(cause)
    } finally { budgetDecisionBusy.value = false }
  }

  async function deleteConversation(id: string) {
    if (deletingConversations.has(id)) return
    deletingConversations.add(id)
    const vault = workspace.vaultId
    if (activeConversationId.value === id) stopGeneration()
    historyError.value = ''
    contextNotice.value = ''
    try {
      if (pendingCreates.has(id)) await pendingCreates.get(id)
      await removeConversation(id)
      if (vault !== workspace.vaultId) return
      conversations.value = conversations.value.filter(item => item.conversation_id !== id)
      if (activeConversationId.value === id) {
        const next = sortedConversations.value[0]
        if (next) await setActiveConversation(next.conversation_id)
        else { loadVersion++; activeConversationId.value = null; messages.value = []; messagesReady.value = true }
      }
    } catch (error) {
      if (vault === workspace.vaultId) historyError.value = error instanceof Error ? error.message : t('会话删除失败', 'Failed to delete conversation')
    } finally {
      deletingConversations.delete(id)
    }
  }

  return {
    uploading, pendingAttachments, imageFallbackTools, uploadFiles,
    conversations, activeConversationId, activeConversation, sortedConversations, messages,
    messageWindow, windowBusy, liveMessage, atLatest, loadEarlier, loadLater, showLatest, locateMessage, captureReading, restoreReading,
    isStreaming, isPreparing, canSend, inputText, useRag, allowAgent, selectedSkillId, selectedProviderId, selectedModel, historyError, contextNotice,
    pendingBudget, budgetDecisionBusy, budgetError, resolveBudget,
    loadConversations, setActiveConversation, sendMessage, stopGeneration, createNewConversation, deleteConversation, retryMessage, switchVersion,
  }
})
