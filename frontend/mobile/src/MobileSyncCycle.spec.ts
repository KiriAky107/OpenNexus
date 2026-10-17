// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { appLocale } from '../../src/i18n'
import { installTestDialogApi } from './dialogTestSupport'
import MobileSyncCycle from './MobileSyncCycle.vue'
import { mobileOnSyncActivity, mobileSyncCycle } from './native'
import type { CycleReply, CycleSnapshot, CycleStatus, SyncTarget } from './syncCycleTypes'

vi.mock('./native', () => ({ mobileSyncCycle: vi.fn(), mobileOnSyncActivity: vi.fn() }))
installTestDialogApi()
const ids = Array.from({ length: 10 }, (_, index) => `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`)
const target: SyncTarget = { vault_id: ids[0]!, binding_id: ids[1]!, expected: {
  revision: 1, generation: 1, operation_id: ids[2]!, endpoint: 'https://sync.example/', account: 'owned-reader',
} }
const status = (): CycleStatus => ({ vault_id: target.vault_id, binding: { id: target.binding_id, remote_vault: 'owned-remote', endpoint: target.expected.endpoint, account: target.expected.account, cursor: 1 },
  paused: false, incoming_boundary: null, pending_outbox: 2, queued_jobs: 1, pending_decisions: 0, open_conflicts: 1, attempts: [], next_after: null, last_cycle_success: null,
  conflicts: [{ sequence: 3, file_id: ids[3]!, local_path: 'notes/原文.md', recorded_local_hash: 'd'.repeat(64), remote: {
    vault_id: 'owned-remote', sequence: 3, file_id: ids[3]!, base_revision: 1, path: 'notes/原文.md', operation: 'put', hash: 'e'.repeat(64), size: 1, operation_id: ids[4]!,
  } }] })
