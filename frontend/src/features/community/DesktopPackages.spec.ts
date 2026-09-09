// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
const native = vi.hoisted(() => ({ invoke: vi.fn(), workspace: { vaultId: 'vault-one' } }))
vi.mock('@/services/platform/desktop', () => ({ hostInvoke: native.invoke }))
vi.mock('@/stores/workspace', async () => {
  const { reactive } = await import('vue')
  native.workspace = reactive(native.workspace)
  return { useWorkspaceStore: () => native.workspace }
})
import DesktopPackages from './DesktopPackages.vue'
const item = { package_key: 'package-key', source: 'https://example.com/', namespace: 'examples', package_id: 'reviewer', version: '1.0.0', state: 'staged' }
beforeEach(() => { native.invoke.mockReset(); native.workspace.vaultId = 'vault-one' })
function component() { return mount(DesktopPackages, { props: { refreshKey: 0 }, global: { stubs: { AppDialog: { template: '<section><slot /></section>' } } } }) }
it('uses themed surfaces for the section, empty state, and staged package rows', async () => {
  native.invoke.mockResolvedValueOnce([])
  const empty = component(); await flushPromises()
  expect(empty.get('section.desktop-packages').classes()).toContain('panel')
  expect(empty.get('.empty-state').text()).toContain('本页没有暂存包')
  empty.unmount()

  native.invoke.mockResolvedValueOnce([item])
  const populated = component(); await flushPromises()
  expect(populated.get('.package-list > li').classes()).toContain('item-card')
  populated.unmount()
})
it('loads durable staged metadata and previews without issuing install commands', async () => {
  native.invoke.mockImplementation(async command => command === 'extension_staged' ? [item] : {
    fingerprint: 'fingerprint', dependencies: { packages: [{ ...item, permissions: ['notes.read'] }] }, changes: [{ target: { configuration: {} }, expected_revision: null }],
  })
  const wrapper = component(); await flushPromises()
  expect(native.invoke).toHaveBeenCalledWith('extension_staged', { offset: 0, limit: 20 })
  await wrapper.findAll('button').find(b => b.text() === '查看安装预览')!.trigger('click')
  await wrapper.findAll('button').find(b => b.text() === '检查依赖、权限与配置')!.trigger('click')
  await flushPromises()
  expect(native.invoke).toHaveBeenLastCalledWith('extension_install_preview', { request: { root_key: 'package-key', vault_id: 'vault-one', configurations: { 'package-key': {} } } })
  expect(wrapper.text()).toContain('notes.read')
  expect(wrapper.text()).toContain('安装执行暂未开放')
  expect(native.invoke.mock.calls.every(call => ['extension_staged', 'extension_install_preview'].includes(call[0]))).toBe(true)
  wrapper.unmount()
})
it('discards delayed preview after switching Vault', async () => {
  let resolve!: (value: unknown) => void
  native.invoke.mockImplementation(command => command === 'extension_staged' ? Promise.resolve([item]) : new Promise(done => { resolve = done }))
  const wrapper = component(); await flushPromises()
  await wrapper.findAll('button').find(b => b.text() === '查看安装预览')!.trigger('click')
  await wrapper.findAll('button').find(b => b.text() === '检查依赖、权限与配置')!.trigger('click')
  native.workspace.vaultId = 'vault-two'; await flushPromises()
  resolve({ dependencies: { packages: [{ ...item, permissions: ['stale-permission'] }] }, changes: [] }); await flushPromises()
  expect(wrapper.text()).not.toContain('stale-permission')
  expect(wrapper.find('textarea').exists()).toBe(false)
  wrapper.unmount()
})
