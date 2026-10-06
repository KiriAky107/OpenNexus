// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
const controls=vi.hoisted(()=>({invoke:vi.fn(),workspace:{vaultId:'vault-one'}}))
vi.mock('@/services/platform/desktop',()=>({hostInvoke:controls.invoke}))
vi.mock('@/stores/workspace',async()=>{const {reactive}=await import('vue');controls.workspace=reactive(controls.workspace);return {useWorkspaceStore:()=>controls.workspace}})
import Dialog from './TemplateCandidateDialog.vue'

const slot='a'.repeat(64), file={key:'main.py',target:{file_id:'11111111-1111-4111-8111-111111111111',path:'experiments/course/main.py',hash:'',revision:0},fingerprint:'b'.repeat(64),before:null,after:'<img src=x onerror=alert(1)>\r\n',after_sha256:'c'.repeat(64)}
const input={key:'data.csv',target:{file_id:'22222222-2222-4222-8222-222222222222',path:'experiments/course/data.csv',hash:'',revision:0},fingerprint:'d'.repeat(64),before:null,after:'name,value\r\n中,2\r\n',after_sha256:'e'.repeat(64)}
const review={slot,package_name:'Course template',package_version:'1.0.0',entry:file.target.path,inputs:[input.target.path],files:[file,input]}
function component(){return mount(Dialog,{props:{slot,sourceRevision:'source-one'},global:{stubs:{AppDialog:{template:'<section><slot /></section>'}}}})}
function button(wrapper:ReturnType<typeof component>,name:string){return wrapper.findAll('button').find(button=>button.text()===name)!}
async function previewed(){const wrapper=component();await button(wrapper,'预览文件差异').trigger('click');await flushPromises();return wrapper}
beforeEach(()=>{controls.invoke.mockReset();controls.workspace.vaultId='vault-one'})

