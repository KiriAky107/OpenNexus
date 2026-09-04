// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import RequestJsonEditor from './RequestJsonEditor.vue'
import { apiClient } from '@/services/apiClient'

vi.mock('@/services/apiClient', () => ({apiClient:{post:vi.fn()}}))

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

it('ignores an imported configuration that finishes after a newer edit', async () => {
  let finish!: (value: {request_overrides: unknown[]}) => void
  vi.mocked(apiClient.post).mockReturnValue(new Promise(resolve => { finish = resolve }))
  const wrapper = mount(RequestJsonEditor, {props:{modelValue:[]}})
  const input = wrapper.get('input[type="file"]')
  const file = new File(['{"version":1,"request_overrides":[]}'], 'rules.json', {type:'application/json'})
  Object.defineProperty(input.element, 'files', {value:[file], configurable:true})
  await input.trigger('change')
  await wrapper.findAll('button').find(button => button.text() === '添加请求规则')!.trigger('click')
  finish({request_overrides:[{capability:'embedding',body:{dimensions:384}}]})
  await Promise.resolve(); await Promise.resolve()
  expect(wrapper.findAll('textarea')).toHaveLength(1)
  expect(wrapper.get('textarea').element.value).toBe('{}')
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