const result = (): CycleReply => ({ kind: 'result', response: { kind: 'status', status: status() } })
const content = (text: string) => ({ exists: true, text, hash: 'a'.repeat(64), byte_size: new TextEncoder().encode(text).length, preview_bytes: new TextEncoder().encode(text).length, truncated: false })
const release = vi.fn()
let wrapper: VueWrapper | undefined, receive: ((snapshot: CycleSnapshot) => void) | undefined
async function button(label: string) {
  const node = wrapper!.findAll('button').find(item => item.text() === label)
  expect(node, label).toBeDefined(); await node!.trigger('click'); await flushPromises()
}
beforeEach(() => {
  appLocale.value = 'zh-CN'; vi.clearAllMocks(); vi.spyOn(crypto, 'randomUUID').mockReturnValue(ids[9]! as ReturnType<typeof crypto.randomUUID>)
  vi.mocked(mobileOnSyncActivity).mockImplementation(async handler => { receive = handler; return release })
  vi.mocked(mobileSyncCycle).mockImplementation(async command => {
    if (command.action === 'stop') return { kind: 'stopped', state: 'cancelled' }
    if (command.action === 'activity') return { kind: 'activity', snapshot: { state: 'inactive' } }
    if (command.request.action === 'status' || command.request.action === 'now') return result()
    if (command.request.action === 'decision') return { kind: 'result', response: { kind: 'decision', decision: null } }
    if (command.request.action === 'review') return { kind: 'result', response: { kind: 'review', review: {
      vault_id: target.vault_id, binding_id: target.binding_id, sequence: 3, file_id: ids[3]!, local_file_id: ids[3]!,
      local_path: 'notes/原文.md', remote: { path: 'notes/原文.md', sequence: 3, base_revision: 1, operation: 'put' },
      local: content('中文\r\n'), incoming: content('<script>原文</script>'), base: content('共同基线'), related: [], fingerprint: 'b'.repeat(64),
    } } }
    throw new Error('SYNC_NETWORK_ERROR')
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; receive = undefined; vi.restoreAllMocks(); appLocale.value = 'zh-CN' })

it('reuses desktop activity and verified content, renders original text safely and starts no cycle on mount', async () => {
  wrapper = mount(MobileSyncCycle, { props: { target } }); await flushPromises()
  expect(wrapper.findComponent({ name: 'SyncActivity' }).exists()).toBe(true)
  expect(wrapper.text()).toContain('冲突记录摘要，并非当前文件摘要')
  expect(vi.mocked(mobileSyncCycle).mock.calls).toHaveLength(1)
  await button('读取原决策与正文差异')
  expect(wrapper.findComponent({ name: 'SyncConflictContent' }).exists()).toBe(true)
  expect(wrapper.text()).toContain('三方比较'); expect(wrapper.text()).toContain('基线 → 远端')
  expect(wrapper.text()).toContain('<script>原文</script>'); expect(wrapper.find('script').exists()).toBe(false)
  expect(wrapper.findAll('pre').map(node => node.element.textContent).join('')).toContain('中文\r\n')
  expect(vi.mocked(mobileSyncCycle).mock.calls.every(([command]) => command.action === 'run' && ['status', 'decision', 'review'].includes(command.request.action))).toBe(true)
})

it('keeps frozen body across language changes and reads a lost original before explicit retry', async () => {
  wrapper = mount(MobileSyncCycle, { props: { target } }); await flushPromises()
  await button('读取原决策与正文差异'); await button('采用远端')
  const codes = wrapper.get('dialog').findAll('code').map(node => node.text()), calls = vi.mocked(mobileSyncCycle).mock.calls.length
  appLocale.value = 'en'; await flushPromises()
  expect(wrapper.get('dialog').text()).toContain('Confirm this original content')
  expect(wrapper.get('dialog').findAll('code').map(node => node.text())).toEqual(codes)
  expect(vi.mocked(mobileSyncCycle).mock.calls).toHaveLength(calls)
  await button('Confirm this original content')
  const original = vi.mocked(mobileSyncCycle).mock.calls.at(-1)![0]
  expect(original).toEqual({ action: 'run', request: { action: 'resolve', target, sequence: 3, choice: 'remote', destination: '', expected_hash: 'a'.repeat(64), fingerprint: 'b'.repeat(64) } })
  expect(wrapper.text()).toContain('Original conflict decision')
  expect(wrapper.findAll('button').find(node => node.text() === 'Review and continue original decision')).toBeUndefined()
  await button('Read original decision')
  await button('Review and continue original decision')
  expect(vi.mocked(mobileSyncCycle).mock.calls.filter(([command]) => command.action === 'run' && command.request.action === 'resolve')).toHaveLength(1)
  await button('Confirm this original content')
  expect(vi.mocked(mobileSyncCycle).mock.calls.at(-1)![0]).toEqual(original)
})

it('closes a frozen review on identity change and releases exactly the owning event listener on unmount', async () => {
  wrapper = mount(MobileSyncCycle, { props: { target } }); await flushPromises()
  await button('读取原决策与正文差异'); await button('保留本地')
  expect(wrapper.find('dialog').exists()).toBe(true)
  await wrapper.setProps({ target: { ...target, expected: { ...target.expected, revision: 2 } } }); await flushPromises()
  expect(wrapper.find('dialog').exists()).toBe(false)
  expect(vi.mocked(mobileSyncCycle).mock.calls.some(([command]) => command.action === 'run' && command.request.action === 'resolve')).toBe(false)
  wrapper.unmount(); wrapper = undefined
  expect(release).toHaveBeenCalledOnce()
  receive?.({ state: 'inactive' })
})

it('keeps original Stop available during a pending cycle and refuses its late completion or progress', async () => {
  let complete!: (reply: CycleReply) => void
  const original = vi.mocked(mobileSyncCycle).getMockImplementation()!
  vi.mocked(mobileSyncCycle).mockImplementation(async command => command.action === 'run' && command.request.action === 'now'
    ? new Promise(resolve => { complete = resolve }) : original(command))
  wrapper = mount(MobileSyncCycle, { props: { target } }); await flushPromises(); await button('立即同步一轮')
  const snapshot: CycleSnapshot = { state: 'known', observation: { target, invocation_id: ids[9]!, generation: 1, error: null,
    activity: { running: true, phase: 'pull', transfer: null, uploaded_revisions: 1, received_revisions: 2, started_at: 100, finished_at: null } } }
  receive?.(snapshot); await flushPromises(); await button('停止本轮')
  expect(wrapper.text()).toContain('本轮已停止')
  receive?.(snapshot); complete(result()); await flushPromises()
  expect(wrapper.text()).toContain('本轮已停止')
  expect(vi.mocked(mobileSyncCycle).mock.calls.find(([command]) => command.action === 'stop')?.[0]).toEqual({ action: 'stop', target, invocation_id: ids[9] })
  expect(wrapper.findAll('button').find(node => node.text() === '立即同步一轮')?.attributes('disabled')).toBeDefined()
})

it('releases a listener that arrives after unmount and never creates a stop or transfer as cleanup', async () => {
  let attached!: (release: () => void) => void
  vi.mocked(mobileOnSyncActivity).mockImplementation(() => new Promise(resolve => { attached = resolve }))
  wrapper = mount(MobileSyncCycle, { props: { target } }); await flushPromises(); wrapper.unmount(); wrapper = undefined
  attached(release); await flushPromises()
  expect(release).toHaveBeenCalledOnce()
  expect(vi.mocked(mobileSyncCycle).mock.calls.every(([command]) => command.action === 'run' && command.request.action === 'status')).toBe(true)
})
