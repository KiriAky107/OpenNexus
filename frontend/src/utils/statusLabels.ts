import { t } from '@/i18n'

export function statusLabel(status?: string): string {
  const labels: Record<string, string> = {
    todo: t('待办', 'To do'), in_progress: t('进行中', 'In progress'), done: t('已完成', 'Done'),
    cancelled: t('已取消', 'Cancelled'), pending: t('待执行', 'Pending'), running: t('运行中', 'Running'),
    queued: t('排队中', 'Queued'), completed: t('已完成', 'Completed'), failed: t('失败', 'Failed'),
    scheduled: t('已安排', 'Scheduled'), triggered: t('已触发', 'Triggered'), disabled: t('已停用', 'Disabled'),
    ready: t('可用', 'Ready'), installed: t('已安装', 'Installed'), error: t('异常', 'Error'),
    dependency_missing: t('缺少依赖', 'Missing dependency'), permission_required: t('等待授权', 'Permission required'),
    waiting_permission: t('等待授权', 'Waiting for permission'), paused: t('已暂停', 'Paused'),
    waiting_budget: t('等待追加预算', 'Waiting for more budget'),
    awaiting_confirmation: t('等待确认分工', 'Awaiting plan approval'), partial_failure: t('部分失败', 'Partial failure'),
    blocked: t('依赖未完成', 'Dependency not completed'), interrupted: t('执行已中断', 'Interrupted'),
    stopped: t('未运行', 'Stopped'), starting: t('启动中', 'Starting'), connected: t('已连接', 'Connected'),
  }
  return status ? labels[status] || status : t('未知状态', 'Unknown status')
}

export function cronSummary(expression: string): string {
  const fields = expression.trim().split(/\s+/)
  if (fields.length !== 5) return t('自定义循环', 'Custom schedule')
  const [minute, hour, day, month, weekday] = fields
  if (day !== '*' || month !== '*') return t('自定义循环', 'Custom schedule')
  if (/^\d+$/.test(minute!) && /^\d+$/.test(hour!) && +minute! < 60 && +hour! < 24) {
    const time = `${hour!.padStart(2, '0')}:${minute!.padStart(2, '0')}`
    if (weekday === '*') return t(`每天 ${time}`, `Daily at ${time}`)
    if (weekday === '1-5') return t(`工作日 ${time}`, `Weekdays at ${time}`)
  }
  const interval = minute?.match(/^\*\/(\d+)$/)
  if (interval && +interval[1]! >= 1 && +interval[1]! <= 59 && hour === '*' && weekday === '*') {
    return t(`每小时内每 ${interval[1]} 分钟`, `Every ${interval[1]} minutes within each hour`)
  }
  return t('自定义循环', 'Custom schedule')
}

export function localDateTime(value?: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return ''
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}
