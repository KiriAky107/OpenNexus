import apiClient from './apiClient'
import type { ApiTask, OperationResponse, PageMeta, TaskItem, TaskStatus, TaskPriority } from '@/contracts'

function toTask(task: ApiTask): TaskItem {
  return {
    task_id: task.task_id,
    title: task.title,
    description: task.description,
    status: task.status,
    priority: 'medium',
    due_date: task.due_at ?? undefined,
    note_id: task.note_id ?? undefined,
    source: 'user',
    created_at: task.created_at,
    updated_at: task.updated_at,
  }
}

export async function listTasks(params?: {
  limit?: number
  offset?: number
}): Promise<{ items: TaskItem[]; total: number }> {
  const response = await apiClient.get<{ items: ApiTask[]; page: PageMeta }>('/api/tasks', {
    params: { limit: params?.limit, offset: params?.offset },
  })
  return { items: response.items.map(toTask), total: response.page.total }
}

export async function getTask(taskId: string): Promise<TaskItem> {
  return toTask(await apiClient.get<ApiTask>(`/api/tasks/${taskId}`))
}

export async function createTask(data: {
  title: string
  description?: string
  due_date?: string
  note_id?: string
}): Promise<TaskItem> {
  return toTask(await apiClient.post<ApiTask>('/api/tasks', {
    title: data.title,
    description: data.description ?? '',
    due_at: data.due_date,
    note_id: data.note_id,
  }))
}

export async function updateTask(
  taskId: string,
  data: Partial<Pick<TaskItem, 'title' | 'description' | 'status' | 'due_date'>> & { note_id?: string | null }
): Promise<TaskItem> {
  return toTask(await apiClient.patch<ApiTask>(`/api/tasks/${taskId}`, {
    title: data.title,
    description: data.description,
    status: data.status,
    due_at: data.due_date,
    note_id: data.note_id,
  }))
}

export async function deleteTask(taskId: string): Promise<OperationResponse> {
  return apiClient.delete(`/api/tasks/${taskId}`)
}

export const mockTasks: TaskItem[] = [
  {
    task_id: 't-1',
    title: '完成红黑树章节复习',
    description: '整理插入、删除操作的所有情况，准备期末复习',
    status: 'todo',
    priority: 'high',
    due_date: '2026-08-30T23:59:00Z',
    note_id: 'n-rbt',
    note_title: '红黑树',
    source: 'user',
    created_at: '2026-08-20T10:00:00Z',
    updated_at: '2026-08-25T14:30:00Z',
  },
  {
    task_id: 't-2',
    title: '理解死锁的银行家算法',
    description: '推导银行家算法的安全性检查过程',
    status: 'in_progress',
    priority: 'medium',
    note_id: 'n-deadlock',
    note_title: '死锁',
    source: 'agent',
    created_at: '2026-08-22T09:00:00Z',
    updated_at: '2026-08-24T16:00:00Z',
  },
  {
    task_id: 't-3',
    title: 'TCP 三次握手与四次挥手',
    description: '',
    status: 'done',
    priority: 'high',
    note_id: 'n-tcp',
    note_title: 'TCP_IP',
    source: 'user',
    created_at: '2026-08-15T08:00:00Z',
    updated_at: '2026-08-18T20:00:00Z',
  },
  {
    task_id: 't-4',
    title: 'HTTP 状态码整理',
    description: '整理常见 HTTP 状态码及含义',
    status: 'todo',
    priority: 'low',
    note_id: 'n-http',
    note_title: 'HTTP协议',
    source: 'note',
    created_at: '2026-08-10T10:00:00Z',
    updated_at: '2026-08-10T10:00:00Z',
  },
  {
    task_id: 't-5',
    title: '链表操作实现练习',
    description: '实现单链表和双向链表的基本操作',
    status: 'todo',
    priority: 'medium',
    note_id: 'n-slist',
    note_title: '单链表',
    source: 'agent',
    created_at: '2026-08-23T11:00:00Z',
    updated_at: '2026-08-23T11:00:00Z',
  },
]
