import { onMounted, onUnmounted, watch } from 'vue'
import { useTaskStore } from '@/stores/task'
import { useWorkspaceStore } from '@/stores/workspace'
import { publishAppNotification } from '@/services/notificationCenter'
import { t } from '@/i18n'

const MAX_TIMER_DELAY = 60_000

export function useTaskScheduleRunner() {
  const tasks = useTaskStore()
  const workspace = useWorkspaceStore()
  let timer: ReturnType<typeof setTimeout> | undefined
  let running = false
  let disposed = false

  function clearTimer() { clearTimeout(timer); timer = undefined }

  function plan() {
    clearTimer()
    if (disposed || !workspace.vaultId || running) return
    const pending = tasks.tasks
      .filter(task => task.agent_schedule?.enabled && task.agent_schedule.status === 'pending' && task.agent_schedule.next_run_at)
      .sort((left, right) => Date.parse(left.agent_schedule!.next_run_at!) - Date.parse(right.agent_schedule!.next_run_at!))
    const next = pending[0]?.agent_schedule?.next_run_at
    if (!next) return
    const delay = Math.max(0, Math.min(MAX_TIMER_DELAY, Date.parse(next) - Date.now()))
    timer = setTimeout(() => void runDue(), delay)
  }

  async function runDue() {
    if (running || disposed || !workspace.vaultId) return
    running = true
    try {
      const due = tasks.tasks.filter(task => {
        const schedule = task.agent_schedule
        return schedule?.enabled && schedule.status === 'pending' && schedule.next_run_at && Date.parse(schedule.next_run_at) <= Date.now()
      })
      for (const task of due) {
        try {
          const updated = await tasks.runTaskAgentSchedule(task.task_id)
          const runId = updated.agent_schedule?.last_run_id
          publishAppNotification({
            level: 'success',
            title: t('定时 Agent 已启动', 'Scheduled Agent started'),
            message: task.title,
            action: runId ? { type: 'agent_run', resource_id: runId } : { type: 'task', resource_id: task.task_id },
          })
        } catch (error) {
          publishAppNotification({
            level: 'error',
            title: t('定时 Agent 启动失败', 'Scheduled Agent failed to start'),
            message: `${task.title}: ${error instanceof Error ? error.message : String(error)}`,
            action: { type: 'task', resource_id: task.task_id },
          })
        }
      }
      if (due.length) await tasks.loadTasks()
    } finally {
      running = false
      plan()
    }
  }

  const stopVault = watch(() => workspace.vaultId, async vaultId => {
    clearTimer()
    if (!vaultId) return
    await tasks.loadTasks()
    if (!disposed && workspace.vaultId === vaultId) plan()
  }, { immediate: true })
  const stopSchedules = watch(
    () => tasks.tasks.map(task => `${task.task_id}:${task.agent_schedule?.status}:${task.agent_schedule?.next_run_at}`).join('|'),
    plan,
  )
  onMounted(plan)
  onUnmounted(() => { disposed = true; clearTimer(); stopVault(); stopSchedules() })
}
