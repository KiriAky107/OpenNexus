export const APP_NOTIFICATION_EVENT = 'opennexus:notification'

export type AppNotificationCategory =
  | 'generic'
  | 'task_upcoming'
  | 'task_started'
  | 'task_completed'
  | 'task_failed'

export interface AppNotification {
  notification_id: string
  level: 'info' | 'success' | 'warning' | 'error'
  title: string
  message: string
  created_at: string
  category?: AppNotificationCategory
  vault_id?: string
  dedupe_key?: string
  persistent?: boolean
  auto_close_ms?: number
  action?: { type: 'task' | 'agent_run'; resource_id: string }
}

export interface NativeNotificationAdapter {
  show(notification: AppNotification): void | Promise<void>
}

let nativeAdapter: NativeNotificationAdapter | undefined

export function setNativeNotificationAdapter(adapter?: NativeNotificationAdapter) {
  nativeAdapter = adapter
}

/** Removes common credential/header forms before an error reaches the visible UI. */
export function sanitizeNotificationMessage(value: unknown): string {
  const source = value instanceof Error ? value.message : String(value)
  return source
    .replace(/(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+/gi, '$1[redacted]')
    .replace(/((?:api[-_ ]?key|token|secret|password)\s*[:=]\s*)[^\s,;]+/gi, '$1[redacted]')
    .replace(/\n\s*at\s+.*(?:\n\s*at\s+.*)*/g, '')
    .slice(0, 320)
}

export function publishAppNotification(input: Omit<AppNotification, 'notification_id' | 'created_at'>): AppNotification {
  const notification: AppNotification = {
    ...input,
    notification_id: crypto.randomUUID(),
    created_at: new Date().toISOString(),
  }
  window.dispatchEvent(new CustomEvent<AppNotification>(APP_NOTIFICATION_EVENT, { detail: notification }))
  void nativeAdapter?.show(notification)
  return notification
}

/** Future toast/tray UI can subscribe without coupling itself to the task scheduler. */
export function subscribeAppNotifications(listener: (notification: AppNotification) => void): () => void {
  const handler = (event: Event) => listener((event as CustomEvent<AppNotification>).detail)
  window.addEventListener(APP_NOTIFICATION_EVENT, handler)
  return () => window.removeEventListener(APP_NOTIFICATION_EVENT, handler)
}
