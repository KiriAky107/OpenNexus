// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import ToolOption from './ToolOption.vue'

it('shows Chinese summaries, preserves raw metadata and emits the original tool ID', async () => {
  const name = 'mcp.9ca7ee21603a.web_search'
  const description = 'Search the web. query: string. ' + 'Full provider instructions. '.repeat(40)
  const wrapper = mount(ToolOption, { props: { name, description, selected: false } })
  expect(wrapper.get('strong').text()).toBe('网页搜索')
  expect(wrapper.get('code').text()).toBe(name)
  expect(wrapper.get('.tool-summary').text()).toContain('搜索关键词')
  expect(wrapper.get('details').attributes('open')).toBeUndefined()
  expect(wrapper.get('details p').element.textContent).toBe(description)
  await wrapper.get('summary').trigger('click')
  expect(wrapper.emitted('toggle')).toBeUndefined()
  await wrapper.get('input').setValue(true)
  expect(wrapper.emitted('toggle')).toEqual([[name]])
})
