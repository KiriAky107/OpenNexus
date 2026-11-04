// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import HandwritingPanel from './HandwritingPanel.vue'
import { emptyHandwriting } from './handwriting'
const button=(wrapper:ReturnType<typeof mount>,text:string)=>wrapper.findAll('button').find(b=>b.text()===text)!
it('keeps model output as a preview until the user chooses a result',async()=>{
  const analyze=vi.fn().mockResolvedValue('# Organized notes')
  const wrapper=mount(HandwritingPanel,{props:{ink:emptyHandwriting('text'),text:'原文',language:'en',analyze}})
  await button(wrapper,'Generate preview').trigger('click');await flushPromises()
  expect(analyze).toHaveBeenCalledTimes(1);expect(wrapper.emitted('apply')).toBeUndefined()
  expect(wrapper.findAll('textarea').some(t=>t.element.value==='# Organized notes')).toBe(true)
  await button(wrapper,'Add handwriting node').trigger('click')
  const proposal=wrapper.emitted('apply')![0]![0] as any
  expect(proposal.original.text).toBe('原文');expect(proposal.output.text).toBe('# Organized notes');expect(proposal.output.ink.mode).toBe('text')
  wrapper.unmount()
})
it('cancels late results and never applies them after a close/unmount',async()=>{
  let finish:(value:string)=>void=()=>{}
  const analyze=vi.fn((_request,signal:AbortSignal)=>new Promise<string>(resolve=>{finish=resolve;signal.addEventListener('abort',()=>{})}))
  const wrapper=mount(HandwritingPanel,{props:{ink:emptyHandwriting('text'),text:'original',language:'en',analyze}})
  await button(wrapper,'Generate preview').trigger('click');await flushPromises()
  await button(wrapper,'Cancel request').trigger('click');finish('late result');await flushPromises()
  expect(wrapper.text()).not.toContain('late result');expect(wrapper.emitted('apply')).toBeUndefined()
  wrapper.unmount()
})
it('disables saving into a read-only document',async()=>{
  const wrapper=mount(HandwritingPanel,{props:{ink:emptyHandwriting(),language:'en',readOnly:true}})
  expect(button(wrapper,'Save handwriting node').attributes('disabled')).toBeDefined()
  expect(wrapper.emitted('apply')).toBeUndefined();wrapper.unmount()
})
it('fits a long imported note and rejects oversized conversion without emitting a partial result',async()=>{
  const text='长'.repeat(1200)
  const wrapper=mount(HandwritingPanel,{props:{ink:emptyHandwriting('text'),text,language:'en'}})
  await button(wrapper,'Save handwriting node').trigger('click')
  const saved=wrapper.emitted('apply')![0]![0] as any
  expect(saved.original.text).toBe(text);expect(saved.original.ink.height).toBeGreaterThan(640)
  await wrapper.find('textarea').setValue('长'.repeat(10000))
  await button(wrapper,'Text to handwriting').trigger('click')
  expect(wrapper.emitted('apply')).toHaveLength(1)
  expect(wrapper.text()).toContain('source was not truncated')
  wrapper.unmount()
})
it('maps actual pointer coordinates into the page and does not save cancelled ink',async()=>{
  const wrapper=mount(HandwritingPanel,{props:{language:'en'}})
  const surface=wrapper.get('.ink-surface').element as HTMLElement
  vi.spyOn(surface,'getBoundingClientRect').mockReturnValue({left:10,top:20,width:480,height:320,right:490,bottom:340,x:10,y:20,toJSON:()=>({})})
  const pointer=(type:string,id:number,x:number,y:number)=>{
    const event=new Event(type,{bubbles:true,cancelable:true})
    Object.assign(event,{button:0,pointerId:id,pointerType:'pen',pressure:.6,clientX:x,clientY:y,isPrimary:true})
    surface.dispatchEvent(event)
  }
  pointer('pointerdown',1,20,30);pointer('pointermove',1,40,50);pointer('pointerup',1,40,50)
  pointer('pointerdown',2,100,100);pointer('pointercancel',2,120,120);pointer('pointerup',2,120,120)
  await button(wrapper,'Save handwriting node').trigger('click')
  const proposal=wrapper.emitted('apply')![0]![0] as any
  expect(proposal.original.ink.strokes).toHaveLength(1)
  expect(proposal.original.ink.strokes[0].points[0]).toEqual({x:20,y:20,pressure:.6})
  wrapper.unmount()
})
