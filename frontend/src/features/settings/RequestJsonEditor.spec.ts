// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import RequestJsonEditor from './RequestJsonEditor.vue'

it('validates object JSON and prevents host-owned fields from being saved', async () => {
  const wrapper = mount(RequestJsonEditor, {props: {modelValue: []}})
  await wrapper.get('button').trigger('click')
  await wrapper.get('textarea').setValue('{"stream":false}')
  expect(wrapper.emitted('valid')?.at(-1)).toEqual([false])
  expect(wrapper.text()).toContain('运行请求管理字段不可覆盖')
  await wrapper.get('textarea').setValue('{"stream_options":{"include_usage":true}}')
  expect(wrapper.emitted('valid')?.at(-1)).toEqual([true])
  expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toEqual([
    {capability:'chat', model:null, stream:null, body:{stream_options:{include_usage:true}}},
  ])
  await wrapper.get('textarea').setValue('[]')
  expect(wrapper.emitted('valid')?.at(-1)).toEqual([false])
  wrapper.unmount()
})

it('restores defaults even from an invalid draft and reflects replacement configurations', async () => {
  const wrapper = mount(RequestJsonEditor, {props:{modelValue:[{capability:'chat', body:{enable_thinking:false}}]}})
  await wrapper.get('textarea').setValue('{invalid')
  expect(wrapper.emitted('valid')?.at(-1)).toEqual([false])
  await wrapper.findAll('button').find(button => button.text() === '恢复默认请求')!.trigger('click')
  expect(wrapper.findAll('textarea')).toHaveLength(0)
  expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([[]])
  await wrapper.setProps({modelValue:[{capability:'embedding', body:{dimensions:384}}]})
  expect(wrapper.get('textarea').element.value).toContain('384')
  expect(wrapper.emitted('valid')?.at(-1)).toEqual([true])
  wrapper.unmount()
})
