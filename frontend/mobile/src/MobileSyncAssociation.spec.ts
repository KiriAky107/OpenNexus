// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { appLocale } from '../../src/i18n'
import { installTestDialogApi } from './dialogTestSupport'
import MobileSyncAssociation from './MobileSyncAssociation.vue'
import { mobileSyncAssociation } from './native'
import type { AuthSelection } from './syncAuthTypes'
import type { AssociationRequest, BindingIntent, BindingRequest } from './syncAssociationTypes'

vi.mock('./native', () => ({ mobileSyncAssociation: vi.fn() }))
installTestDialogApi()
const selection: AuthSelection = { revision: 1, endpoint: 'https://sync.example/', account: 'owned-a', operation_id: '00000000-0000-4000-8000-000000000001', generation: 1 }
const local = '10000000-0000-4000-8000-000000000001', id = '20000000-0000-4000-8000-000000000001'
const remote = { id: 'remote-a', name: '<b>原名称 😀</b>', sequence: 1, used: 12, quota: 100 }
const empty = { vault_id: local, binding: null, original_operation_id: null, another_connection: false }
let wrapper: VueWrapper | undefined
async function button(label: string) {
  const node = wrapper!.findAll('button').find(value => value.text() === label)
  expect(node, label).toBeDefined(); await node!.trigger('click'); await flushPromises()
}
beforeEach(() => {
  appLocale.value = 'zh-CN'; vi.clearAllMocks(); vi.spyOn(crypto, 'randomUUID').mockReturnValue(id)
  vi.mocked(mobileSyncAssociation).mockImplementation(async command => {
    if (command.action === 'status') return { kind: 'status', status: empty }
    if (command.action === 'preview') return { kind: 'preview', page: { selection, local_vault: local, remote_vault: remote.id, fingerprint: 'a'.repeat(64), boundary: 1,
      total: 1, offset: 0, limit: 100, items: [{ path: '<script>原路径.md', action: 'conflict' }] } }
    throw new Error('Unexpected command')
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.restoreAllMocks() })

it('uses the desktop preview and explicit frozen confirmation while preserving raw names as text', async () => {
  wrapper = mount(MobileSyncAssociation, { props: { selection, authenticated: true, remoteVault: remote } }); await flushPromises()
  await button('预览合并')
  expect(wrapper.findComponent({ name: 'SyncInitialPreview' }).exists()).toBe(true)
  expect(wrapper.text()).toContain(remote.name); expect(wrapper.text()).toContain('<script>原路径.md')
  expect(wrapper.find('script').exists()).toBe(false); expect(wrapper.find('b').exists()).toBe(false)
  await button('确认合并并绑定')
  expect(wrapper.get('dialog').text()).toContain(id)
  expect(vi.mocked(mobileSyncAssociation).mock.calls.map(([command]) => command.action)).toEqual(['status', 'preview'])
  await button('取消')
  expect(wrapper.find('dialog').exists()).toBe(false)
})

it('queries a lost original then resumes its frozen content and revision only after another confirmation', async () => {
  let original: BindingRequest | undefined, resumed = false
  const base = vi.mocked(mobileSyncAssociation).getMockImplementation()!
  const receipt = (): BindingIntent => ({ request: original!, phase: resumed ? 'bound' : 'prepared', attempt_revision: resumed ? 2 : 1,
    binding: resumed ? { id: '30000000-0000-4000-8000-000000000001', endpoint: selection.endpoint, account: selection.account, remote_vault: remote.id, cursor: 0 } : null })
  vi.mocked(mobileSyncAssociation).mockImplementation(async command => {
    if (command.action === 'bind') { original = command.request; throw new Error('SYNC_NETWORK_ERROR') }
    if (command.action === 'operation') return { kind: 'operation', intent: receipt() }
    if (command.action === 'resume') { expect(command).toEqual({ action: 'resume', request: original, observed_revision: 1 }); resumed = true; return { kind: 'operation', intent: receipt() } }
    if (command.action === 'status' && resumed) return { kind: 'status', status: { ...empty, binding: receipt().binding, original_operation_id: id } }
    return base(command)
  })
  wrapper = mount(MobileSyncAssociation, { props: { selection, authenticated: true, remoteVault: remote } }); await flushPromises()
  await button('预览合并'); await button('确认合并并绑定'); await button('确认此关联')
  expect(wrapper.text()).toContain(id)
  expect(vi.mocked(mobileSyncAssociation).mock.calls.filter(([command]) => command.action === 'bind')).toHaveLength(1)
  await button('只读查询原编号'); await button('审核并继续原关联')
  expect(vi.mocked(mobileSyncAssociation).mock.calls.some(([command]) => command.action === 'resume')).toBe(false)
  await button('确认此关联')
  expect(resumed).toBe(true); expect(wrapper.text()).toContain('原关联已提交')
})

it('changes labels without changing the frozen body or making an API request', async () => {
  wrapper = mount(MobileSyncAssociation, { props: { selection, authenticated: true, remoteVault: remote } }); await flushPromises()
  await button('预览合并'); await button('确认合并并绑定')
  const codes = wrapper.get('dialog').findAll('code').map(code => code.text())
  const calls = vi.mocked(mobileSyncAssociation).mock.calls.length
  appLocale.value = 'en'; await flushPromises()
  expect(wrapper.get('dialog').text()).toContain('Confirm this association')
  expect(wrapper.get('dialog').findAll('code').map(code => code.text())).toEqual(codes)
  expect(vi.mocked(mobileSyncAssociation).mock.calls).toHaveLength(calls)
})
