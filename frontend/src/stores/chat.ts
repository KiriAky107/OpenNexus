import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ChatMessage, Conversation } from '@/contracts'
import { mockConversations, mockMessages, streamChat } from '@/services/chatService'
import type { SseClient } from '@/services/sseClient'

export const useChatStore = defineStore('chat', () => {
  const conversations = ref<Conversation[]>(mockConversations)
  const activeConversationId = ref<string | null>('conv-1')
  const messages = ref<ChatMessage[]>(mockMessages['conv-1'] || [])
  const isStreaming = ref(false)
  const inputText = ref('')
  const useRag = ref(true)
  const selectedSkillId = ref<string | null>(null)
  const selectedProviderId = ref('mock')
  const selectedModel = ref('mock-1')
  let sseClient: SseClient | null = null

  const activeConversation = computed(() =>
    conversations.value.find((c) => c.conversation_id === activeConversationId.value) || null
  )

  const sortedConversations = computed(() =>
    [...conversations.value].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
  )

  async function setActiveConversation(id: string) {
    activeConversationId.value = id
    messages.value = mockMessages[id] || []
  }

  async function sendMessage(text: string) {
    if (!text.trim() || isStreaming.value) return
    const conversationId = activeConversationId.value || `conv-${Date.now()}`

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

    const userMsg: ChatMessage = {
      message_id: `msg-${Date.now()}`,
      conversation_id: conversationId,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    messages.value.push(userMsg)
    inputText.value = ''
    isStreaming.value = true

    const aiMsg: ChatMessage = {
      message_id: `msg-${Date.now() + 1}`,
      conversation_id: conversationId,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
      citations: [],
      tool_calls: [],
    }
    messages.value.push(aiMsg)

    sseClient = streamChat({
      provider_id: selectedProviderId.value,
      model: selectedModel.value,
      conversation_id: conversationId,
      use_rag: useRag.value,
      messages: messages.value
        .filter((message) => message !== aiMsg)
        .map((message) => ({ role: message.role, content: message.content })),
    }, {
      onEvent(event) {
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
        aiMsg.content += `\n\n连接失败：${error.message}`
        isStreaming.value = false
        sseClient = null
      },
      onDone() {
        const conversation = conversations.value.find((item) => item.conversation_id === conversationId)
        if (conversation) {
          conversation.message_count = messages.value.length
          conversation.updated_at = new Date().toISOString()
        }
        isStreaming.value = false
        sseClient = null
      },
    })
  }

  function stopGeneration() {
    if (sseClient) {
      sseClient.cancel()
      sseClient = null
    }
    isStreaming.value = false
  }

  function createNewConversation() {
    const newConv: Conversation = {
      conversation_id: `conv-${Date.now()}`,
      title: '新对话',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      message_count: 0,
    }
    conversations.value.unshift(newConv)
    activeConversationId.value = newConv.conversation_id
    messages.value = []
  }

  function deleteConversation(id: string) {
    const idx = conversations.value.findIndex((c) => c.conversation_id === id)
    if (idx > -1) {
      conversations.value.splice(idx, 1)
      if (activeConversationId.value === id) {
        activeConversationId.value = conversations.value[0]?.conversation_id || null
        messages.value = conversations.value[0] ? mockMessages[conversations.value[0].conversation_id] || [] : []
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
