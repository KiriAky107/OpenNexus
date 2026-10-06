// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import { hostInvoke } from '@/services/platform/desktop'
import SyncSettings from './SyncSettings.vue'
vi.mock('@/services/platform/preferenceSync', () => ({ preferenceSyncIssues: [], resolvePreferenceDraft: vi.fn(), seedCurrentPreferences: vi.fn() }))
vi.mock('@/services/platform/desktop', () => ({ hostInvoke: vi.fn() }))
vi.mock('@/stores/editor', () => ({ useEditorStore: () => ({ currentFilePath: '', saveStatus: 'saved' }) }))
const confirm = vi.hoisted(() => vi.fn())
vi.mock('@/composables/useActionDialog', () => ({ useActionDialog: () => ({ actionDialog: null, resolveAction: vi.fn(), askConfirm: confirm }) }))
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks() })
const empty = () => ({ vault_id: 'local', binding: null, paused: false, pending: 0, conflicts: [], credential_state: 'unbound', running: false, error: null, retry_in: null })
it('does not let an older status request overwrite a newer vault and binding', async () => {
  vi.useFakeTimers()
  const answers: Array<(value: unknown) => void> = []
  vi.mocked(hostInvoke).mockImplementation(command => command === 'sync_status' ? new Promise(resolve => { answers.push(resolve) }) : Promise.resolve(undefined))
  const wrapper = mount(SyncSettings)
  await vi.advanceTimersByTimeAsync(1500)
  expect(answers).toHaveLength(2)
  const bound = (id: string) => ({ ...empty(), vault_id: id, credential_state: 'ready', binding: { id, endpoint: `https://${id}.example/`, account: id, remote_vault: id, cursor: 0 } })
  answers[1](bound('new')); await flushPromises()
  answers[0](bound('old')); await flushPromises()
  expect(wrapper.text()).toContain('https://new.example/'); expect(wrapper.text()).not.toContain('https://old.example/')
  wrapper.unmount()
})
it('clears login password, explicitly opts into HTTP and stops polling on unmount', async () => {
  vi.useFakeTimers()
  vi.mocked(hostInvoke).mockImplementation(async command => command === 'sync_status' ? empty() : command === 'sync_login' ? { endpoint: 'http://test.example:18080/', account: 'test' } : { items: [] })
  const wrapper = mount(SyncSettings); await flushPromises()
  await wrapper.get('input[type=url]').setValue('http://test.example:18080')
  await wrapper.get('input[autocomplete=username]').setValue('test')
  await wrapper.get('input[type=password]').setValue('private-fixture')
  await wrapper.get('input[type=checkbox]').setValue(true)
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(hostInvoke).toHaveBeenCalledWith('sync_login', { request: { endpoint: 'http://test.example:18080', account: 'test', password: 'private-fixture', device_name: 'OpenNexus Desktop', allow_test_http: true } })
  expect((wrapper.get('input[type=password]').element as HTMLInputElement).value).toBe('')
  expect(wrapper.text()).not.toContain('private-fixture')
  wrapper.unmount(); const count = vi.mocked(hostInvoke).mock.calls.length
  await vi.advanceTimersByTimeAsync(6000); expect(hostInvoke).toHaveBeenCalledTimes(count)
})
it('cancels destructive choices and binds accepted conflict decisions to their snapshot', async () => {
  const state = { ...empty(), binding: { id: 'binding', endpoint: 'https://test.example/', account: 'test', remote_vault: 'remote', cursor: 7 }, credential_state: 'ready', conflicts: [{ sequence: 7, local_path: 'note.md', local_hash: 'original-hash', remote: { path: 'note.md', operation: 'put' } }] }
  const review = { vault_id: 'local', binding_id: 'binding', sequence: 7, local_path: 'note.md', local_file_id: 'file', related: [], remote: { path: 'note.md', base_revision: 0 }, local: { exists: true, hash: 'original-hash', byte_size: 5, text: 'local', preview_bytes: 5, truncated: false }, incoming: { exists: true, hash: 'remote-hash', byte_size: 6, text: 'remote', preview_bytes: 6, truncated: false }, base: null, fingerprint: 'reviewed' }
  vi.mocked(hostInvoke).mockImplementation(async command => command === 'sync_conflict_review' ? review : command === 'sync_status' ? state : undefined)
  const wrapper = mount(SyncSettings); await flushPromises()
  expect(wrapper.text()).not.toContain('采用远端')
  await wrapper.findAll('button').find(button => button.text() === '读取正文与差异')!.trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('两方比较')
  const button = wrapper.findAll('button').find(button => button.text() === '采用远端')!
  confirm.mockResolvedValue(false); await button.trigger('click'); await flushPromises()
  expect(vi.mocked(hostInvoke).mock.calls.some(([command]) => command === 'sync_resolve')).toBe(false)
  confirm.mockResolvedValue(true); await button.trigger('click'); await flushPromises()
  expect(hostInvoke).toHaveBeenCalledWith('sync_resolve', { request: { vault_id: 'local', binding_id: 'binding', sequence: 7, choice: 'remote', destination: '', expected: 'original-hash', fingerprint: 'reviewed' } })
  expect(wrapper.get('input[type=url]').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})

it('requires a reviewed merge fingerprint and invalidates preview when the remote changes', async () => {
  vi.mocked(hostInvoke).mockImplementation(async command => {
    if (command === 'sync_status') return empty()
    if (command === 'sync_login') return { endpoint: 'https://test.example/', account: 'test' }
    if (command === 'sync_vaults') return { items: [{ id: 'one', name: 'One', sequence: 2 }, { id: 'two', name: 'Two', sequence: 3 }] }
    if (command === 'sync_preview') return { fingerprint: 'reviewed-snapshot', boundary: 2, items: [{ path: 'same.md', action: 'conflict' }] }
    return null
  })
  const wrapper = mount(SyncSettings); await flushPromises()
  await wrapper.get('input[type=url]').setValue('https://test.example/')
  await wrapper.get('input[autocomplete=username]').setValue('test')
  await wrapper.get('input[type=password]').setValue('fixture')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  await wrapper.get('select').setValue('one')
  await wrapper.findAll('button').find(button => button.text() === '预览合并')!.trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('same.md')
  await wrapper.get('select').setValue('two')
  expect(wrapper.text()).not.toContain('same.md')
  await wrapper.get('select').setValue('one')
  await wrapper.findAll('button').find(button => button.text() === '预览合并')!.trigger('click'); await flushPromises()
  confirm.mockResolvedValue(true)
  await wrapper.findAll('button').find(button => button.text() === '确认合并并绑定')!.trigger('click'); await flushPromises()
  expect(hostInvoke).toHaveBeenCalledWith('sync_bind', { request: { vault_id: 'local', endpoint: 'https://test.example/', account: 'test', remote_vault: 'one', mode: 'merge', fingerprint: 'reviewed-snapshot' } })
  wrapper.unmount()
})

it('shows a persisted halt without suggesting an automatic retry countdown', async () => {
  vi.mocked(hostInvoke).mockResolvedValue({ ...empty(), binding: { id: 'binding', endpoint: 'https://test.example/', account: 'test', remote_vault: 'remote', cursor: 7 }, halted: true, failures: 3, error: 'UNAUTHORIZED', retry_in: 120 })
  const wrapper = mount(SyncSettings); await flushPromises()
  expect(wrapper.text()).toContain('自动同步已停止')
  expect(wrapper.text()).toContain('UNAUTHORIZED')
  expect(wrapper.text()).not.toContain('120s')
  wrapper.unmount()
})

it('shows persisted per-job interruption counts without starting work from the view', async () => {
  vi.mocked(hostInvoke).mockResolvedValue({ ...empty(), binding: { id: 'binding', endpoint: 'https://test.example/', account: 'test', remote_vault: 'remote', cursor: 7 }, attempts: [{ operation_id: 'op', path: 'attachments/large.pdf', attempts: 11, outcome: 'interrupted', error: null }] })
  const wrapper = mount(SyncSettings); await flushPromises()
  expect(wrapper.text()).toContain('attachments/large.pdf')
  expect(wrapper.text()).toContain('尝试次数 11')
  expect(wrapper.text()).toContain('从已确认位置恢复')
  expect(vi.mocked(hostInvoke).mock.calls.every(([command]) => command === 'sync_status')).toBe(true)
  wrapper.unmount()
})


it('keeps every optional record class off until an explicit unbound scope change', async () => {
  const optionalScope = { persona: false, layout: false, conversations: false, agent_history: false, provider_settings: false, extension_installations: false }
  vi.mocked(hostInvoke).mockResolvedValue({ ...empty(), optional_scope: optionalScope })
  const wrapper = mount(SyncSettings); await flushPromises()
  const options = wrapper.findAll('.sync-scope input[type=checkbox]')
  expect(options).toHaveLength(6)
  expect(options.every(option => !(option.element as HTMLInputElement).checked)).toBe(true)
  await options[0]!.setValue(true); await flushPromises()
  expect(hostInvoke).toHaveBeenCalledWith('sync_set_scope', { vaultId: empty().vault_id, scope: { ...optionalScope, persona: true } })
  wrapper.unmount()
})
