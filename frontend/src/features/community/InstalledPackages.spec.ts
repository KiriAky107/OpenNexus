// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import vector from '@/services/fixtures/community-python-vector.json'
const controls=vi.hoisted(()=>({invoke:vi.fn(),update:vi.fn(),workspace:{vaultId:'vault-one'}}))
vi.mock('@/services/platform/desktop',()=>({hostInvoke:controls.invoke}))
vi.mock('@/services/communityService',()=>({findCompatibleUpdate:controls.update,loadSources:()=>[]}))
vi.mock('@/stores/workspace',async()=>{const {reactive}=await import('vue');controls.workspace=reactive(controls.workspace);return {useWorkspaceStore:()=>controls.workspace}})
import InstalledPackages from './InstalledPackages.vue'
const source={id:'source',url:'https://catalog.example/',enabled:true,source_id:'fixture',keys:[vector.key]}
const item={slot:'slot',package_key:'package',source:source.url,release:vector.release,revision:'revision',pending_operation:null,configuration:{},rollback_operation_id:'old-update-operation'}
const page={items:[item],total:1,offset:0,limit:20,app_version:'0.6.0',platform:'windows',architecture:'x86_64'}
function component(){return mount(InstalledPackages,{props:{refreshKey:0,sources:[source]},global:{stubs:{AppDialog:{template:'<section><slot /></section>'}}}})}
beforeEach(()=>{controls.invoke.mockReset();controls.update.mockReset();controls.workspace.vaultId='vault-one'})

it('shows the native vault-bound installed version and incomplete health state without claiming it is running',async()=>{
  controls.invoke.mockResolvedValue({...page,items:[{...item,pending_operation:'checking-operation'}]})
  const wrapper=component();await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_installed',{vaultId:'vault-one',offset:0,limit:20})
  expect(wrapper.text()).toContain('安装等待健康检查，尚未完成')
  expect(wrapper.findAll('button').find(button=>button.text()==='查询兼容更新')!.attributes('disabled')).toBeDefined()
  expect(controls.update).not.toHaveBeenCalled();wrapper.unmount()
})

it('shows before/after version, changelog and permission changes without installing or enabling',async()=>{
  controls.invoke.mockResolvedValue(page)
  controls.update.mockResolvedValue({...vector.release,version:'2.0.0',permissions:['notes.write'],changelog:'New review requirement'})
  const wrapper=component();await flushPromises()
  await wrapper.findAll('button').find(button=>button.text()==='查询兼容更新')!.trigger('click');await flushPromises()
  expect(wrapper.text()).toContain('1.0.0 → 2.0.0');expect(wrapper.text()).toContain('notes.write')
  expect(wrapper.text()).toContain('New review requirement')
  expect(controls.invoke.mock.calls.map(call=>call[0])).toEqual(['extension_installed']);wrapper.unmount()
})

it('reads a sealed native rollback preview and keeps it separate from confirmation',async()=>{
  controls.invoke.mockResolvedValueOnce(page).mockResolvedValueOnce({fingerprint:'sealed-review',dependencies:{packages:[{package_key:'old',namespace:'examples',package_id:'test-package',version:'0.9.0',permissions:[]}]},changes:[{target:{package_key:'old',configuration:{}}}]})
  const wrapper=component();await flushPromises()
  await wrapper.findAll('button').find(button=>button.text()==='预览回滚')!.trigger('click');await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_rollback_preview',{installedOperationId:'old-update-operation',vaultId:'vault-one'})
  expect(wrapper.text()).toContain('0.9.0')
  expect(controls.invoke.mock.calls.some(call=>call[0]==='extension_install_rollback')).toBe(false);wrapper.unmount()
})

it('cancels late update details after vault or source configuration changes',async()=>{
  controls.invoke.mockResolvedValue(page)
  let resolve!:(value:unknown)=>void
  controls.update.mockImplementation(()=>new Promise(done=>{resolve=done}))
  const wrapper=component();await flushPromises()
  await wrapper.findAll('button').find(button=>button.text()==='查询兼容更新')!.trigger('click');await flushPromises()
  const signal=controls.update.mock.calls[0]![3] as AbortSignal
  await wrapper.setProps({sources:[{...source,enabled:false}]});await flushPromises()
  expect(signal.aborted).toBe(true)
  resolve({...vector.release,version:'9.0.0'});await flushPromises()
  expect(wrapper.text()).not.toContain('9.0.0')
  controls.workspace.vaultId='vault-two';await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_installed',{vaultId:'vault-two',offset:0,limit:20});wrapper.unmount()
})

it('ignores an unrelated malformed source when matching a valid installed origin',async()=>{
  controls.invoke.mockResolvedValue(page);controls.update.mockResolvedValue(null)
  const wrapper=component()
  await wrapper.setProps({sources:[{...source,id:'invalid',url:'invalid url'},source]});await flushPromises()
  await wrapper.findAll('button').find(button=>button.text()==='查询兼容更新')!.trigger('click');await flushPromises()
  expect(controls.update).toHaveBeenCalledWith(source,item.release,page,expect.any(AbortSignal))
  expect(wrapper.text()).toContain('没有与当前应用');wrapper.unmount()
})

it('rejects an inconsistent native page without exposing review actions',async()=>{
  controls.invoke.mockResolvedValue({...page,total:0})
  const wrapper=component();await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('安装状态响应无效')
  expect(wrapper.findAll('button').some(button=>button.text()==='查询兼容更新')).toBe(false)
  expect(controls.update).not.toHaveBeenCalled();wrapper.unmount()
})

it('returns to the last available page after installed packages are removed',async()=>{
  controls.invoke.mockResolvedValueOnce({...page,total:21}).mockResolvedValueOnce({...page,total:1,offset:20,items:[]}).mockResolvedValueOnce(page)
  const wrapper=component();await flushPromises()
  await wrapper.findAll('button').find(button=>button.text()==='下一页')!.trigger('click');await flushPromises()
  expect(controls.invoke.mock.calls.map(call=>call[1].offset)).toEqual([0,20,0])
  expect(wrapper.text()).toContain('第 1 / 1 页');wrapper.unmount()
})
