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
        if (event.event === 'Error') aiMsg.content += `\n\n生成失败：${String(event.data.message ?? '未知错误')}`
      },
      onError(error) {
        aiMsg.content += `\n\n连接失败：${error.message}`
        isStreaming.value = false
        sseClient = null
      },
      onDone() {
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
