// @vitest-environment jsdom
import { expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import DiagramInteractions from '@/components/common/DiagramInteractions.vue'
import { renderMarkdown } from './markdown'
vi.mock('@/services/mermaidService', () => ({ renderMermaid: vi.fn(async () => ({ warnings: [], svg: '<svg viewBox="0 0 400 200"><foreignObject width="100" height="30"><div xmlns="http://www.w3.org/1999/xhtml"><span>系统验证</span><img src="x" onerror="alert(1)"></div></foreignObject></svg>' })) }))
it('preserves diagram labels, switches preview/source, and copies original Mermaid', async () => {
  const source = 'graph TD; A-->B'
  const html = await renderMarkdown('```mermaid\n' + source + '\n```')
  const wrapper = mount(DiagramInteractions, { slots: { default: '<div></div>' }, attachTo: document.body })
  // 在注入清理后的渲染 HTML 时保留 SVGforeignObject 命名空间。
  wrapper.element.firstElementChild!.innerHTML = html
  expect(wrapper.text()).toContain('系统验证')
  expect(wrapper.find('[onerror]').exists()).toBe(false)
  const raw = wrapper.get('.markdown-code-source').element as HTMLElement
  const svg = wrapper.get('.markdown-mermaid > svg').element as SVGSVGElement
  expect(raw.hidden).toBe(true)
  await wrapper.get('[data-code-action="source"]').trigger('click')
  expect(raw.hidden).toBe(false)
  expect(svg.style.display).toBe('none')
  await wrapper.get('[data-code-action="source"]').trigger('click')
  expect(raw.hidden).toBe(true)
  expect(svg.style.display).toBe('')
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  await wrapper.get('[data-code-action="copy"]').trigger('click')
  await flushPromises()
  expect(writeText).toHaveBeenCalledWith(source + '\n')
  wrapper.unmount()
})
