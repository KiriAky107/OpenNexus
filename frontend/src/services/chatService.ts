import apiClient from './apiClient'
import { SseClient } from './sseClient'
import type { Conversation, ChatMessage, ModelEvent } from '@/contracts'

export async function listConversations(): Promise<Conversation[]> {
  return apiClient.get('/api/conversations')
}

export async function getConversation(conversationId: string): Promise<Conversation> {
  return apiClient.get(`/api/conversations/${conversationId}`)
}

export async function getMessages(conversationId: string): Promise<ChatMessage[]> {
  return apiClient.get(`/api/conversations/${conversationId}/messages`)
}

export interface ChatRequest {
  conversation_id?: string
  message: string
  provider_id?: string
  model?: string
  use_rag?: boolean
  skill_id?: string
  attachments?: string[]
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

export const mockConversations: Conversation[] = [
  {
    conversation_id: 'conv-1',
    title: '关于红黑树的讨论',
    created_at: '2026-08-25T10:00:00Z',
    updated_at: '2026-08-25T10:30:00Z',
    message_count: 6,
  },
  {
    conversation_id: 'conv-2',
    title: '死锁避免算法',
    created_at: '2026-08-24T14:00:00Z',
    updated_at: '2026-08-24T15:20:00Z',
    message_count: 4,
  },
  {
    conversation_id: 'conv-3',
    title: 'TCP三次握手',
    created_at: '2026-08-22T09:00:00Z',
    updated_at: '2026-08-22T09:15:00Z',
    message_count: 3,
  },
]

export const mockMessages: Record<string, ChatMessage[]> = {
  'conv-1': [
    {
      message_id: 'msg-1',
      conversation_id: 'conv-1',
      role: 'user',
      content: '红黑树的插入操作有哪些情况需要调整？',
      created_at: '2026-08-25T10:00:00Z',
    },
    {
      message_id: 'msg-2',
      conversation_id: 'conv-1',
      role: 'assistant',
      content:
        '红黑树插入后可能违反红黑性质，需要通过旋转和变色来修复。主要有以下几种情况：\n\n**情况1：叔叔节点是红色**\n- 将父节点和叔叔节点设为黑色\n- 将祖父节点设为红色\n- 当前节点上移到祖父节点，继续向上调整\n\n**情况2：叔叔节点是黑色，且当前节点是右孩子**\n- 以父节点为支点左旋\n- 将当前节点转换为左孩子，进入情况3\n\n**情况3：叔叔节点是黑色，且当前节点是左孩子**\n- 以祖父节点为支点右旋\n- 将父节点设为黑色，祖父节点设为红色\n\n以上三种情况需要从插入节点开始向上递归处理，直到根节点。',
      created_at: '2026-08-25T10:01:00Z',
      citations: [
        {
          note_id: 'n-rbt',
          block_id: 'b1',
          file_path: '/数据结构/红黑树.md',
          heading_path: '数据结构 / 红黑树 / 插入操作',
          content: '插入后可能破坏红黑性质，需要通过变色和旋转来修复...',
        },
        {
          note_id: 'n-rbt',
          block_id: 'b2',
          file_path: '/数据结构/红黑树.md',
          heading_path: '数据结构 / 红黑树 / 五种情况',
          content: 'Case 1: 叔叔为红色，直接变色...',
        },
      ],
    },
    {
      message_id: 'msg-3',
      conversation_id: 'conv-1',
      role: 'user',
      content: '能对比一下 AVL 树和红黑树的性能差异吗？',
      created_at: '2026-08-25T10:10:00Z',
    },
    {
      message_id: 'msg-4',
      conversation_id: 'conv-1',
      role: 'assistant',
      content:
        'AVL 树和红黑树都是平衡二叉搜索树，但它们的平衡策略和性能特点有所不同：\n\n| 特性 | AVL 树 | 红黑树 |\n|------|--------|--------|\n| 严格平衡 | 高度差 ≤ 1 | 黑色高度相同 |\n| 查找速度 | 更快（严格平衡） | 略慢但仍为 O(log n) |\n| 插入/删除 | 旋转更多 | 旋转更少（颜色调整为主） |\n| 存储开销 | 每个节点存高度（int） | 每个节点存颜色（1 bit） |\n| 适用场景 | 读多写少 | 读写均衡或写较多 |\n\n**总结：**\n- 如果应用以查询为主，很少修改，AVL 树可能更优\n- 如果插入删除频繁，红黑树更合适，重平衡开销更低\n- 红黑树在工业界应用更广泛（C++ STL 的 map/set、Java 的 TreeMap 等）',
      created_at: '2026-08-25T10:11:00Z',
      citations: [
        {
          note_id: 'n-rbt',
          block_id: 'b3',
          file_path: '/数据结构/红黑树.md',
          heading_path: '数据结构 / 红黑树 / 与AVL树对比',
          content: '红黑树相比AVL树，牺牲了部分平衡性以换取更少的旋转操作...',
        },
      ],
    },
  ],
}
