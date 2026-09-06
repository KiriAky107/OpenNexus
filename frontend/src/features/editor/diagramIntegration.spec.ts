// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getMarkdown } from '@milkdown/kit/utils'
import type { Editor } from '@milkdown/kit/core'
import VisualMarkdownEditor from './VisualMarkdownEditor.vue'

vi.mock('@/services/mermaidService', () => ({ renderMermaid: vi.fn(async () => ({ svg: '<svg viewBox="0 0 400 200"><text>Diagram</text></svg>', warnings: [], width: 400, height: 200 })) }))
let wrapper: VueWrapper
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = ''; vi.unstubAllGlobals() })
it('keeps diagram buttons usable after real Milkdown preview copying without changing Markdown', async () => {
  localStorage.clear()
  setActivePinia(createPinia())
  vi.stubGlobal('IntersectionObserver', class {
    constructor(private callback: IntersectionObserverCallback) {}
    observe(target: Element) { queueMicrotask(() => this.callback([{ isIntersecting: true, target } as IntersectionObserverEntry], this as unknown as IntersectionObserver)) }
    unobserve() {}
    disconnect() {}
  })
  wrapper = mount(VisualMarkdownEditor, { props: { initialContent: '```mermaid\ngraph TD; A-->B\n```' }, attachTo: document.body })
  await vi.waitFor(() => expect(wrapper.find('[data-diagram-action="in"]').exists()).toBe(true), { timeout: 3000 })
  const editor = (wrapper.vm as unknown as { getEditor(): Editor }).getEditor()
  const before = editor.action(getMarkdown())
  await wrapper.get('[data-diagram-action="in"]').trigger('click')
  expect((wrapper.get('.editor-mermaid-preview svg').element as SVGSVGElement).style.width).toBe('480px')
  expect(editor.action(getMarkdown())).toBe(before)
})
