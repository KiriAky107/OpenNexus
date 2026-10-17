// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import SyncInitialPreview from './SyncInitialPreview.vue'

vi.mock('../../i18n', () => ({ t: (zh: string) => zh }))
describe('shared initial merge preview', () => {
  it('preserves original paths as text and pages without changing a confirmation', async () => {
    const original = '<img src=x onerror=alert(1)>/中文.md'
    const wrapper = mount(SyncInitialPreview, { props: { items: [{ path: original, action: 'conflict' }], total: 101, offset: 0, limit: 100, busy: false } })
    expect(wrapper.text()).toContain(original)
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('保留为待解决冲突')
    const buttons = wrapper.findAll('button')
    expect(buttons[0]!.attributes('disabled')).toBeDefined()
    await buttons[1]!.trigger('click')
    expect(wrapper.emitted('page')).toEqual([[100]])
    expect(wrapper.emitted('confirm')).toBeUndefined()
    await buttons[2]!.trigger('click')
    expect(wrapper.emitted('confirm')).toEqual([[]])
  })
  it('holds paging and confirmation while the original request is running', () => {
    const wrapper = mount(SyncInitialPreview, { props: { items: [], total: 300, offset: 100, limit: 100, busy: true } })
    expect(wrapper.findAll('button').every(button => button.attributes('disabled') !== undefined)).toBe(true)
  })
})
