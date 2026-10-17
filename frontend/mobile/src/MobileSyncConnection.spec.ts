// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { appLocale } from '../../src/i18n'
import { installTestDialogApi } from './dialogTestSupport'
import MobileSyncConnection from './MobileSyncConnection.vue'
import { mobileSyncAuth } from './native'
import type { AuthIntent, AuthRequest, AuthResponse, AuthSelection } from './syncAuthTypes'

vi.mock('./native', () => ({ mobileSyncAuth: vi.fn() }))
// Association has its own native/session and component checks below this layer.
vi.mock('./MobileSyncAssociation.vue', () => ({ default: { template: '<div />' } }))
installTestDialogApi()
const first = '00000000-0000-4000-8000-000000000001'
const selection: AuthSelection = { revision: 1, endpoint: 'https://sync.example/', account: 'reader-a', operation_id: first, generation: 1 }
const original: AuthIntent = { ...selection, kind: 'login', phase: 'active', device_name: 'Owned phone', device_id: 'device-a',
  previous_operation: null, server_logout_confirmed: false }
const current = (): AuthResponse => ({ kind: 'current', selection: { ...selection }, state: { intent: { ...original },
  authenticated: true, expired: false, credential_change_required: false, expires_at: 1000, rejected: null } })
const profiles = (): AuthResponse => ({ kind: 'profiles', profiles: { items: [{ intent: { ...original }, rejected: null, selected: true }],
  offset: 0, limit: 20, total: 1, selection: { ...selection } } })
let wrapper: VueWrapper | undefined
async function button(text: string) {
  const value = wrapper!.findAll('button').find(node => node.text() === text)
  expect(value, text).toBeDefined(); await value!.trigger('click'); await flushPromises()
}
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear(); sessionStorage.clear(); appLocale.value = 'en'
  vi.mocked(mobileSyncAuth).mockImplementation(async request => request.action === 'cancel' ? { kind: 'cancelled' }
    : request.action === 'profiles' ? profiles() : current())
})
afterEach(async () => { wrapper?.unmount(); wrapper = undefined; await flushPromises() })

