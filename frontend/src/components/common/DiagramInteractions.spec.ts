// @vitest-environment jsdom
import { expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import DiagramInteractions from './DiagramInteractions.vue'
import { appendDiagramControls } from '@/utils/diagramControls'

it.each(['markdown-mermaid', 'editor-mermaid-preview'])('handles copied SVG controls in %s', async className => {
  const container = document.createElement('div')
  container.className = className
  container.innerHTML = '<svg viewBox="0 0 400 200"><text>Diagram</text></svg>'
  appendDiagramControls(container)
  const wrapper = mount(DiagramInteractions, { slots: { default: container.outerHTML }, attachTo: document.body })
  const svg = wrapper.get('svg').element as SVGSVGElement
  await wrapper.get('[data-diagram-action="in"]').trigger('click')
  expect(svg.style.width).toBe('480px')
  await wrapper.get('[data-diagram-action="reset"]').trigger('click')
  expect(svg.style.maxWidth).toBe('')
  const dialog = document.querySelector('dialog')!
  const show = vi.fn()
  dialog.showModal = show
  await wrapper.get('[data-diagram-action="view"]').trigger('click')
  await flushPromises()
  expect(show).toHaveBeenCalledOnce()
  expect(dialog.textContent).toContain('Diagram')
  wrapper.unmount()
})


it('arms wheel zoom with middle click and releases page scrolling after mouse movement', async () => {
  const wrapper = mount(DiagramInteractions, { slots: { default: '<div class="markdown-mermaid"><svg viewBox="0 0 400 200"><text>Chart</text></svg></div>' }, attachTo: document.body })
  const svg = wrapper.get('svg').element as SVGSVGElement
  const scroll = () => { const event = new WheelEvent('wheel', { deltaY: -100, bubbles: true, cancelable: true }); svg.dispatchEvent(event); return event }
  expect(scroll().defaultPrevented).toBe(false)
  await wrapper.get('svg').trigger('mousedown', { button: 1, clientX: 10, clientY: 20 })
  expect(scroll().defaultPrevented).toBe(true)
  expect(parseFloat(svg.style.width)).toBeGreaterThan(400)
  const width = svg.style.width
  document.dispatchEvent(new MouseEvent('mousemove', { clientX: 11, clientY: 20 }))
  expect(scroll().defaultPrevented).toBe(false)
  expect(svg.style.width).toBe(width)
  await wrapper.get('svg').trigger('mousedown', { button: 1 })
  window.dispatchEvent(new Event('blur'))
  expect(scroll().defaultPrevented).toBe(false)
  wrapper.unmount()
})


it('preserves Mermaid HTML node and edge labels in the viewer while removing active HTML', async () => {
  const container = document.createElement('div')
  container.className = 'markdown-mermaid'
  container.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200"><g class="nodeLabel"><foreignObject width="100" height="30"><div xmlns="http://www.w3.org/1999/xhtml"><span onclick="alert(1)">系统验证</span></div></foreignObject></g><g class="edgeLabel"><foreignObject width="100" height="30"><div xmlns="http://www.w3.org/1999/xhtml">验证通过<img src="x" onerror="alert(1)" /></div></foreignObject></g><text>结束</text></svg>`
  appendDiagramControls(container)
  const wrapper = mount(DiagramInteractions, { slots: { default: '<div class="markdown-mermaid"></div>' }, attachTo: document.body })
  wrapper.get('.markdown-mermaid').element.innerHTML = container.innerHTML
  const dialog = document.querySelector('dialog')!
  dialog.showModal = vi.fn()
  await wrapper.get('[data-diagram-action="view"]').trigger('click')
  await flushPromises()
  expect(dialog.querySelectorAll('foreignObject')).toHaveLength(2)
  expect(dialog.textContent).toContain('系统验证')
  expect(dialog.textContent).toContain('验证通过')
  expect(dialog.textContent).toContain('结束')
  expect(dialog.querySelector('[onclick], [onerror], script')).toBeNull()
  wrapper.unmount()
})


it('zooms directly in the viewer with bounded speed even for a large wheel delta', async () => {
  const container = document.createElement('div')
  container.className = 'markdown-mermaid'
  container.innerHTML = '<svg viewBox="0 0 400 200"><text>Chart</text></svg>'
  appendDiagramControls(container)
  const wrapper = mount(DiagramInteractions, { slots: { default: container.outerHTML }, attachTo: document.body })
  const dialog = document.querySelector('dialog')!
  dialog.showModal = vi.fn()
  await wrapper.get('[data-diagram-action="view"]').trigger('click')
  const event = new WheelEvent('wheel', { deltaY: -10000, bubbles: true, cancelable: true })
  dialog.querySelector('.diagram-viewer-scroll')!.dispatchEvent(event)
  await flushPromises()
  expect(event.defaultPrevented).toBe(true)
  expect(Number(dialog.querySelector('output')!.textContent!.replace('%', ''))).toBeGreaterThan(100)
  expect(Number(dialog.querySelector('output')!.textContent!.replace('%', ''))).toBeLessThanOrEqual(105)
  wrapper.unmount()
})


it('starts wheel zoom from the fitted width instead of the intrinsic SVG width', async () => {
  const wrapper = mount(DiagramInteractions, { slots: { default: '<div class="markdown-mermaid"><svg viewBox="0 0 4000 2000"></svg></div>' }, attachTo: document.body })
  const svg = wrapper.get('svg').element as SVGSVGElement
  vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ width: 400, height: 200, left: 0, top: 0 } as DOMRect)
  await wrapper.get('svg').trigger('mousedown', { button: 1 })
  svg.dispatchEvent(new WheelEvent('wheel', { deltaY: -100, bubbles: true, cancelable: true }))
  expect(parseFloat(svg.style.width)).toBeGreaterThan(400)
  expect(parseFloat(svg.style.width)).toBeLessThanOrEqual(420)
  wrapper.unmount()
})

