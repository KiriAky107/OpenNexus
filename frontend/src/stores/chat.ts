import { defineStore } from 'pinia'
import { ref, computed, reactive } from 'vue'
import type { ChatMessage, Conversation } from '@/contracts'
import { streamChat } from '@/services/chatService'
import type { SseClient } from '@/services/sseClient'

export const useChatStore = defineStore('chat', () => {
  const conversations = ref<Conversation[]>([])
  const activeConversationId = ref<string | null>(null)
  const messages = ref<ChatMessage[]>([])
  const isStreaming = ref(false)
  const inputText = ref('')
  const useRag = ref(false)
  const selectedSkillId = ref<string | null>(null)
  const selectedProviderId = ref('')
  const selectedModel = ref('')
  let sseClient: SseClient | null = null
  let streamVersion = 0

  // User-created conversations live in this browser session; no fabricated history.
  const history = reactive<Record<string, ChatMessage[]>>({})

  const activeConversation = computed(() =>
    conversations.value.find((c) => c.conversation_id === activeConversationId.value) || null
  )

  const sortedConversations = computed(() =>
    [...conversations.value].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  )

  async function setActiveConversation(id: string) {
    stopGeneration()
    activeConversationId.value = id
    messages.value = history[id] ?? []
  }

  async function sendMessage(text: string) {
    if (!text.trim() || isStreaming.value || !selectedProviderId.value || !selectedModel.value) return
    const conversationId = activeConversationId.value || crypto.randomUUID()

    if (!activeConversationId.value) {
      const newConv: Conversation = {
        conversation_id: conversationId,
        title: text.slice(0, 30),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        message_count: 0,
      }
      conversations.value.unshift(newConv)
      activeConversationId.value = conversationId
    }

    history[conversationId] = messages.value
    const conversationMessages = messages.value
    const userMsg: ChatMessage = {
      message_id: crypto.randomUUID(),
      conversation_id: conversationId,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    messages.value.push(userMsg)
    inputText.value = ''
    isStreaming.value = true
    const conversation = conversations.value.find(c => c.conversation_id === conversationId)
    if (conversation) { conversation.updated_at = new Date().toISOString(); conversation.message_count = messages.value.length }

    // 先插入占位消息，随后将 SSE 增量原位合并，避免每个 token 重建消息列表。
    const aiMsg = reactive<ChatMessage>({
      message_id: crypto.randomUUID(),
      conversation_id: conversationId,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
      citations: [],
      tool_calls: [],
    })
    messages.value.push(aiMsg)

    const version = ++streamVersion
    const argumentBuffers = new Map<string, string>()
    sseClient = streamChat({
      provider_id: selectedProviderId.value,
      model: selectedModel.value,
      conversation_id: conversationId,
      use_rag: useRag.value,
      messages: messages.value
        .filter((message) => message.message_id !== aiMsg.message_id)
        .map((message) => ({ role: message.role, content: message.content })),
    }, {
      onEvent(event) {
        if (version !== streamVersion) return
        if (event.event === 'TextDelta') aiMsg.content += String(event.data.text ?? '')
        if (event.event === 'ThinkingDelta') aiMsg.thinking = `${aiMsg.thinking ?? ''}${String(event.data.text ?? '')}`
        if (event.event === 'ToolCallStart') {
          aiMsg.tool_calls?.push({
            tool_call_id: String(event.data.tool_call_id ?? ''),
            name: String(event.data.name ?? 'unknown'),
            parameters: (event.data.arguments ?? {}) as Record<string, unknown>,
            status: 'running',
          })
        }
        if (event.event === 'ToolCallDelta') {
          const call = aiMsg.tool_calls?.find((item) => item.tool_call_id === event.data.tool_call_id)
          if (call && typeof event.data.arguments_delta === 'string') {
            const buffer = (argumentBuffers.get(call.tool_call_id) ?? '') + event.data.arguments_delta
            argumentBuffers.set(call.tool_call_id, buffer)
            try { call.parameters = JSON.parse(buffer) } catch { /* incomplete JSON fragment */ }
          }
          if (call && event.data.arguments && typeof event.data.arguments === 'object') {
            Object.assign(call.parameters, event.data.arguments)
          }
        }
        if (event.event === 'ToolCallEnd') {
          const call = aiMsg.tool_calls?.find((item) => item.tool_call_id === event.data.tool_call_id)
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
        if (event.event === 'Error') aiMsg.content += `\n\n生成失败：${String(event.data.message ?? '未知错误')}`
      },
      onError(error) {
        if (version !== streamVersion) return
        aiMsg.content += `\n\n连接失败：${error.message}`
        isStreaming.value = false
        sseClient = null
      },
      onDone() {
        if (version !== streamVersion) return
        const conversation = conversations.value.find((item) => item.conversation_id === conversationId)
        if (conversation) {
          conversation.message_count = conversationMessages.length
          conversation.updated_at = new Date().toISOString()
        }
        isStreaming.value = false
        sseClient = null
      },
    })
  }

  function stopGeneration() {
    streamVersion++
    if (sseClient) {
      sseClient.cancel()
      sseClient = null
    }
    isStreaming.value = false
  }

  function createNewConversation() {
    stopGeneration()
    const newConv: Conversation = {
      conversation_id: crypto.randomUUID(),
      title: '新对话',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      message_count: 0,
    }
    conversations.value.unshift(newConv)
    activeConversationId.value = newConv.conversation_id
    history[newConv.conversation_id] = []
    messages.value = history[newConv.conversation_id]
  }

  function deleteConversation(id: string) {
    if (activeConversationId.value === id) stopGeneration()
    delete history[id]
    const idx = conversations.value.findIndex((c) => c.conversation_id === id)
    if (idx > -1) {
      conversations.value.splice(idx, 1)
      if (activeConversationId.value === id) {
        activeConversationId.value = conversations.value[0]?.conversation_id || null
        messages.value = conversations.value[0] ? history[conversations.value[0].conversation_id] || [] : []
      }
    }
  }

  return {
    conversations,
    activeConversationId,
    activeConversation,
    sortedConversations,
    messages,
    isStreaming,
    inputText,
    useRag,
    selectedSkillId,
    selectedProviderId,
    selectedModel,
    setActiveConversation,
    sendMessage,
    stopGeneration,
    createNewConversation,
    deleteConversation,
  }
})
