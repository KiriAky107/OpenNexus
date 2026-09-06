// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useTaskStore } from './task'
import { useAgentStore } from './agent'
const mocks = vi.hoisted(() => ({ tasks: vi.fn(), runs: vi.fn() }))
vi.mock('@/services/taskService', () => ({ listTasks: mocks.tasks }))
vi.mock('@/services/agentService', () => ({ listAgentRuns: mocks.runs }))
beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })
it('loads tasks past the first page before applying global status filters', async () => {
  const tasks = Array.from({ length: 251 }, (_, i) => ({ task_id: `t${i}`, status: i >= 200 ? 'done' : 'todo' }))
  mocks.tasks.mockImplementation(async ({ offset, limit }) => ({ items: tasks.slice(offset, offset + limit), total: tasks.length }))
  const store = useTaskStore()
  await store.loadTasks()
  expect(store.tasks).toHaveLength(251)
  store.setFilterStatus('done')
  expect(store.filteredTasks).toHaveLength(51)
  expect(mocks.tasks).toHaveBeenCalledTimes(3)
})
it('includes older Agent history and leaves failed refresh data intact', async () => {
  const runs = Array.from({ length: 201 }, (_, i) => ({ run_id: `r${i}`, status: 'completed' }))
  mocks.runs.mockImplementation(async ({ offset, limit }) => ({ items: runs.slice(offset, offset + limit), total: runs.length }))
  const store = useAgentStore()
  await store.loadRuns()
  expect(store.runs).toHaveLength(201)
  mocks.runs.mockRejectedValue(new Error('offline'))
  await expect(store.loadRuns()).rejects.toThrow('offline')
  expect(store.runs).toHaveLength(201)
})
