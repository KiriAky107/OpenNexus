export const APP_NOTIFICATION_EVENT = 'opennexus:notification'

export interface AppNotification {
  notification_id: string
  level: 'info' | 'success' | 'warning' | 'error'
  title: string
  message: string
  created_at: string
  action?: { type: 'task' | 'agent_run'; resource_id: string }
}

export function publishAppNotification(input: Omit<AppNotification, 'notification_id' | 'created_at'>): AppNotification {
  const notification: AppNotification = {
    ...input,
    notification_id: crypto.randomUUID(),
    created_at: new Date().toISOString(),
  }
  window.dispatchEvent(new CustomEvent<AppNotification>(APP_NOTIFICATION_EVENT, { detail: notification }))
  return notification
}

/** Future toast/tray UI can subscribe without coupling itself to the task scheduler. */
export function subscribeAppNotifications(listener: (notification: AppNotification) => void): () => void {
  const handler = (event: Event) => listener((event as CustomEvent<AppNotification>).detail)
  window.addEventListener(APP_NOTIFICATION_EVENT, handler)
  return () => window.removeEventListener(APP_NOTIFICATION_EVENT, handler)
}
