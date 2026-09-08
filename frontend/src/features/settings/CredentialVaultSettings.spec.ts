// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import { hostInvoke } from '@/services/platform/desktop'
import CredentialVaultSettings from './CredentialVaultSettings.vue'

vi.mock('@/services/platform/desktop', () => ({ hostInvoke: vi.fn() }))
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks() })

it('reflects host session lock and clears its polling timer on unmount', async () => {
  vi.useFakeTimers()
  vi.mocked(hostInvoke).mockResolvedValue({ locked: false })
  const wrapper = mount(CredentialVaultSettings)
  await flushPromises()
  expect(wrapper.text()).toContain('立即锁定')
  vi.mocked(hostInvoke).mockResolvedValue({ locked: true })
  await vi.advanceTimersByTimeAsync(500)
  expect(wrapper.text()).not.toContain('立即锁定')
  expect(wrapper.text()).toContain('恢复备份')
  wrapper.unmount()
  const calls = vi.mocked(hostInvoke).mock.calls.length
  await vi.advanceTimersByTimeAsync(2000)
  expect(vi.mocked(hostInvoke).mock.calls.length).toBe(calls)
})

it('clears the restore password and treats native cancellation as no change', async () => {
  vi.mocked(hostInvoke).mockImplementation(async command => command === 'credentials_status' ? { locked: true } : null)
  const wrapper = mount(CredentialVaultSettings)
  await flushPromises()
  await wrapper.get('input[type=password]').setValue('test-backup-password')
  await wrapper.findAll('button').find(button => button.text().includes('恢复备份'))!.trigger('click')
  await flushPromises()
  expect(hostInvoke).toHaveBeenCalledWith('credentials_restore', { password: 'test-backup-password' })
  expect((wrapper.get('input').element as HTMLInputElement).value).toBe('')
  expect(wrapper.text()).not.toContain('已恢复')
  wrapper.unmount()
})
