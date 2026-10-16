// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ExportPanel from './ExportPanel.vue'
import type { ExportDelivery, ExportReceipt, MobileRequest } from './types'
const host=vi.hoisted(()=>({mobileRequest:vi.fn(),mobileSaveExport:vi.fn(),mobileExportDelivery:vi.fn()}))
vi.mock('./native',()=>host)
let wrapper: VueWrapper | undefined, prepared: ExportReceipt[], delivered: ExportDelivery
const receipt: ExportReceipt={ ticket:'original-sealed-ticket',filename:'OpenNexus-content-original.zip',sha256:'a'.repeat(64),size:1234,file_count:3,content_bytes:4321 }
beforeEach(()=>{
  vi.clearAllMocks();prepared=[];delivered={ticket:receipt.ticket,state:'not_started'}
  host.mobileRequest.mockImplementation(async (request:MobileRequest)=>{
    if (request.kind==='list_exports') return {items:structuredClone(prepared),total:prepared.length}
    if (request.kind==='prepare_export') { prepared.push(receipt);return structuredClone(receipt) }
    if (request.kind==='export_status') return structuredClone(receipt)
    throw Error(`Unexpected ${request.kind}`)
  })
  host.mobileExportDelivery.mockImplementation(async ()=>structuredClone(delivered))
  host.mobileSaveExport.mockImplementation(async ()=>{delivered={ticket:receipt.ticket,delivery_id:'original-output-attempt',state:'saved',sha256:receipt.sha256,size:receipt.size,verified_at:1792130000000};return structuredClone(delivered)})
})
afterEach(()=>{wrapper?.unmount();wrapper=undefined})
async function start() {wrapper=mount(ExportPanel,{props:{language:'en'},attachTo:document.body});await flushPromises()}
function button(label:string) { const found=wrapper!.findAll('button').find(button=>button.text()===label);expect(found,label).toBeDefined();return found! }
async function freeze() {await button('Prepare content snapshot').trigger('click');await flushPromises()}

it('freezes content once and chooses a destination only after reading the original status',async()=>{
  await start();await freeze()
  expect(host.mobileSaveExport).not.toHaveBeenCalled();expect(wrapper!.text()).toContain(receipt.sha256)
  const nativeCalls=host.mobileRequest.mock.calls.length
  await wrapper!.setProps({language:'zh-CN'});await flushPromises()
  expect(host.mobileRequest).toHaveBeenCalledTimes(nativeCalls);expect(host.mobileExportDelivery).not.toHaveBeenCalled()
  await button('选择保存位置').trigger('click');await flushPromises()
  expect(host.mobileExportDelivery).toHaveBeenCalledWith(receipt.ticket,undefined)
  expect(host.mobileExportDelivery.mock.invocationCallOrder[0]).toBeLessThan(host.mobileSaveExport.mock.invocationCallOrder[0]!)
  expect(host.mobileSaveExport).toHaveBeenCalledExactlyOnceWith(receipt.ticket)
  expect(wrapper!.text()).toContain('已保存，文件原字节核对通过')
  expect(host.mobileRequest.mock.calls.filter(([r])=>r.kind==='prepare_export')).toHaveLength(1)
})

it('recovers a lost output reply by reading its original delivery without a second picker or write',async()=>{
  host.mobileSaveExport.mockImplementation(async()=>{
    delivered={ticket:receipt.ticket,delivery_id:'original-output-attempt',state:'saved',sha256:receipt.sha256,size:receipt.size}
    throw Error('MOBILE_EXPORT_RESULT_UNKNOWN')
  })
  await start();await freeze();await button('Choose save location').trigger('click');await flushPromises()
  expect(wrapper!.text()).not.toContain('Saved file bytes verified')
  await button('Check original result').trigger('click');await flushPromises()
  expect(wrapper!.text()).toContain('Saved file bytes verified');expect(wrapper!.text()).toContain('original-output-attempt')
  expect(host.mobileSaveExport).toHaveBeenCalledTimes(1)
  expect(host.mobileRequest.mock.calls.filter(([r])=>r.kind==='prepare_export')).toHaveLength(1)
})

it('finds a sealed snapshot after losing preparation acknowledgement without preparing another ZIP',async()=>{
  host.mobileRequest.mockImplementation(async(request:MobileRequest)=>{
    if(request.kind==='list_exports')return {items:structuredClone(prepared),total:prepared.length}
    if(request.kind==='prepare_export'){prepared.push(receipt);throw Error('MOBILE_HOST_INTERRUPTED')}
    if(request.kind==='export_status')return receipt
    throw Error('unexpected')
  })
  await start();await freeze()
  expect(wrapper!.text()).toContain(receipt.filename);expect(host.mobileSaveExport).not.toHaveBeenCalled()
  await wrapper!.get('.note-row').trigger('click');await flushPromises()
  expect(host.mobileExportDelivery).toHaveBeenCalledWith(receipt.ticket)
  expect(wrapper!.text()).toContain('No destination selected yet')
  expect(host.mobileRequest.mock.calls.filter(([r])=>r.kind==='prepare_export')).toHaveLength(1)
})

it('does not claim success when the picker was cancelled and preserves the original frozen snapshot',async()=>{
  host.mobileSaveExport.mockResolvedValue({ticket:receipt.ticket,delivery_id:'cancelled-output',state:'cancelled'})
  await start();await freeze();await button('Choose save location').trigger('click');await flushPromises()
  expect(wrapper!.text()).toContain('Cancelled');expect(wrapper!.text()).not.toContain('Saved file bytes verified');expect(wrapper!.text()).toContain(receipt.sha256)
})
