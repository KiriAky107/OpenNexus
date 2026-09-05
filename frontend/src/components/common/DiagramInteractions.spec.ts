// @vitest-environment happy-dom
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