it('requires explicit review and file selection and displays template code as escaped text',async()=>{
  controls.invoke.mockResolvedValue(review)
  const wrapper=component();expect(controls.invoke).not.toHaveBeenCalled()
  await button(wrapper,'预览文件差异').trigger('click');await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_template_preview',{request:{vault_id:'vault-one',slot,directory:'experiments/community',note_path:null}})
  expect(wrapper.find('img').exists()).toBe(false);expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
  expect(wrapper.findAll('button').filter(b=>b.text()==='确认导入此文件').every(b=>b.attributes('disabled')!==undefined)).toBe(true)
  expect(controls.invoke.mock.calls.some(call=>call[0]==='extension_template_apply')).toBe(false);wrapper.unmount()
})
it('imports only the selected file after confirmation and preserves a separate not-yet-imported input',async()=>{
  controls.invoke.mockImplementation(async(command,args)=>command==='extension_template_preview'?review:command==='extension_stage_prepare'?'request-id':{operation_id:args?.request?.application?.operation_id,state:'applied',key:file.key,entry:{...file.target,hash:file.after_sha256,revision:1}})
  const wrapper=await previewed();await wrapper.findAll('input[type="checkbox"]')[0].setValue(true)
  await button(wrapper,'确认导入此文件').trigger('click');await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_template_apply',{request:{request_id:'request-id',application:{vault_id:'vault-one',slot,key:file.key,target:file.target,fingerprint:file.fingerprint,operation_id:expect.any(String)}}})
  expect(controls.invoke.mock.calls.filter(call=>call[0]==='extension_template_apply')).toHaveLength(1)
  expect(wrapper.emitted('updated')).toHaveLength(1);expect(wrapper.text()).toContain('已导入 1 个文件')
  expect(wrapper.findAll('button').filter(b=>b.text()==='确认导入此文件')).toHaveLength(1)
  expect(controls.invoke.mock.calls.some(call=>String(call[0]).includes('experiment_request'))).toBe(false);wrapper.unmount()
})
it('recovers a lost reply only from the exact committed operation file identity and hash',async()=>{
  let id:string|undefined
  controls.invoke.mockImplementation(async(command,args)=>{if(command==='extension_template_preview')return review;if(command==='extension_stage_prepare')return 'request-id';if(command==='extension_template_apply'){id=args.request.application.operation_id;throw new Error('REQUEST_CANCELLED')}if(command==='workspace_operation')return {operation_id:id,state:'committed',result:{...file.target,hash:file.after_sha256}};return null})
  const wrapper=await previewed();await wrapper.findAll('input[type="checkbox"]')[0].setValue(true);await button(wrapper,'确认导入此文件').trigger('click');await flushPromises()
  expect(wrapper.emitted('updated')).toHaveLength(1);expect(wrapper.text()).toContain('已导入 1 个文件');wrapper.unmount()
})
it('refuses a committed receipt with a different file identity and clears the stale review',async()=>{
  let id:string|undefined
  controls.invoke.mockImplementation(async(command,args)=>{if(command==='extension_template_preview')return review;if(command==='extension_stage_prepare')return 'request-id';if(command==='extension_template_apply'){id=args.request.application.operation_id;throw new Error('REVISION_CONFLICT')}if(command==='workspace_operation')return {operation_id:id,state:'committed',result:{...file.target,file_id:input.target.file_id,hash:file.after_sha256}};return null})
  const wrapper=await previewed();await wrapper.findAll('input[type="checkbox"]')[0].setValue(true);await button(wrapper,'确认导入此文件').trigger('click');await flushPromises()
  expect(wrapper.emitted('updated')).toBeUndefined();expect(wrapper.text()).toContain('目标文件已改变');expect(wrapper.find('article').exists()).toBe(false);wrapper.unmount()
})
it('cancels a late prepared lease before dispatch and never starts remaining files',async()=>{
  let resolve!:(value:string)=>void
  controls.invoke.mockImplementation(async command=>command==='extension_template_preview'?review:command==='extension_stage_prepare'?new Promise(done=>{resolve=done}):null)
  const wrapper=await previewed();await wrapper.findAll('input[type="checkbox"]')[0].setValue(true);await button(wrapper,'确认导入此文件').trigger('click');await flushPromises()
  await button(wrapper,'停止并核对结果').trigger('click');resolve('late-request');await flushPromises()
  expect(controls.invoke).toHaveBeenCalledWith('extension_stage_cancel',{requestId:'late-request'});expect(controls.invoke.mock.calls.some(call=>call[0]==='extension_template_apply')).toBe(false);wrapper.unmount()
})
it('keeps the first actual receipt visible when importing a later file fails',async()=>{
  let count=0
  controls.invoke.mockImplementation(async(command,args)=>{if(command==='extension_template_preview')return review;if(command==='extension_stage_prepare')return 'request-id';if(command==='extension_template_apply'){if(++count===2)throw new Error('REVISION_CONFLICT');return {operation_id:args.request.application.operation_id,state:'applied',key:file.key,entry:{...file.target,hash:file.after_sha256}}}return null})
  const wrapper=await previewed();await wrapper.findAll('input[type="checkbox"]')[0].setValue(true);await button(wrapper,'确认导入此文件').trigger('click');await flushPromises()
  await wrapper.findAll('input[type="checkbox"]')[1].setValue(true);await button(wrapper,'确认导入此文件').trigger('click');await flushPromises()
  expect(wrapper.text()).toContain('已导入 1 个文件');expect(wrapper.text()).toContain(file.target.path);expect(wrapper.text()).toContain('目标文件已改变');expect(wrapper.emitted('updated')).toHaveLength(1);wrapper.unmount()
})
it('discards late preview on source change and clears old receipts on vault change',async()=>{
  let resolve!:(value:unknown)=>void
  controls.invoke.mockImplementation(async()=>new Promise(done=>{resolve=done}))
  const wrapper=component();await button(wrapper,'预览文件差异').trigger('click');await flushPromises();await wrapper.setProps({sourceRevision:'changed-key'});resolve(review);await flushPromises()
  expect(wrapper.find('article').exists()).toBe(false);controls.workspace.vaultId='vault-two';await flushPromises();expect(wrapper.text()).toContain('知识库已切换');wrapper.unmount()
})
it('refuses duplicate target identities and an entry absent from the reviewed files',async()=>{
  for(const value of [{...review,files:[file,{...input,target:{...input.target,file_id:file.target.file_id}}]},{...review,entry:'experiments/missing.py'}]){
    controls.invoke.mockResolvedValue(value);const wrapper=await previewed();expect(wrapper.find('article').exists()).toBe(false);expect(wrapper.text()).toContain('模板文件格式');wrapper.unmount()
  }
})
