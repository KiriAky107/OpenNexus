// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import CanvasProperties from './CanvasProperties.vue'

const node = { id: 'original-node', type: 'file' as const, x: 1, y: 2, width: 120, height: 90, file: '原文/Source.md', subpath: '#Heading' }
const edge = { id: 'original-edge', fromNode: node.id, toNode: 'target', fromSide: 'left' as const, toSide: 'bottom' as const, fromEnd: 'none' as const, toEnd: 'arrow' as const }
const props = { node, edge, nodeValues: { value: node.file, ...node }, edgeValues: { ...edge }, selectionCount: 1, readOnly: false, immediate: true, touch: true, language: 'en' }

it('names shared property controls and translates direction labels without changing Canvas values', () => {
  const wrapper = mount(CanvasProperties, { props })
  expect(wrapper.get('select[aria-label="From side"]').element).toHaveProperty('value', 'left')
  expect(wrapper.get('select[aria-label="To side"]').element).toHaveProperty('value', 'bottom')
  expect(wrapper.get('select[aria-label="From arrow"]').element).toHaveProperty('value', 'none')
  expect(wrapper.get('select[aria-label="To arrow"]').element).toHaveProperty('value', 'arrow')
  expect(wrapper.get('select[aria-label="From side"] option[value="bottom"]').text()).toBe('Bottom')
  expect(wrapper.get('input[aria-label="Heading or block subpath"]').element).toHaveProperty('value', '#Heading')
  wrapper.unmount()
})

it('switches all shared property labels to Chinese while preserving original paths, values and edit events', async () => {
  const wrapper = mount(CanvasProperties, { props })
  await wrapper.setProps({ language: 'zh-CN', readOnly: true })
  expect(wrapper.get('select[aria-label="起点侧边"] option[value="left"]').text()).toBe('左侧')
  expect(wrapper.get('select[aria-label="终点侧边"] option[value="bottom"]').text()).toBe('下侧')
  expect(wrapper.get('select[aria-label="起点箭头"]').element).toHaveProperty('value', 'none')
  expect(wrapper.get('select[aria-label="终点箭头"]').element).toHaveProperty('value', 'arrow')
  expect(wrapper.get('input[aria-label="宽度"]').element).toHaveProperty('value', '120')
  expect(wrapper.get('input[aria-label="标题或块子路径"]').element).toHaveProperty('value', '#Heading')
  expect(wrapper.get('textarea[aria-label="节点内容"]').element).toHaveProperty('value', node.file)
  for (const control of wrapper.findAll('select')) expect(control.element.disabled).toBe(true)
  expect(wrapper.emitted()).toEqual({})
  expect(props.nodeValues.value).toBe('原文/Source.md'); expect(props.edgeValues.fromSide).toBe('left')
  wrapper.unmount()
})