it('uses the desktop form and clears its password before the native call, with no identity storage', async () => {
  wrapper = mount(MobileSyncConnection); await flushPromises()
  const readonly = vi.mocked(mobileSyncAuth).getMockImplementation()!
  vi.mocked(mobileSyncAuth).mockImplementation(async request => {
    if (request.action !== 'login') return readonly(request)
    expect((wrapper!.get('input[type=password]').element as HTMLInputElement).value).toBe('')
    expect(request.password).toBe('private-original-password')
    expect(request.expected).toEqual(selection)
    return { kind: 'current', selection: { ...selection, operation_id: request.operation_id, revision: 2 },
      state: { ...(current() as Extract<AuthResponse,{kind:'current'}>).state,
        intent: { ...original, operation_id: request.operation_id } } }
  })
  await wrapper.get('input[type=password]').setValue('private-original-password')
  await wrapper.get('form.sync-form').trigger('submit'); await flushPromises()
  expect(mobileSyncAuth).toHaveBeenCalledWith(expect.objectContaining({ action: 'login', endpoint: selection.endpoint, account: selection.account }))
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
  expect(wrapper.text()).not.toContain('private-original-password')
})
it('queries a lost login by its exact original ID and displays a known rejection without resending', async () => {
  let sent: Extract<AuthRequest,{action:'login'}> | undefined
  wrapper = mount(MobileSyncConnection); await flushPromises()
  vi.mocked(mobileSyncAuth).mockImplementation(async request => {
    if (request.action === 'cancel') return {kind:'cancelled'}
    if (request.action === 'login') { sent=request; throw 'SYNC_NETWORK_ERROR' }
    if (request.action === 'operation') return {kind:'operation',state:{intent:{...original,operation_id:request.operation_id,phase:'unknown'},
      authenticated:false,expired:false,credential_change_required:false,expires_at:null,rejected:{status:401,code:'LOGIN_FAILED'}}}
    return current()
  })
  await wrapper.get('input[type=password]').setValue('private-original-password')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  const id=sent!.operation_id
  expect(wrapper.text()).toContain(id)
  await button('Read original ID')
  expect(mobileSyncAuth).toHaveBeenCalledWith({action:'operation',endpoint:selection.endpoint,account:selection.account,operation_id:id})
  expect(vi.mocked(mobileSyncAuth).mock.calls.filter(([r])=>r.action==='login')).toHaveLength(1)
  expect(wrapper.text()).toContain('The account or password is incorrect.')
  expect(wrapper.get('input[type=password]').attributes('disabled')).toBeDefined()
})
it('freezes sign-out identity across a language change and claims server logout only after its actual receipt', async () => {
  wrapper=mount(MobileSyncConnection); await flushPromises()
  await button('Sign out on server')
  expect(wrapper.find('.connection-confirm').exists()).toBe(true)
  const before=vi.mocked(mobileSyncAuth).mock.calls.length
  appLocale.value='zh-CN'; await flushPromises()
  expect(vi.mocked(mobileSyncAuth).mock.calls).toHaveLength(before)
  expect(wrapper.get('.connection-confirm').text()).toContain(selection.endpoint)
  vi.mocked(mobileSyncAuth).mockImplementation(async request=>{
    if(request.action==='cancel')return {kind:'cancelled'}
    if(request.action!=='logout')return current()
    expect(request.expected).toEqual(selection)
    expect(request.previous_operation).toBe(first)
    return {kind:'current',selection:{...selection,revision:2,generation:2,operation_id:request.operation_id},
      state:{intent:{...original,kind:'logout',generation:2,operation_id:request.operation_id,previous_operation:first,
        phase:'disconnected',server_logout_confirmed:true},authenticated:false,expired:false,credential_change_required:false,
        expires_at:null,rejected:null}}
  })
  await button('确认此操作')
  expect(wrapper.text()).toContain('服务已确认原设备会话退出')
})
it('clears entered credentials on scope edit and unmount, and ignores a late login without keeping a session', async () => {
  wrapper=mount(MobileSyncConnection); await flushPromises()
  await wrapper.get('input[type=password]').setValue('private-before-scope-edit')
  await wrapper.get('input[autocomplete=username]').setValue('reader-b')
  expect((wrapper.get('input[type=password]').element as HTMLInputElement).value).toBe('')
  let complete!: (reply:AuthResponse)=>void
  const wait=new Promise<AuthResponse>(resolve=>complete=resolve)
  const read=vi.mocked(mobileSyncAuth).getMockImplementation()!
  vi.mocked(mobileSyncAuth).mockImplementation(request=>request.action==='login'?wait:read(request))
  await wrapper.get('input[type=password]').setValue('private-for-pending-request')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  wrapper.unmount(); wrapper=undefined; await flushPromises()
  expect(mobileSyncAuth).toHaveBeenCalledWith({action:'cancel'})
  complete(current()); await flushPromises()
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
})
it('shows initial credential setup separately from an authenticated session', async () => {
  vi.mocked(mobileSyncAuth).mockImplementation(async request=>{
    if(request.action==='cancel')return {kind:'cancelled'}
    if(request.action==='profiles')return profiles()
    const result=current();if(result.kind==='current')result.state.credential_change_required=true
    return result
  })
  wrapper=mount(MobileSyncConnection);await flushPromises()
  expect(wrapper.text()).toContain('Complete initial password setup on your Sync service, then reconnect.')
  expect(wrapper.text()).toContain('Signed in')
})
it('reuses the desktop vault selector without a transfer, HTML rendering or language-triggered request', async () => {
  vi.mocked(mobileSyncAuth).mockImplementation(async request => request.action === 'cancel' ? { kind: 'cancelled' }
    : request.action === 'profiles' ? profiles() : request.action === 'vaults'
      ? { kind: 'vaults', page: { selection, total: 1, offset: 0, limit: 20,
        items: [{ id: 'remote-a', name: '<script>原知识库 😀</script>', sequence: 3, used: 12, quota: 100 }] } } : current())
  wrapper = mount(MobileSyncConnection); await flushPromises()
  expect(vi.mocked(mobileSyncAuth).mock.calls.some(([request]) => request.action === 'vaults')).toBe(false)
  await button('Read remote vaults')
  const picker = wrapper.get('.sync-vault-selector select')
  await picker.setValue('remote-a')
  const count = vi.mocked(mobileSyncAuth).mock.calls.length
  appLocale.value = 'zh-CN'; await flushPromises()
  expect(vi.mocked(mobileSyncAuth).mock.calls).toHaveLength(count)
  expect((picker.element as HTMLSelectElement).value).toBe('remote-a')
  expect(wrapper.get('.sync-vault-selector').text()).toContain('<script>原知识库 😀</script>')
  expect(wrapper.findAll('script')).toHaveLength(0)
  expect(vi.mocked(mobileSyncAuth).mock.calls.filter(([request]) => request.action === 'vaults')).toHaveLength(1)
})
