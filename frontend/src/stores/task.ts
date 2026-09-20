import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { TaskAgentScheduleInput, TaskItem, TaskStatus, TaskPriority, TaskSource } from '@/contracts'
import { createTask as createTaskRequest, deleteTask as deleteTaskRequest, listTasks, runTaskAgentSchedule as runTaskAgentScheduleRequest, updateTask as updateTaskRequest } from '@/services/taskService'
import { t } from '@/i18n'

export const useTaskStore = defineStore('task', () => {
  const tasks = ref<TaskItem[]>([])
  const filterStatus = ref<TaskStatus | 'all'>('all')
  const filterPriority = ref<TaskPriority | 'all'>('all')
  const filterSource = ref<TaskSource | 'all'>('all')
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  const filteredTasks = computed(() => {
    return tasks.value.filter((t) => {
      if (filterStatus.value !== 'all' && t.status !== filterStatus.value) return false
      if (filterPriority.value !== 'all' && t.priority !== filterPriority.value) return false
      if (filterSource.value !== 'all' && t.source !== filterSource.value) return false
      return true
    })
  })

  const todoTasks = computed(() => tasks.value.filter((t) => t.status === 'todo'))
  const inProgressTasks = computed(() => tasks.value.filter((t) => t.status === 'in_progress'))
  const doneTasks = computed(() => tasks.value.filter((t) => t.status === 'done'))

  let loadVersion = 0
  async function loadTasks() {
    const version = ++loadVersion
    isLoading.value = true
    try {
      const items: TaskItem[] = []
      let offset = 0
      do {
        const resp = await listTasks({ limit: 100, offset })
        if (version !== loadVersion) return
        items.push(...resp.items)
        offset += resp.items.length
        if (!resp.items.length || offset >= resp.total) break
      } while (true)
      tasks.value = [...new Map(items.map(item => [item.task_id, item])).values()]
      error.value = null
    } catch (reason) {
      if (version !== loadVersion) return
      error.value = reason instanceof Error ? reason.message : t('任务加载失败', 'Failed to load tasks')
    } finally {
      if (version === loadVersion) isLoading.value = false
    }
  }

  async function createTask(data: { title: string; description?: string; priority?: TaskPriority; due_date?: string; note_id?: string; agent_schedule?: TaskAgentScheduleInput }) {
    const newTask = await createTaskRequest(data)
    tasks.value.unshift(newTask)
    return newTask
  }

  async function updateTask(taskId: string, data: Partial<Pick<TaskItem, 'title' | 'description' | 'status' | 'due_date'>> & { note_id?: string | null; agent_schedule?: TaskAgentScheduleInput | null }) {
    const task = tasks.value.find((t) => t.task_id === taskId)
    if (task) {
      const updated = await updateTaskRequest(taskId, data)
      Object.assign(task, updated)
    }
  }

  async function runTaskAgentSchedule(taskId: string) {
    const updated = await runTaskAgentScheduleRequest(taskId)
    const index = tasks.value.findIndex(task => task.task_id === taskId)
    if (index >= 0) tasks.value[index] = updated
    return updated
  }

  async function deleteTask(taskId: string) {
    await deleteTaskRequest(taskId)
    const idx = tasks.value.findIndex((t) => t.task_id === taskId)
    if (idx > -1) tasks.value.splice(idx, 1)
  }

  function setFilterStatus(s: TaskStatus | 'all') { filterStatus.value = s }
  function setFilterPriority(p: TaskPriority | 'all') { filterPriority.value = p }
  function setFilterSource(s: TaskSource | 'all') { filterSource.value = s }

  return {
    tasks,
    filterStatus,
    filterPriority,
    filterSource,
    filteredTasks,
    todoTasks,
    inProgressTasks,
    doneTasks,
    isLoading,
    error,
    loadTasks,
    createTask,
    updateTask,
    deleteTask,
    runTaskAgentSchedule,
    setFilterStatus,
    setFilterPriority,
    setFilterSource,
  }
})
