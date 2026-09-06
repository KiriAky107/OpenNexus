import { SseClient } from './sseClient'
import { apiClient } from './apiClient'
import type { ChatMessage, Conversation, ModelEvent, PageMeta } from '@/contracts'

export interface ChatRequest {
  retry_message_id?: string
  provider_id: string
  model: string
  conversation_id?: string
  user_message_id?: string
  assistant_message_id?: string
  conversation_title?: string
  system?: string
  messages: Array<{
    role: 'system' | 'user' | 'assistant' | 'tool'
    content: string
    name?: string
    tool_call_id?: string
  }>
  use_rag?: boolean
  attachments?: string[]
  temperature?: number
  max_tokens?: number
}

export function listConversations(offset = 0, limit = 100) {
  return apiClient.get<{ items: Conversation[]; page: PageMeta }>('/api/chat/conversations', { params: { limit, offset } })
}

export function createConversation(conversation: Pick<Conversation, 'conversation_id' | 'title'>) {
  return apiClient.post<Conversation>('/api/chat/conversations', {
    conversation_id: conversation.conversation_id,
    title: conversation.title,
  })
}

export function listConversationMessages(conversationId: string, offset = 0, limit = 500) {
  return apiClient.get<{ items: ChatMessage[]; page: PageMeta }>(`/api/chat/conversations/${encodeURIComponent(conversationId)}/messages`, { params: { limit, offset } })
}

export function removeConversation(conversationId: string) {
  return apiClient.delete(`/api/chat/conversations/${encodeURIComponent(conversationId)}`)
}

export function selectMessageVersion(conversationId: string, messageId: string) {
  return apiClient.post(`/api/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/select`, {})
}

export function streamChat(
  request: ChatRequest,
  handlers: {
    onEvent?: (event: ModelEvent) => void
    onError?: (error: Error) => void
    onDone?: () => void
    onOpen?: () => void
  }
): SseClient {
  const client = new SseClient({
    url: '/api/chat',
    method: 'POST',
    body: request,
    onEvent: (eventName, data) => {
      handlers.onEvent?.({
        event: eventName as ModelEvent['event'],
        sequence: data.sequence as number,
        data: (data.data || {}) as Record<string, unknown>,
        timestamp: (data.timestamp as string) || new Date().toISOString(),
      })
    },
    onError: handlers.onError,
    onDone: handlers.onDone,
    onOpen: handlers.onOpen,
  })
  client.connect().catch(() => {})
  return client
}
