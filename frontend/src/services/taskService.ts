import apiClient from './apiClient'
import type { ApiTask, OperationResponse, PageMeta, TaskAgentScheduleInput, TaskItem, TaskStatus } from '@/contracts'

function toTask(task: ApiTask): TaskItem {
  return {
    task_id: task.task_id,
    title: task.title,
    description: task.description,
    status: task.status,
    due_date: task.due_at ?? undefined,
    note_id: task.note_id ?? undefined,
    agent_schedule: task.agent_schedule ?? null,
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
  agent_schedule?: TaskAgentScheduleInput
}): Promise<TaskItem> {
  return toTask(await apiClient.post<ApiTask>('/api/tasks', {
    title: data.title,
    description: data.description ?? '',
    due_at: data.due_date,
    note_id: data.note_id,
    agent_schedule: data.agent_schedule,
  }))
}

export async function updateTask(
  taskId: string,
  data: Partial<Pick<TaskItem, 'title' | 'description' | 'status' | 'due_date'>> & { note_id?: string | null; agent_schedule?: TaskAgentScheduleInput | null }
): Promise<TaskItem> {
  return toTask(await apiClient.patch<ApiTask>(`/api/tasks/${taskId}`, {
    title: data.title,
    description: data.description,
    status: data.status,
    due_at: data.due_date,
    note_id: data.note_id,
    agent_schedule: data.agent_schedule,
  }))
}

export async function deleteTask(taskId: string): Promise<OperationResponse> {
  return apiClient.delete(`/api/tasks/${taskId}`)
}

export async function runTaskAgentSchedule(taskId: string): Promise<TaskItem> {
  return toTask(await apiClient.post<ApiTask>(`/api/tasks/${taskId}/agent-schedule/run`))
}
