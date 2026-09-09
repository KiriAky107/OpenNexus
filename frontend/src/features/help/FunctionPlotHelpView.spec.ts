// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import FunctionPlotHelpView from './FunctionPlotHelpView.vue'

const render = vi.hoisted(() => vi.fn())
vi.mock('@/services/functionPlotService', () => ({ renderFunctionPlot: render }))

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  render.mockReset().mockResolvedValue({ svg: '<svg class="function-plot-svg"></svg>', warnings: [], nodeCount: 8 })
})

describe('Function Plot tutorial', () => {
  it('documents the fenced syntax and renders the editable example', async () => {
    const wrapper = mount(FunctionPlotHelpView)
    expect(wrapper.text()).toContain('Function Plot 教程')
    expect(wrapper.text()).toContain('domain: -10, 10')
    expect(wrapper.text()).toContain('sin(x)')
    expect(wrapper.text()).toContain('最多 16 条表达式')
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    expect(render).toHaveBeenCalledWith(expect.stringContaining('y = sin(x)'), 'light')
    expect(wrapper.get('.svg-host').html()).toContain('function-plot-svg')
    wrapper.unmount()
    vi.useRealTimers()
  })
})
