// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import vector from '@/services/fixtures/community-python-vector.json'
const controls=vi.hoisted(()=>({invoke:vi.fn(),update:vi.fn(),stage:vi.fn(),workspace:{vaultId:'vault-one'}}))
vi.mock('@/services/platform/desktop',()=>({hostInvoke:controls.invoke}))
vi.mock('@/services/communityService',()=>({findCompatibleUpdate:controls.update,stageDesktopRelease:controls.stage,loadSources:()=>[]}))
vi.mock('@/stores/workspace',async()=>{const {reactive}=await import('vue');controls.workspace=reactive(controls.workspace);return {useWorkspaceStore:()=>controls.workspace}})
import InstalledPackages from './InstalledPackages.vue'
const source={id:'source',url:'https://catalog.example/',enabled:true,source_id:'fixture',keys:[vector.key]}
const item={slot:'slot',package_key:'package',source:source.url,release:vector.release,revision:'revision',pending_operation:null,configuration:{},rollback_operation_id:'old-update-operation'}
const page={items:[item],total:1,offset:0,limit:20,app_version:'0.6.0',platform:'windows',architecture:'x86_64'}
function component(){return mount(InstalledPackages,{props:{refreshKey:0,sources:[source]},global:{stubs:{AppDialog:{template:'<section><slot /></section>'}}}})}
beforeEach(()=>{controls.invoke.mockReset();controls.update.mockReset();controls.stage.mockReset();controls.workspace.vaultId='vault-one'})

it('opens the persona application target from installed packages without implicitly previewing or writing',async()=>{
  controls.invoke.mockResolvedValue({...page,items:[{...item,release:{...item.release,type:'persona'}}]})
  const wrapper=component();await flushPromises()
  await wrapper.findAll('button').find(button=>button.text()==='应用人设')!.trigger('click');await flushPromises()
  expect(wrapper.find('select').exists()).toBe(true)
  expect(wrapper.text()).toContain('当前知识库人设')
  expect(controls.invoke.mock.calls.map(call=>call[0])).toEqual(['extension_installed']);wrapper.unmount()
})

it('opens template targets without implicitly importing or running anything',async()=>{
  controls.invoke.mockResolvedValue({...page,items:[{...item,release:{...item.release,type:'template'}}]})
  const wrapper=component();await flushPromises()
  await wrapper.findAll('button').find(button=>button.text()==='导入模板')!.trigger('click');await flushPromises()
  expect(wrapper.text()).toContain('逐个确认导入')
  expect(controls.invoke.mock.calls.map(call=>call[0])).toEqual(['extension_installed']);wrapper.unmount()
})

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

const preview={fingerprint:'native-reviewed-plan',dependencies:{packages:[{package_key:'new-package',namespace:'examples',package_id:vector.release.package_id,kind:'persona',version:'2.0.0',permissions:['notes.write']}]},changes:[{target:{package_key:'new-package',configuration:{language:'zh'}}}]}
function button(wrapper:ReturnType<typeof component>,text:string){return wrapper.findAll('button').find(button=>button.text()===text)!}
async function reviewedUpdate(){
  controls.update.mockResolvedValue({...vector.release,version:'2.0.0',permissions:['notes.write']})
  controls.stage.mockResolvedValue({package_key:'new-package'})
  const wrapper=component();await flushPromises()
  await button(wrapper,'查询兼容更新').trigger('click');await flushPromises()
  await wrapper.get('textarea').setValue('{"language":"zh"}')
  await button(wrapper,'校验包并预览依赖').trigger('click');await flushPromises()
  return wrapper
}

it('stages and shows the full dependency plan before a separate update confirmation',async()=>{
  controls.invoke.mockImplementation(async command=>command==='extension_install_preview'?preview:page)
  const wrapper=await reviewedUpdate()
  expect(controls.stage).toHaveBeenCalledWith(source,expect.objectContaining({version:'2.0.0'}),expect.any(AbortSignal))
  expect(controls.invoke).toHaveBeenCalledWith('extension_install_preview',{request:{root_key:'new-package',vault_id:'vault-one',configurations:{'new-package':{language:'zh'}}}})
  expect(wrapper.text()).toContain('完整安装计划')
  expect(controls.invoke.mock.calls.some(call=>call[0]==='extension_install_confirm')).toBe(false)
  await wrapper.get('textarea').setValue('{"language":"en"}')
  expect(wrapper.findAll('button').some(button=>button.text()==='确认更新')).toBe(false);wrapper.unmount()
})

