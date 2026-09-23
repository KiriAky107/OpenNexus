<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import { useWorkspaceStore } from '@/stores/workspace'
import { subscribeAppNotifications, type AppNotification } from '@/services/notificationCenter'
import { t } from '@/i18n'

const MAX_VISIBLE = 4
const DEFAULT_AUTO_CLOSE = 5000
const settings = useSettingsStore()
const workspace = useWorkspaceStore()
const router = useRouter()
const notifications = ref<AppNotification[]>([])
const timers = new Map<string, ReturnType<typeof setTimeout>>()
const deadlines = new Map<string, number>()
const remaining = new Map<string, number>()
let unsubscribe: (() => void) | undefined

const visible = computed(() => notifications.value.slice(0, MAX_VISIBLE))
const queuedCount = computed(() => Math.max(0, notifications.value.length - MAX_VISIBLE))

function allowed(notification: AppNotification) {
  if (!settings.taskNotificationsEnabled) return false
  if (notification.vault_id && notification.vault_id !== workspace.vaultId) return false
  if (notification.category === 'task_upcoming' && !settings.taskUpcomingNotifications) return false
  if ((notification.level === 'success' || notification.category === 'task_started' || notification.category === 'task_completed') && !settings.taskSuccessNotifications) return false
  if (notification.level === 'error' && !settings.taskFailureNotifications) return false
  return true
}

function keyOf(notification: AppNotification) {
  return notification.dedupe_key || [notification.vault_id, notification.category, notification.action?.resource_id, notification.title].join(':')
}

function clearTimer(id: string) {
  const timer = timers.get(id)
  if (timer) clearTimeout(timer)
  timers.delete(id)
  deadlines.delete(id)
}

function dismiss(id: string) {
  clearTimer(id)
  remaining.delete(id)
  notifications.value = notifications.value.filter(item => item.notification_id !== id)
  startVisibleTimers()
}

function schedule(notification: AppNotification, delay = notification.auto_close_ms ?? DEFAULT_AUTO_CLOSE) {
  if (notification.persistent || notification.level === 'error') return
  clearTimer(notification.notification_id)
  deadlines.set(notification.notification_id, Date.now() + delay)
  timers.set(notification.notification_id, setTimeout(() => dismiss(notification.notification_id), delay))
}

function startVisibleTimers() {
  for (const notification of visible.value) {
    if (!timers.has(notification.notification_id) && !deadlines.has(notification.notification_id) && !remaining.has(notification.notification_id)) {
      schedule(notification)
    }
  }
}

function push(notification: AppNotification) {
  if (!allowed(notification)) return
  const duplicate = notifications.value.find(item => keyOf(item) === keyOf(notification))
  if (duplicate) dismiss(duplicate.notification_id)
  notifications.value.push(notification)
  startVisibleTimers()
}

function pause(notification: AppNotification) {
  const deadline = deadlines.get(notification.notification_id)
  if (deadline) remaining.set(notification.notification_id, Math.max(250, deadline - Date.now()))
  clearTimer(notification.notification_id)
}

function resume(notification: AppNotification) {
  if (notification.persistent || notification.level === 'error') return
  schedule(notification, remaining.get(notification.notification_id) ?? DEFAULT_AUTO_CLOSE)
  remaining.delete(notification.notification_id)
}

async function activate(notification: AppNotification) {
  if (!notification.action) return
  dismiss(notification.notification_id)
  if (notification.action.type === 'agent_run') {
    await router.push({ name: 'agent', params: { runId: notification.action.resource_id } })
  } else {
    await router.push({ name: 'tasks', query: { task: notification.action.resource_id } })
  }
}

watch(() => workspace.vaultId, () => {
  for (const item of notifications.value) clearTimer(item.notification_id)
  remaining.clear()
  notifications.value = []
})

onMounted(() => { unsubscribe = subscribeAppNotifications(push) })
onUnmounted(() => {
  unsubscribe?.()
  for (const item of notifications.value) clearTimer(item.notification_id)
})
</script>

<template>
  <aside class="notification-center" :aria-label="t('任务通知', 'Task notifications')" aria-live="polite">
    <article
      v-for="notification in visible"
      :key="notification.notification_id"
      class="app-notification"
      :class="notification.level"
      :role="notification.level === 'error' ? 'alert' : 'status'"
      tabindex="0"
      @mouseenter="pause(notification)"
      @mouseleave="resume(notification)"
      @focusin="pause(notification)"
      @focusout="resume(notification)"
    >
      <div class="notification-copy">
        <strong>{{ notification.title }}</strong>
        <p>{{ notification.message }}</p>
      </div>
      <div class="notification-actions">
        <button v-if="notification.action" type="button" class="notification-open" @click="activate(notification)">
          {{ t('查看', 'View') }}
        </button>
        <button type="button" class="notification-close" :aria-label="t('关闭通知', 'Dismiss notification')" @click="dismiss(notification.notification_id)">×</button>
      </div>
    </article>
    <p v-if="queuedCount" class="notification-queue">{{ t(`另有 ${queuedCount} 条通知`, `${queuedCount} more notifications`) }}</p>
  </aside>
</template>

<style scoped>
.notification-center {
  position: fixed;
  right: var(--space-xl);
  bottom: calc(var(--statusbar-height) + 88px);
  z-index: var(--z-notification);
  display: grid;
  width: min(380px, calc(100vw - 32px));
  gap: var(--space-sm);
  max-height: calc(100dvh - 200px);
  overflow-y: auto;
  pointer-events: none;
}
.app-notification {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-md);
  padding: var(--space-lg);
  border: 1px solid var(--color-border-default);
  border-left: 4px solid var(--color-info);
  border-radius: var(--radius-lg);
  background: var(--color-surface-elevated);
  color: var(--color-text-primary);
  box-shadow: var(--shadow-lg);
  pointer-events: auto;
  animation: notification-in var(--motion-normal) both;
}
.app-notification.success { border-left-color: var(--color-success); }
.app-notification.warning { border-left-color: var(--color-warning); }
.app-notification.error { border-left-color: var(--color-error); }
.notification-copy { min-width: 0; }
.notification-copy strong { display: block; margin-bottom: var(--space-xs); }
.notification-copy p { color: var(--color-text-secondary); overflow-wrap: anywhere; }
.notification-actions { display: flex; align-items: center; gap: var(--space-xs); flex-shrink: 0; }
.notification-open { color: var(--color-text-link); padding: var(--space-xs); }
.notification-close { color: var(--color-text-secondary); width: 28px; height: 28px; border-radius: var(--radius-sm); }
.notification-open:hover, .notification-close:hover { background: var(--color-background-hover); }
.notification-queue {
  justify-self: end;
  padding: var(--space-xs) var(--space-sm);
  border-radius: var(--radius-full);
  background: var(--color-surface-elevated);
  color: var(--color-text-secondary);
  box-shadow: var(--shadow-sm);
}
@keyframes notification-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
@media (max-width: 560px) { .notification-center { right: var(--space-md); } }
</style>
