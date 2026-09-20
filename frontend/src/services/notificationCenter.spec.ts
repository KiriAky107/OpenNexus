// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { publishAppNotification, subscribeAppNotifications } from './notificationCenter'

afterEach(() => vi.restoreAllMocks())

it('exposes scheduled-task notifications through a UI-independent subscription contract', () => {
  vi.spyOn(crypto, 'randomUUID').mockReturnValue('00000000-0000-4000-8000-000000000001')
  const received: string[] = []
  const unsubscribe = subscribeAppNotifications(notification => received.push(notification.action?.resource_id ?? ''))
  const notification = publishAppNotification({
    level: 'success', title: 'Scheduled Agent started', message: 'Daily review',
    action: { type: 'agent_run', resource_id: 'run_123' },
  })
  unsubscribe()
  publishAppNotification({ level: 'info', title: 'ignored', message: 'ignored' })
  expect(notification.notification_id).toBe('00000000-0000-4000-8000-000000000001')
  expect(received).toEqual(['run_123'])
})
