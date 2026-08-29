import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ChatMessage, Conversation, Citation } from '@/contracts'
import { mockConversations, mockMessages } from '@/services/chatService'
import type { SseClient } from '@/services/sseClient'

export const useChatStore = defineStore('chat', () => {
  const conversations = ref<Conversation[]>(mockConversations)
  const activeConversationId = ref<string | null>('conv-1')
  const messages = ref<ChatMessage[]>(mockMessages['conv-1'] || [])
  const isStreaming = ref(false)
  const inputText = ref('')
  const useRag = ref(true)
  const selectedSkillId = ref<string | null>(null)
  const selectedProviderId = ref('mock-provider')
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
        conversation_id,
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

    // Mock streaming
    const fullText =
      '这是一个模拟的 AI 回复。在实际环境中，这里会通过 SSE 接收后端 AI Core 的流式输出，基于 RAG 引擎和你的知识库生成回答，并附带来源引用。\n\n**要点总结：**\n1. 这是演示用的流式输出\n2. 实际会调用 ModelEvent SSE\n3. 支持 Citation、Tool Call 等事件\n\n你可以在设置中配置真实的模型 Provider 来启用完整功能。'
    const citations: Citation[] = [
      {
        note_id: 'n-rbt',
        block_id: 'b1',
        file_path: '/数据结构/红黑树.md',
        heading_path: '数据结构 / 红黑树 / 概述',
        content: '红黑树是一种自平衡二叉搜索树...',
      },
    ]

    let i = 0
    const interval = setInterval(() => {
      if (i >= fullText.length) {
        clearInterval(interval)
        isStreaming.value = false
        aiMsg.citations = citations
        return
      }
      const chunk = fullText.slice(i, i + 3)
      aiMsg.content += chunk
      i += 3
    }, 20)
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
