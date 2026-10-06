// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import type { SyncActivity as Activity } from '@/contracts/sync'
import SyncActivity from './SyncActivity.vue'
import { syncError } from './syncPresentation'
it('distinguishes received, confirmed and resumed bytes from completed cycles', async () => {
  const activity: Activity = { running: true, phase: 'pull', uploaded_revisions: 0, received_revisions: 2, started_at: 100, finished_at: null, transfer: { direction: 'download', path: 'experiments/课程.py', total_bytes: 10, bytes_done: 5, resumed_bytes: 0, transferred_bytes: 5, phase: 'receiving' } }
  const wrapper = mount(SyncActivity, { props: { activity, lastSuccess: 90 } })
  expect(wrapper.text()).toContain('尚待完整校验'); expect(wrapper.get('progress').attributes('value')).toBe('5')
  await wrapper.setProps({ activity: { ...activity, phase: 'push', transfer: { ...activity.transfer!, direction: 'upload', phase: 'sending', resumed_bytes: 4, transferred_bytes: 1 } } })
  expect(wrapper.text()).toContain('服务端已确认'); expect(wrapper.text()).toContain('恢复已确认偏移 4 B')
  await wrapper.setProps({ activity: { ...activity, phase: 'cancelled', running: false } })
  expect(wrapper.text()).toContain('本轮已停止'); expect(wrapper.text()).toContain('一轮成功不表示队列已清空')
  wrapper.unmount()
})
it('maps actionable errors without reflecting arbitrary response strings', () => {
  expect(syncError('QUOTA_EXCEEDED')).toContain('配额不足')
  expect(syncError('CREDENTIALS_LOCKED')).toContain('解锁')
  expect(syncError('PROTOCOL_INCOMPATIBLE')).toContain('协议不兼容')
  expect(syncError('unexpected private-token text')).not.toContain('private-token')
})
