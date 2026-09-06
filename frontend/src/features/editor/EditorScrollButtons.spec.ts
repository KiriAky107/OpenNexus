// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import EditorScrollButtons from './EditorScrollButtons.vue'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); document.body.innerHTML = '' })

it('shows only useful directions, scrolls the editor, and hides empty or short documents', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
  const container = document.createElement('div')
  const viewport = document.createElement('textarea')
  viewport.className = 'source'
  container.append(viewport)
  document.body.append(container)
  Object.defineProperty(viewport, 'scrollHeight', { configurable: true, value: 1000 })
  Object.defineProperty(viewport, 'clientHeight', { configurable: true, value: 200 })
  const scroll = vi.fn()
  viewport.scrollTo = scroll
  const wrapper = mount(EditorScrollButtons, { props: { container, content: 'A long note' } })
  const update = async (top: number) => {
    viewport.scrollTop = top
    viewport.dispatchEvent(new Event('scroll'))
    await vi.advanceTimersByTimeAsync(40)
  }
  try {
    await update(0)
    expect(wrapper.findAll('button')).toHaveLength(1)
    expect(wrapper.find('button').attributes('aria-label')).toBe('滑动到底部')
    await wrapper.find('button').trigger('click')
    expect(scroll).toHaveBeenLastCalledWith({ top: 1000, behavior: 'smooth' })
    await update(400)
    expect(wrapper.findAll('button')).toHaveLength(2)
    await update(800)
    expect(wrapper.findAll('button')).toHaveLength(1)
    expect(wrapper.find('button').attributes('aria-label')).toBe('滑动到顶部')
    await wrapper.find('button').trigger('click')
    expect(scroll).toHaveBeenLastCalledWith({ top: 0, behavior: 'smooth' })
    await wrapper.setProps({ content: '  \n' })
    await update(400)
    expect(wrapper.findAll('button')).toHaveLength(0)
    Object.defineProperty(viewport, 'scrollHeight', { value: 200 })
    await wrapper.setProps({ content: 'Short note' })
    await update(0)
    expect(wrapper.findAll('button')).toHaveLength(0)
  } finally { wrapper.unmount() }
})
