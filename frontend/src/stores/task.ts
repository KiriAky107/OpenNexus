import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { TaskItem, TaskStatus, TaskPriority, TaskSource } from '@/contracts'
import { createTask as createTaskRequest, deleteTask as deleteTaskRequest, listTasks, mockTasks, updateTask as updateTaskRequest } from '@/services/taskService'

export const useTaskStore = defineStore('task', () => {
  const tasks = ref<TaskItem[]>(mockTasks)
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

  async function loadTasks() {
    isLoading.value = true
    try {
      const resp = await listTasks()
      tasks.value = resp.items
      error.value = null
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '任务加载失败'
    } finally {
      isLoading.value = false
    }
  }

  async function createTask(data: { title: string; description?: string; priority?: TaskPriority; due_date?: string; note_id?: string }) {
    const newTask = await createTaskRequest(data)
    tasks.value.unshift(newTask)
    return newTask
  }

  async function updateTask(taskId: string, data: Partial<Pick<TaskItem, 'title' | 'description' | 'status' | 'due_date'>> & { note_id?: string | null }) {
    const task = tasks.value.find((t) => t.task_id === taskId)
    if (task) {
      const updated = await updateTaskRequest(taskId, data)
      Object.assign(task, updated, data)
    }
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
    setFilterStatus,
    setFilterPriority,
    setFilterSource,
  }
})
