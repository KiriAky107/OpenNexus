// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { appLocale } from '../../src/i18n'
import { installTestDialogApi } from './dialogTestSupport'
import MobileSyncCycle from './MobileSyncCycle.vue'
import { mobileOnSyncActivity, mobileSyncCycle } from './native'
import { createCycleSession } from './syncCycleSession'
import type { CycleReply, CycleStatus, NativeCycle, OriginalDecisionPage, SyncDecision, SyncTarget } from './syncCycleTypes'

vi.mock('./native', () => ({ mobileSyncCycle: vi.fn(), mobileOnSyncActivity: vi.fn() }))
installTestDialogApi()
const uuid = (value: number) => `00000000-0000-4000-8000-${String(value).padStart(12, '0')}`
const owner: SyncTarget = { vault_id: uuid(1), binding_id: uuid(2), expected: {
  revision: 1, generation: 1, operation_id: uuid(3), endpoint: 'https://sync.example/', account: 'owned-reader',
} }
const status = (): CycleStatus => ({ vault_id: owner.vault_id, binding: { id: owner.binding_id, remote_vault: 'owned-remote',
  endpoint: owner.expected.endpoint, account: owner.expected.account, cursor: 30 }, paused: false, incoming_boundary: null,
  pending_outbox: 0, queued_jobs: 0, pending_decisions: 0, open_conflicts: 0, attempts: [], conflicts: [], next_after: null, last_cycle_success: 100 })
const decision = (sequence = 2): SyncDecision => ({ sequence, choice: 'copy', destination: 'notes/<script>原文</script>-copy.md',
  expected_hash: 'a'.repeat(64), fingerprint: 'b'.repeat(64), operation_id: uuid(sequence * 10 + 1), rename_id: uuid(sequence * 10 + 2),
  copy_id: uuid(sequence * 10 + 3), retire_id: uuid(sequence * 10 + 4), restore_id: uuid(sequence * 10 + 5), state: 'completed' })
const page = (items = [decision()], before: number | null = null, next_before: number | null = null): OriginalDecisionPage => ({
  vault_id: owner.vault_id, binding_id: owner.binding_id, before, limit: 20, items, next_before,
})
const reply = (value: OriginalDecisionPage): CycleReply => ({ kind: 'result', response: { kind: 'originals', page: value } })
function driver(original = decision()) {
  return vi.fn<NativeCycle>(async command => {
    if (command.action !== 'run') throw new Error('Unexpected activity or stop')
    if (command.request.action === 'status') return { kind: 'result', response: { kind: 'status', status: status() } }
    if (command.request.action === 'originals') return reply(page([original], command.request.before))
    if (command.request.action === 'decision') return { kind: 'result', response: { kind: 'decision', decision: original } }
    throw new Error('Unexpected mutation or review')
  })
}
let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); wrapper = undefined; appLocale.value = 'zh-CN'; vi.clearAllMocks(); vi.restoreAllMocks() })
async function button(label: string) {
  const value = wrapper!.findAll('button').find(node => node.text() === label)
  expect(value, label).toBeDefined(); await value!.trigger('click'); await flushPromises()
}

it('reads a completed original from a cold view with no open conflicts and never repeats its decision', async () => {
  appLocale.value = 'en'; const native = driver(), original = decision()
  vi.mocked(mobileSyncCycle).mockImplementation(native); vi.mocked(mobileOnSyncActivity).mockResolvedValue(() => undefined)
  wrapper = mount(MobileSyncCycle, { props: { target: owner } }); await flushPromises()
  expect(wrapper.text()).toContain('Conflicts to review 0')
  expect(native.mock.calls).toHaveLength(1)
  await button('Read original decisions'); await button('Read this original decision')
  expect(wrapper.text()).toContain('The original local decision completed.')
  for (const id of [original.operation_id, original.rename_id, original.copy_id, original.retire_id, original.restore_id]) expect(wrapper.text()).toContain(id)
  expect(wrapper.findAll('button').find(node => node.text() === 'Review and continue original decision')).toBeUndefined()
  expect(native.mock.calls.map(([command]) => command.action === 'run' && command.request.action)).toEqual(['status', 'originals', 'decision'])
})

