// @vitest-environment jsdom
import { flushPromises,mount } from '@vue/test-utils'
import { beforeEach,expect,it,vi } from 'vitest'
const controls=vi.hoisted(()=>({invoke:vi.fn(),workspace:{vaultId:'vault-one'}}))
vi.mock('@/services/platform/desktop',()=>({hostInvoke:controls.invoke}))
vi.mock('@/stores/workspace',async()=>{const {reactive}=await import('vue');controls.workspace=reactive(controls.workspace);return {useWorkspaceStore:()=>controls.workspace}})
import PersonaCandidateDialog from './PersonaCandidateDialog.vue'
const slot='a'.repeat(64),review={fingerprint:'b'.repeat(64),slot,target:'workspace_persona',path:'opennexus-records/v1/persona/default.json',expected:'',target_version:0,before:null,after:{version:1,name:'Public persona',system_prompt:'<img src=x onerror=alert(1)>',dialogue_pairs:[{user:'Question',assistant:'Answer'}]},after_sha256:'c'.repeat(64),package_name:'Public package',package_version:'1.0.0'}
function component(){return mount(PersonaCandidateDialog,{props:{slot,sourceRevision:'source-one'},global:{stubs:{AppDialog:{template:'<section><slot /></section>'}}}})}
function button(wrapper:ReturnType<typeof component>,name:string){return wrapper.findAll('button').find(button=>button.text()===name)!}
async function previewed(){const wrapper=component();await wrapper.get('select').setValue('workspace_persona');await button(wrapper,'预览人设差异').trigger('click');await flushPromises();return wrapper}
beforeEach(()=>{controls.invoke.mockReset();controls.workspace.vaultId='vault-one'})

it('requires a target and explicit preview, displays escaped before/after data without writing',async()=>{
  controls.invoke.mockResolvedValue(review);const wrapper=component()
  expect(controls.invoke).not.toHaveBeenCalled();expect(button(wrapper,'预览人设差异').attributes('disabled')).toBeDefined()
  await wrapper.get('select').setValue('workspace_persona');await button(wrapper,'预览人设差异').trigger('click');await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_persona_preview',{request:{slot,vault_id:'vault-one'}})
  expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>');expect(wrapper.find('img').exists()).toBe(false)
  expect(controls.invoke.mock.calls.some(call=>call[0]==='extension_persona_apply')).toBe(false);wrapper.unmount()
})
it('sends only the native sealed review and target revision after separate confirmation',async()=>{
  controls.invoke.mockImplementation(async(command,args)=>command==='extension_persona_preview'?review:command==='extension_stage_prepare'?'request-id':{operation_id:args?.request?.application?.operation_id,state:'applied',hash:review.after_sha256,path:review.path})
  const wrapper=await previewed();await button(wrapper,'确认应用人设').trigger('click');await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_persona_apply',{request:{request_id:'request-id',application:{vault_id:'vault-one',slot,target:review.target,expected:review.expected,target_version:review.target_version,fingerprint:review.fingerprint,operation_id:expect.any(String)}}})
  expect(wrapper.emitted('applied')).toHaveLength(1);wrapper.unmount()
})
it('clears a stale target review and shows a useful revision conflict without reporting application',async()=>{
  controls.invoke.mockImplementation(async command=>{if(command==='extension_persona_preview')return review;if(command==='extension_stage_prepare')return 'request-id';if(command==='extension_persona_apply')throw new Error('REVISION_CONFLICT');return null})
  const wrapper=await previewed();await button(wrapper,'确认应用人设').trigger('click');await flushPromises()
  expect(wrapper.text()).toContain('当前人设已被修改');expect(wrapper.emitted('applied')).toBeUndefined()
  expect(wrapper.findAll('button').some(b=>b.text()==='确认应用人设')).toBe(false);wrapper.unmount()
})
it('recovers a lost reply only from the exact committed operation path and content hash',async()=>{
  let operationId:string|undefined
  controls.invoke.mockImplementation(async(command,args)=>{if(command==='extension_persona_preview')return review;if(command==='extension_stage_prepare')return 'request-id';if(command==='extension_persona_apply'){operationId=args.request.application.operation_id;throw new Error('REQUEST_CANCELLED')}if(command==='workspace_operation')return {operation_id:operationId,state:'committed',result:{path:review.path,hash:review.after_sha256}};return null})
  const wrapper=await previewed();await button(wrapper,'确认应用人设').trigger('click');await flushPromises()
  expect(wrapper.emitted('applied')).toHaveLength(1);expect(controls.invoke).toHaveBeenCalledWith('workspace_operation',{vaultId:'vault-one',operationId});wrapper.unmount()
})
it('does not use an unrelated operation receipt as successful application',async()=>{
  controls.invoke.mockImplementation(async command=>{if(command==='extension_persona_preview')return review;if(command==='extension_stage_prepare')return 'request-id';if(command==='extension_persona_apply')throw new Error('REQUEST_CANCELLED');return {operation_id:'unrelated',state:'committed',result:{path:review.path,hash:review.after_sha256}}})
  const wrapper=await previewed();await button(wrapper,'确认应用人设').trigger('click');await flushPromises()
  expect(wrapper.emitted('applied')).toBeUndefined();expect(wrapper.text()).toContain('已取消应用');wrapper.unmount()
})
it('cancels a late prepared request before dispatch after user cancellation',async()=>{
  let resolve!:(value:string)=>void
  controls.invoke.mockImplementation(async command=>command==='extension_persona_preview'?review:command==='extension_stage_prepare'?new Promise(done=>{resolve=done}):null)
  const wrapper=await previewed();await button(wrapper,'确认应用人设').trigger('click');await flushPromises()
  await button(wrapper,'取消并核对结果').trigger('click');resolve('late-request');await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_stage_cancel',{requestId:'late-request'})
  expect(controls.invoke.mock.calls.some(call=>call[0]==='extension_persona_apply')).toBe(false);expect(wrapper.text()).toContain('已取消应用');wrapper.unmount()
})
it('discards late preview or preparation after vault and source changes',async()=>{
  let resolve!:(value:unknown)=>void
  controls.invoke.mockImplementation(async()=>new Promise(done=>{resolve=done}))
  const wrapper=component();await wrapper.get('select').setValue('workspace_persona');await button(wrapper,'预览人设差异').trigger('click');await flushPromises()
  await wrapper.setProps({sourceRevision:'changed-key'});resolve(review);await flushPromises()
  expect(wrapper.findAll('button').some(b=>b.text()==='确认应用人设')).toBe(false)
  controls.workspace.vaultId='vault-two';await flushPromises();expect(wrapper.get('select').element.value).toBe('');wrapper.unmount()
})
it('refuses an inconsistent native persona preview before exposing confirmation',async()=>{
  controls.invoke.mockResolvedValue({...review,after:{...review.after,version:999}})
  const wrapper=await previewed();expect(wrapper.text()).toContain('人设预览响应无效')
  expect(wrapper.findAll('button').some(b=>b.text()==='确认应用人设')).toBe(false);wrapper.unmount()
})
