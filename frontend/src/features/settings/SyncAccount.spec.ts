// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, it, expect, vi } from 'vitest'
import type { SyncAccount as Account } from '@/contracts/sync'
import { hostInvoke } from '@/services/platform/desktop'
import SyncAccount from './SyncAccount.vue'
const confirm = vi.hoisted(() => vi.fn())
vi.mock('@/services/platform/desktop', () => ({ hostInvoke: vi.fn() }))
vi.mock('@/composables/useActionDialog', () => ({ useActionDialog: () => ({ actionDialog: null, resolveAction: vi.fn(), askConfirm: confirm }) }))
const props = () => ({ vaultId: 'vault', bindingId: 'binding', remoteVaultId: 'remote', disabled: false })
const account = (): Account => ({ vault_id: 'vault', binding_id: 'binding', current_device_id: 'current', checked_at: 100, vault: { id: 'remote', name: '课程', sequence: 7, used: 12, quota: 10 }, devices: [{ id: 'current', name: '本机', revoked: false }, { id: 'other', name: '设备二', revoked: false }] })
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks() })
it('shows actual quota and devices and requires a separate accepted revocation', async () => {
  vi.mocked(hostInvoke).mockImplementation(async command => command === 'sync_account_status' ? account() : null)
  const wrapper = mount(SyncAccount, { props: props() }); await flushPromises()
  expect(wrapper.text()).toContain('12 B / 10 B'); expect(wrapper.text()).toContain('剩余配额 0 B'); expect(wrapper.text()).toContain('远端配额已满')
  const buttons = wrapper.findAll('button').filter(button => button.text() === '撤销访问')
  expect(buttons[0].attributes('disabled')).toBeDefined()
  confirm.mockResolvedValue(false); await buttons[1].trigger('click'); await flushPromises()
  expect(vi.mocked(hostInvoke).mock.calls.some(([name]) => name === 'sync_revoke_device')).toBe(false)
  confirm.mockResolvedValue(true); await buttons[1].trigger('click'); await flushPromises()
  expect(hostInvoke).toHaveBeenCalledWith('sync_revoke_device', { request: { vault_id: 'vault', binding_id: 'binding', device_id: 'other' } })
  wrapper.unmount()
})
it('drops old account replies and pending confirmations when scope changes', async () => {
  let answer!: (value: Account) => void
  vi.mocked(hostInvoke).mockImplementation(() => new Promise(resolve => { answer = resolve }))
  const wrapper = mount(SyncAccount, { props: props() })
  const old = answer
  await wrapper.setProps({ vaultId: 'another', disabled: true }); old(account()); await flushPromises()
  expect(wrapper.text()).not.toContain('课程')
  vi.mocked(hostInvoke).mockResolvedValue(account()); await wrapper.setProps(props()); await flushPromises()
  let accept!: (value: boolean) => void
  confirm.mockImplementation(() => new Promise(resolve => { accept = resolve }))
  await wrapper.findAll('button').filter(button => button.text() === '撤销访问')[1].trigger('click')
  await wrapper.setProps({ bindingId: 'new', disabled: true }); accept(true); await flushPromises()
  expect(vi.mocked(hostInvoke).mock.calls.some(([name]) => name === 'sync_revoke_device')).toBe(false)
  wrapper.unmount()
})
it('keeps cached data visibly dated on failure and stops polling on unmount', async () => {
  vi.useFakeTimers(); vi.mocked(hostInvoke).mockResolvedValue(account())
  const wrapper = mount(SyncAccount, { props: props() }); await flushPromises()
  vi.mocked(hostInvoke).mockRejectedValue(new Error('SYNC_NETWORK_ERROR'))
  await vi.advanceTimersByTimeAsync(30000); await flushPromises()
  expect(wrapper.text()).toContain('课程'); expect(wrapper.text()).toContain('读取时间'); expect(wrapper.text()).toContain('连接服务失败')
  wrapper.unmount(); const count = vi.mocked(hostInvoke).mock.calls.length
  await vi.advanceTimersByTimeAsync(60000); expect(hostInvoke).toHaveBeenCalledTimes(count)
})