it('keeps the cursor point fixed by adjusting the scroll container during zoom', async () => {
  let frame: FrameRequestCallback | undefined
  const raf = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => { frame = callback; return 1 })
  const cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {})
  const wrapper = mount(DiagramInteractions, { slots: { default: '<div class="markdown-mermaid" style="overflow-x:auto;overflow-y:auto"><svg viewBox="0 0 400 200"></svg></div>' }, attachTo: document.body })
  try {
    const container = wrapper.get('.markdown-mermaid').element as HTMLElement
    const svg = wrapper.get('svg').element as SVGSVGElement
    vi.spyOn(svg, 'getBoundingClientRect').mockImplementation(() => {
      const width = parseFloat(svg.style.width) || 400
      return { width, height: width / 2, left: -container.scrollLeft, top: -container.scrollTop } as DOMRect
    })
    await wrapper.get('svg').trigger('mousedown', { button: 1, clientX: 100, clientY: 50 })
    svg.dispatchEvent(new WheelEvent('wheel', { clientX: 100, clientY: 50, deltaY: -100, bubbles: true, cancelable: true }))
    frame?.(performance.now())
    const rect = svg.getBoundingClientRect()
    expect(rect.left + rect.width * .25).toBeCloseTo(100)
    expect(rect.top + rect.height * .25).toBeCloseTo(50)
    expect(container.scrollLeft).toBeGreaterThan(0)
  } finally {
    wrapper.unmount()
    raf.mockRestore(); cancel.mockRestore()
  }
})


it('fits the full chart on open and clears the previous viewport scroll', async () => {
  const wrapper = mount(DiagramInteractions, { slots: { default: '<div class="markdown-mermaid"><svg viewBox="0 0 2400 200"><text>Final task</text></svg><button data-diagram-action="view">View</button></div>' }, attachTo: document.body })
  const dialog = document.querySelector('dialog')!
  dialog.showModal = vi.fn()
  const viewport = dialog.querySelector('.diagram-viewer-scroll') as HTMLElement
  Object.defineProperty(viewport, 'clientWidth', { value: 1000 })
  Object.defineProperty(viewport, 'clientHeight', { value: 600 })
  viewport.scrollLeft = 900
  viewport.scrollTop = 30
  await wrapper.get('[data-diagram-action="view"]').trigger('click')
  await flushPromises()
  expect((dialog.querySelector('.diagram-viewer-image') as HTMLElement).style.width).toBe('1000px')
  expect(viewport.scrollLeft).toBe(0)
  expect(viewport.scrollTop).toBe(0)
  expect(dialog.textContent).toContain('Final task')
  wrapper.unmount()
})
