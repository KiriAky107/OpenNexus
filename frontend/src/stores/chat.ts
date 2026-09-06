import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import type { ChatMessage, Citation, Conversation } from '@/contracts'
import {
  createConversation as createConversationApi,
  listConversationMessages,
  listConversations as listConversationsApi,
  removeConversation,
  streamChat,
} from '@/services/chatService'
import type { SseClient } from '@/services/sseClient'
import { t } from '@/i18n'

export const useChatStore = defineStore('chat', () => {
  const conversations = ref<Conversation[]>([])
  const activeConversationId = ref<string | null>(null)
  const messages = ref<ChatMessage[]>([])
  const isStreaming = ref(false)
  const isPreparing = ref(false)
  const messagesReady = ref(true)
  const deletingConversations = reactive(new Set<string>())
  const canSend = computed(() => messagesReady.value && !isPreparing.value && !isStreaming.value
    && (!activeConversationId.value || !deletingConversations.has(activeConversationId.value)))
  const inputText = ref('')
  const useRag = ref(true)
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

  async function fetchAllMessages(conversationId: string) {
    const items: ChatMessage[] = []
    while (true) {
      const result = await listConversationMessages(conversationId, items.length, 500)
      items.push(...result.items)
      if (!result.items.length || items.length >= result.page.total) return items
    }
  }

  async function loadConversations(force = false) {
    if (loading) return loading
    if (initialized && !force) return
    const version = ++loadVersion
    messagesReady.value = false
    loading = (async () => {
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
        loading = null
      }
    })()
    return loading
  }

  async function setActiveConversation(id: string) {
    stopGeneration()
    const version = ++loadVersion
    activeConversationId.value = id
    messagesReady.value = false
    messages.value = []
    historyError.value = ''
    contextNotice.value = ''
    try {
      const loadedMessages = await fetchAllMessages(id)
      if (version === loadVersion && activeConversationId.value === id) {
        messages.value = loadedMessages.map(normalizeMessage)
        messagesReady.value = true
      }
    } catch (error) {
      if (version === loadVersion) historyError.value = error instanceof Error ? error.message : t('消息加载失败', 'Failed to load messages')
    }
  }

  function addLocalConversation(title: string) {
    loadVersion++
    const now = new Date().toISOString()
    const conversation: Conversation = {
      conversation_id: crypto.randomUUID(), title, created_at: now, updated_at: now, message_count: 0,
    }
    conversations.value.unshift(conversation)
    activeConversationId.value = conversation.conversation_id
    messages.value = []
    messagesReady.value = true
    return conversation
  }

  async function persistConversation(conversation: Conversation) {
    const promise = createConversationApi(conversation).then(saved => {
      const index = conversations.value.findIndex(item => item.conversation_id === saved.conversation_id)
      if (index >= 0) Object.assign(conversations.value[index]!, saved)
    }).catch(error => {
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
    historyError.value = ''
    contextNotice.value = ''
    const conversation = addLocalConversation(t('新对话', 'New conversation'))
    try { await persistConversation(conversation) } catch { /* exposed through historyError */ }
  }

  async function sendMessage(text: string) {
    const content = text.trim()
    if (!content || !canSend.value || !selectedProviderId.value || !selectedModel.value) return
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
    // Switching, stopping or deleting cancels sends still waiting for creation.
    if (version !== streamVersion || activeConversationId.value !== conversation.conversation_id) return

    const conversationId = conversation.conversation_id
    if (conversation.message_count === 0) conversation.title = content.slice(0, 30)
    const userMsg: ChatMessage = {
      message_id: crypto.randomUUID(), conversation_id: conversationId, role: 'user', content,
      created_at: new Date().toISOString(),
    }
    const aiMsg = reactive<ChatMessage>({
      message_id: crypto.randomUUID(), conversation_id: conversationId, role: 'assistant', content: '',
      created_at: new Date().toISOString(), citations: [], tool_calls: [],
    })
    messages.value.push(userMsg, aiMsg)
    inputText.value = ''
    isStreaming.value = true
    conversation.updated_at = new Date().toISOString()
    conversation.message_count = messages.value.length

    const argumentBuffers = new Map<string, string>()
    sseClient = streamChat({
      provider_id: selectedProviderId.value,
      model: selectedModel.value,
      conversation_id: conversationId,
      user_message_id: userMsg.message_id,
      assistant_message_id: aiMsg.message_id,
      conversation_title: conversation.title,
      use_rag: useRag.value,
      messages: messages.value
        .filter(message => message.message_id !== aiMsg.message_id)
        .map(message => ({ role: message.role, content: message.content })),
    }, {
      onEvent(event) {
        if (version !== streamVersion) return
        if (event.event === 'TextDelta') aiMsg.content += String(event.data.text ?? '')
        if (event.event === 'ThinkingDelta') aiMsg.thinking = `${aiMsg.thinking ?? ''}${String(event.data.text ?? '')}`
        if (event.event === 'ToolCallStart') {
          aiMsg.tool_calls?.push({
            tool_call_id: String(event.data.tool_call_id ?? ''), name: String(event.data.name ?? 'unknown'),
            parameters: (event.data.arguments ?? {}) as Record<string, unknown>, status: 'running',
          })
        }
        if (event.event === 'ToolCallDelta') {
          const call = aiMsg.tool_calls?.find(item => item.tool_call_id === event.data.tool_call_id)
          if (call && typeof event.data.arguments_delta === 'string') {
            const buffer = (argumentBuffers.get(call.tool_call_id) ?? '') + event.data.arguments_delta
            argumentBuffers.set(call.tool_call_id, buffer)
            try { call.parameters = JSON.parse(buffer) } catch { /* incomplete JSON fragment */ }
          }
          if (call && event.data.arguments && typeof event.data.arguments === 'object') Object.assign(call.parameters, event.data.arguments)
        }
        if (event.event === 'ToolCallEnd') {
          const call = aiMsg.tool_calls?.find(item => item.tool_call_id === event.data.tool_call_id)
          if (call) call.status = 'completed'
        }
        if (event.event === 'Usage') {
          const input = Number(event.data.input_tokens ?? 0)
          const output = Number(event.data.output_tokens ?? 0)
          aiMsg.usage = { input_tokens: input, output_tokens: output, total_tokens: input + output }
        }
        if (event.event === 'Citation') {
          aiMsg.citations?.push({
            note_id: String(event.data.note_id ?? ''), block_id: String(event.data.block_id ?? ''),
            file_path: String(event.data.file_path ?? ''),
            heading_path: Array.isArray(event.data.heading_path) ? event.data.heading_path.join(' / ') : String(event.data.heading_path ?? ''),
            content: String(event.data.content ?? event.data.snippet ?? ''),
          })
        }
        if (event.event === 'ContextStatus') contextNotice.value = String(event.data.message ?? '')
        if (event.event === 'Error') aiMsg.content += `\n\n${t('生成失败：', 'Generation failed: ')}${String(event.data.message ?? t('未知错误', 'Unknown error'))}`
      },
      onError(error) {
        if (version !== streamVersion) return
        aiMsg.content += `\n\n${t('连接失败：', 'Connection failed: ')}${error.message}`
        isStreaming.value = false
        sseClient = null
      },
      onDone() {
        if (version !== streamVersion) return
        conversation!.message_count = messages.value.length
        conversation!.updated_at = new Date().toISOString()
        isStreaming.value = false
        sseClient = null
      },
    })
  }

  function stopGeneration() {
    streamVersion++
    isPreparing.value = false
    if (sseClient) { sseClient.cancel(); sseClient = null }
    isStreaming.value = false
  }

  async function deleteConversation(id: string) {
    if (deletingConversations.has(id)) return
    deletingConversations.add(id)
    if (activeConversationId.value === id) stopGeneration()
    historyError.value = ''
    contextNotice.value = ''
    try {
      if (pendingCreates.has(id)) await pendingCreates.get(id)
      await removeConversation(id)
      conversations.value = conversations.value.filter(item => item.conversation_id !== id)
      if (activeConversationId.value === id) {
        const next = sortedConversations.value[0]
        if (next) await setActiveConversation(next.conversation_id)
        else { loadVersion++; activeConversationId.value = null; messages.value = []; messagesReady.value = true }
      }
    } catch (error) {
      historyError.value = error instanceof Error ? error.message : t('会话删除失败', 'Failed to delete conversation')
    } finally {
      deletingConversations.delete(id)
    }
  }

  return {
    conversations, activeConversationId, activeConversation, sortedConversations, messages,
    isStreaming, isPreparing, canSend, inputText, useRag, selectedSkillId, selectedProviderId, selectedModel, historyError, contextNotice,
    loadConversations, setActiveConversation, sendMessage, stopGeneration, createNewConversation, deleteConversation,
  }
})
