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
