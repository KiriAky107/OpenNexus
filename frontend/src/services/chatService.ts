import { SseClient } from './sseClient'
import type { ModelEvent } from '@/contracts'

export interface ChatRequest {
  provider_id: string
  model: string
  conversation_id?: string
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