it('confirms the sealed update then refreshes actual installation state without a renderer enable loop',async()=>{
  controls.invoke.mockImplementation(async(command,args)=>{
    if(command==='extension_install_preview')return preview
    if(command==='extension_stage_prepare')return 'request-id'
    if(command==='extension_install_confirm')return {operation_id:args.request.operation_id,state:'complete'}
    return page
  })
  const wrapper=await reviewedUpdate()
  await button(wrapper,'确认更新').trigger('click');await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_install_confirm',{request:{request_id:'request-id',operation_id:expect.any(String),vault_id:'vault-one',fingerprint:preview.fingerprint,root_key:'new-package',configurations:{'new-package':{language:'zh'}}}})
  expect(controls.invoke.mock.calls.some(call=>call[0]==='extension_enable')).toBe(false)
  expect(wrapper.text()).toContain('安装已完成');expect(wrapper.find('textarea').exists()).toBe(false);wrapper.unmount()
})

it('applies only the fingerprint and operation from a reviewed rollback',async()=>{
  controls.invoke.mockImplementation(async(command,args)=>{
    if(command==='extension_rollback_preview')return preview
    if(command==='extension_stage_prepare')return 'rollback-request'
    if(command==='extension_install_rollback')return {operation_id:args.request.operation_id,state:'complete'}
    return page
  })
  const wrapper=component();await flushPromises();await button(wrapper,'预览回滚').trigger('click');await flushPromises()
  await button(wrapper,'确认回滚').trigger('click');await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_install_rollback',{request:{request_id:'rollback-request',operation_id:expect.any(String),vault_id:'vault-one',fingerprint:preview.fingerprint,installed_operation_id:item.rollback_operation_id}})
  expect(controls.stage).not.toHaveBeenCalled();wrapper.unmount()
})

it('does not claim completion for a pending native receipt and refreshes its health state',async()=>{
  let failed=false
  controls.invoke.mockImplementation(async(command,args)=>{
    if(command==='extension_install_preview')return preview
    if(command==='extension_stage_prepare')return 'request-id'
    if(command==='extension_install_confirm'){failed=true;return {operation_id:args.request.operation_id,state:'checking'}}
    return failed?{...page,items:[{...item,pending_operation:'checking-operation'}]}:page
  })
  const wrapper=await reviewedUpdate();await button(wrapper,'确认更新').trigger('click');await flushPromises()
  expect(wrapper.text()).toContain('安装仍未完成');expect(wrapper.text()).toContain('安装等待健康检查')
  expect(wrapper.text()).not.toContain('安装已完成，全部');wrapper.unmount()
})

it('cancels the owned native request while waiting and keeps the result under review',async()=>{
  let reject!:(reason:Error)=>void
  controls.invoke.mockImplementation(async command=>{
    if(command==='extension_install_preview')return preview
    if(command==='extension_stage_prepare')return 'request-id'
    if(command==='extension_install_confirm')return new Promise((_,fail)=>{reject=fail})
    return page
  })
  const wrapper=await reviewedUpdate();await button(wrapper,'确认更新').trigger('click');await flushPromises()
  await button(wrapper,'取消并核对结果').trigger('click');await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_stage_cancel',{requestId:'request-id'})
  expect(wrapper.find('textarea').exists()).toBe(true)
  reject(new Error('REQUEST_CANCELLED'));await flushPromises()
  expect(wrapper.text()).toContain('REQUEST_CANCELLED');expect(wrapper.text()).not.toContain('安装已完成，全部');wrapper.unmount()
})

it('does not dispatch an update after the vault changes during request preparation',async()=>{
  let resolve!:(value:string)=>void
  controls.invoke.mockImplementation(async command=>{
    if(command==='extension_install_preview')return preview
    if(command==='extension_stage_prepare')return new Promise(done=>{resolve=done})
    return page
  })
  const wrapper=await reviewedUpdate();await button(wrapper,'确认更新').trigger('click');await flushPromises()
  controls.workspace.vaultId='vault-two';await flushPromises();resolve('late-request');await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_stage_cancel',{requestId:'late-request'})
  expect(controls.invoke.mock.calls.some(call=>call[0]==='extension_install_confirm')).toBe(false);wrapper.unmount()
})

it('locks configuration during preview and discards a late plan after source keys change',async()=>{
  let resolve!:(value:unknown)=>void
  controls.update.mockResolvedValue({...vector.release,version:'2.0.0'});controls.stage.mockResolvedValue({package_key:'new-package'})
  controls.invoke.mockImplementation(async command=>command==='extension_install_preview'?new Promise(done=>{resolve=done}):page)
  const wrapper=component();await flushPromises();await button(wrapper,'查询兼容更新').trigger('click');await flushPromises()
  await button(wrapper,'校验包并预览依赖').trigger('click');await flushPromises()
  expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
  await wrapper.setProps({sources:[{...source,keys:[]}]});await flushPromises()
  resolve(preview);await flushPromises()
  expect(wrapper.findAll('button').some(button=>button.text()==='确认更新')).toBe(false)
  expect(controls.invoke.mock.calls.some(call=>call[0]==='extension_install_confirm')).toBe(false)
  expect(controls.invoke.mock.calls.filter(call=>call[0]==='extension_installed')).toHaveLength(2);wrapper.unmount()
})