it('keeps a legacy original read-only and escapes retained paths across language changes', async () => {
  appLocale.value = 'en'; const native = driver({ ...decision(), fingerprint: null, state: 'pending' })
  vi.mocked(mobileSyncCycle).mockImplementation(native); vi.mocked(mobileOnSyncActivity).mockResolvedValue(() => undefined)
  wrapper = mount(MobileSyncCycle, { props: { target: owner } }); await flushPromises()
  await button('Read original decisions'); expect(wrapper.text()).toContain('notes/<script>原文</script>-copy.md')
  expect(wrapper.find('script').exists()).toBe(false)
  await button('Read this original decision')
  expect(wrapper.text()).toContain('This legacy decision has no frozen review digest.')
  const count = native.mock.calls.length; appLocale.value = 'zh-CN'; await flushPromises()
  expect(wrapper.text()).toContain('旧决策没有冻结审核摘要')
  expect(wrapper.findAll('button').find(node => node.text() === '审核并继续原决策')).toBeUndefined()
  expect(native.mock.calls).toHaveLength(count)
})

it('pages original decisions in descending order using their immutable sequence without allocating IDs', async () => {
  const native = driver(), allocate = vi.fn(() => uuid(999)), session = createCycleSession(native, allocate)
  session.context(owner)
  native.mockImplementation(async command => {
    if (command.action !== 'run' || command.request.action !== 'originals') throw new Error('Unexpected action')
    return command.request.before === null ? reply(page(Array.from({ length: 20 }, (_, i) => decision(21 - i)), null, 2)) : reply(page([decision(1)], 2))
  })
  expect(await session.readOriginals()).toBe(true); expect(session.state.originals?.next_before).toBe(2)
  expect(await session.readOriginals(2)).toBe(true); expect(session.state.originals?.items[0]?.sequence).toBe(1)
  expect(session.state.originals?.next_before).toBeNull(); expect(allocate).not.toHaveBeenCalled()
  expect(native.mock.calls.map(([command]) => command)).toEqual([
    { action: 'run', request: { action: 'originals', target: owner, before: null, limit: 20 } },
    { action: 'run', request: { action: 'originals', target: owner, before: 2, limit: 20 } },
  ])
})

it('rejects wrong scope, invalid ordering, changed IDs and unsupported page metadata', async () => {
  const variants = [
    { ...page(), vault_id: uuid(999) }, { ...page(), binding_id: uuid(999) }, { ...page(), limit: 21 },
    { ...page(), before: 3 }, page([decision(2), decision(2)]), page([decision(1), decision(2)]),
    page([decision()], null, 2), page(Array.from({ length: 21 }, (_, i) => decision(30 - i))),
    page([{ ...decision(), copy_id: decision().operation_id }]), page([{ ...decision(), fingerprint: 'B'.repeat(64) }]),
  ]
  for (const value of variants) {
    const native = vi.fn<NativeCycle>(async () => reply(value)), session = createCycleSession(native)
    session.context(owner); expect(await session.readOriginals()).toBe(false)
    expect(session.state.originals).toBeNull(); expect(session.state.error).toBe('SYNC_RESPONSE_INVALID')
  }
})

it('refuses invalid cursors before native access and ignores a previous owner’s late originals page', async () => {
  let complete!: (reply: CycleReply) => void
  const native = vi.fn<NativeCycle>(() => new Promise(resolve => { complete = resolve })), session = createCycleSession(native)
  session.context(owner)
  for (const value of [0, -1, NaN, Number.MAX_SAFE_INTEGER + 1]) expect(await session.readOriginals(value)).toBe(false)
  expect(native).not.toHaveBeenCalled()
  const pending = session.readOriginals(); session.context({ ...owner, expected: { ...owner.expected, revision: 2, account: 'other-owned-reader' } })
  complete(reply(page())); expect(await pending).toBe(false)
  expect(session.state.originals).toBeNull(); expect(session.state.original).toBeNull(); expect(session.state.busy).toBe(false)
})
