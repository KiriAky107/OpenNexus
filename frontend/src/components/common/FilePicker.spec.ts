// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import FilePicker from './FilePicker.vue'

describe('FilePicker', () => {
  it('keeps the native file input accessible and reports the selected file', async () => {
    const wrapper = mount(FilePicker, {
      props: { file: null, label: '选择文件', emptyLabel: '尚未选择文件', accept: '.json' },
    })
    const input = wrapper.get('input[type="file"]')
    const file = new File(['{}'], 'rules.json', { type: 'application/json' })
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })

    await input.trigger('change')

    expect(wrapper.emitted('select')).toEqual([[file]])
    expect(wrapper.get('label').attributes('for')).toBe(input.attributes('id'))
    expect(wrapper.text()).toContain('尚未选择文件')

    await wrapper.setProps({ file })
    expect(wrapper.text()).toContain('rules.json')
  })

  it('emits null when the native selection is cleared', async () => {
    const wrapper = mount(FilePicker, {
      props: { file: null, label: '选择文件', emptyLabel: '尚未选择文件' },
    })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [], configurable: true })

    await input.trigger('change')

    expect(wrapper.emitted('select')).toEqual([[null]])
  })
})
