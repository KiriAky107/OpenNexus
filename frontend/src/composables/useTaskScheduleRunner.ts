import { onMounted, onUnmounted, watch } from 'vue'
import { useTaskStore } from '@/stores/task'
import { useWorkspaceStore } from '@/stores/workspace'
import { publishAppNotification } from '@/services/notificationCenter'
import { sanitizeNotificationMessage } from '@/services/notificationCenter'
import { getAgentRun } from '@/services/agentService'
import { t } from '@/i18n'

const MAX_TIMER_DELAY = 60_000
const UPCOMING_WINDOW = 5 * 60_000
const RUN_POLL_DELAY = 2500

export function useTaskScheduleRunner() {
  const tasks = useTaskStore()
  const workspace = useWorkspaceStore()
  let timer: ReturnType<typeof setTimeout> | undefined
  let running = false
  let disposed = false
  const announced = new Set<string>()
  const runTimers = new Set<ReturnType<typeof setTimeout>>()

  function clearTimer() { clearTimeout(timer); timer = undefined }

  function notifyUpcoming() {
    const vaultId = workspace.vaultId
    if (!vaultId) return
    const now = Date.now()
    for (const task of tasks.tasks) {
      const nextRun = task.agent_schedule?.next_run_at
      if (!task.agent_schedule?.enabled || task.agent_schedule.status !== 'pending' || !nextRun) continue
      const delay = Date.parse(nextRun) - now
      const key = `${vaultId}:${task.task_id}:${nextRun}`
      if (delay > 0 && delay <= UPCOMING_WINDOW && !announced.has(key)) {
        announced.add(key)
        publishAppNotification({
          level: 'warning', category: 'task_upcoming', vault_id: vaultId, dedupe_key: key,
          title: t('定时任务即将执行', 'Scheduled task is due soon'),
          message: task.title,
          action: { type: 'task', resource_id: task.task_id },
        })
      }
    }
  }

  function plan() {
    clearTimer()
    if (disposed || !workspace.vaultId || running) return
    notifyUpcoming()
    const pending = tasks.tasks
      .filter(task => task.agent_schedule?.enabled && task.agent_schedule.status === 'pending' && task.agent_schedule.next_run_at)
      .sort((left, right) => Date.parse(left.agent_schedule!.next_run_at!) - Date.parse(right.agent_schedule!.next_run_at!))
    const next = pending[0]?.agent_schedule?.next_run_at
    if (!next) return
    const delay = Math.max(0, Math.min(MAX_TIMER_DELAY, Date.parse(next) - Date.now()))
    timer = setTimeout(() => void runDue(), delay)
  }

  async function monitorRun(runId: string, taskId: string, taskTitle: string, vaultId: string) {
    if (disposed || workspace.vaultId !== vaultId) return
    try {
      const run = await getAgentRun(runId)
      if (workspace.vaultId !== vaultId) return
      if (run.status === 'completed') {
        publishAppNotification({
          level: 'success', category: 'task_completed', vault_id: vaultId,
          dedupe_key: `${vaultId}:${runId}:completed`,
          title: t('定时 Agent 已完成', 'Scheduled Agent completed'), message: taskTitle,
          action: { type: 'agent_run', resource_id: runId },
        })
        return
      }
      if (run.status === 'failed' || run.status === 'cancelled') {
        publishAppNotification({
          level: 'error', category: 'task_failed', vault_id: vaultId, persistent: true,
          dedupe_key: `${vaultId}:${runId}:${run.status}`,
          title: run.status === 'failed' ? t('定时 Agent 执行失败', 'Scheduled Agent failed') : t('定时 Agent 已取消', 'Scheduled Agent cancelled'),
          message: `${taskTitle}: ${sanitizeNotificationMessage(run.error || run.status)}`,
          action: { type: 'agent_run', resource_id: runId },
        })
        return
      }
      const handle = setTimeout(() => {
        runTimers.delete(handle)
        void monitorRun(runId, taskId, taskTitle, vaultId)
      }, RUN_POLL_DELAY)
      runTimers.add(handle)
    } catch (error) {
      if (workspace.vaultId !== vaultId) return
      publishAppNotification({
        level: 'error', category: 'task_failed', vault_id: vaultId, persistent: true,
        dedupe_key: `${vaultId}:${runId}:monitor`,
        title: t('无法获取定时 Agent 状态', 'Could not read scheduled Agent status'),
        message: `${taskTitle}: ${sanitizeNotificationMessage(error)}`,
        action: { type: 'task', resource_id: taskId },
      })
    }
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
        const vaultId = workspace.vaultId
        const scheduledAt = Date.parse(task.agent_schedule!.next_run_at!)
        if (Date.now() - scheduledAt > MAX_TIMER_DELAY) {
          publishAppNotification({
            level: 'warning', category: 'task_started', vault_id: vaultId,
            dedupe_key: `${vaultId}:${task.task_id}:${task.agent_schedule!.next_run_at}:overdue`,
            title: t('正在补执行逾期任务', 'Running an overdue task'), message: task.title,
            action: { type: 'task', resource_id: task.task_id },
          })
        }
        try {
          publishAppNotification({
            level: 'info', category: 'task_started', vault_id: vaultId,
            dedupe_key: `${vaultId}:${task.task_id}:${task.agent_schedule!.next_run_at}:started`,
            title: t('定时 Agent 正在启动', 'Scheduled Agent is starting'), message: task.title,
            action: { type: 'task', resource_id: task.task_id },
          })
          const updated = await tasks.runTaskAgentSchedule(task.task_id)
          const runId = updated.agent_schedule?.last_run_id
          publishAppNotification({
            level: 'success', category: 'task_started', vault_id: vaultId,
            dedupe_key: `${vaultId}:${task.task_id}:${runId || 'started'}`,
            title: t('定时 Agent 已启动', 'Scheduled Agent started'),
            message: task.title,
            action: runId ? { type: 'agent_run', resource_id: runId } : { type: 'task', resource_id: task.task_id },
          })
          if (runId) void monitorRun(runId, task.task_id, task.title, vaultId)
        } catch (error) {
          publishAppNotification({
            level: 'error', category: 'task_failed', vault_id: vaultId, persistent: true,
            dedupe_key: `${vaultId}:${task.task_id}:${task.agent_schedule!.next_run_at}:failed`,
            title: t('定时 Agent 启动失败', 'Scheduled Agent failed to start'),
            message: `${task.title}: ${sanitizeNotificationMessage(error)}`,
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
    announced.clear()
    for (const handle of runTimers) clearTimeout(handle)
    runTimers.clear()
    if (!vaultId) return
    await tasks.loadTasks()
    if (!disposed && workspace.vaultId === vaultId) plan()
  }, { immediate: true })
  const stopSchedules = watch(
    () => tasks.tasks.map(task => `${task.task_id}:${task.agent_schedule?.status}:${task.agent_schedule?.next_run_at}`).join('|'),
    plan,
  )
  onMounted(plan)
  onUnmounted(() => {
    disposed = true
    clearTimer()
    for (const handle of runTimers) clearTimeout(handle)
    runTimers.clear()
    stopVault()
    stopSchedules()
  })
}
