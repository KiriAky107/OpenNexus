// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getMarkdown } from '@milkdown/kit/utils'
import { editorViewCtx, type Editor } from '@milkdown/kit/core'
import VisualMarkdownEditor from './VisualMarkdownEditor.vue'
import { safeHtmlFragment } from './htmlPreviewPlugin'

let wrapper: VueWrapper | undefined
beforeEach(() => {
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals() })

it('sanitizes active content and layout-escaping CSS while retaining document formatting', () => {
  const fragment = safeHtmlFragment('<div style="color: red; position: fixed; background-image:url(https://example.com/pixel)"><strong onclick="evil()">Safe</strong><script>evil()</script><iframe src="https://example.com"></iframe><a href="javascript:evil()">Link</a></div>')
  const root = fragment.firstElementChild as HTMLElement
  expect(root.style.color).toBe('red')
  expect(root.style.position).toBe('')
  expect(root.style.backgroundImage).toBe('')
  expect(fragment.querySelector('script,iframe,[onclick]')).toBeNull()
  expect(fragment.querySelector('a')?.hasAttribute('href')).toBe(false)
  expect(fragment.querySelector('strong')?.textContent).toBe('Safe')
})

it('renders inline HTML and block HTML in the real editor without changing their source on save', async () => {
  localStorage.clear(); setActivePinia(createPinia())
  const inline = '这行包含 <strong>HTML 加粗</strong>、<em>HTML 强调</em>和换行<br>下一行。'
  const block = '<div style="text-align: center"><p>块级内容</p><table><tr><th>标题</th></tr><tr><td>单元格</td></tr></table><details><summary>展开</summary><p>详情</p></details></div>'
  const source = `${inline}\n\n${block}\n\n结尾。\n`
  wrapper = mount(VisualMarkdownEditor, { props: { initialContent: source }, attachTo: document.body })
  await vi.waitFor(() => expect(wrapper!.find('.html-preview-content table').exists()).toBe(true), { timeout: 5000 })
  expect(wrapper.find('.ProseMirror strong').text()).toBe('HTML 加粗')
  expect(wrapper.find('.ProseMirror em').text()).toBe('HTML 强调')
  expect(wrapper.findAll('.html-source-marker')).toHaveLength(4)
  expect(wrapper.find('.ProseMirror br').exists()).toBe(true)
  expect(wrapper.find('.html-preview-content td').text()).toBe('单元格')
  const editor = (wrapper.vm as unknown as { getEditor(): Editor }).getEditor()
  const before = editor.action(getMarkdown())
  expect(before).toContain(inline)
  expect(before).toContain(block)
  editor.action(ctx => { const view = ctx.get(editorViewCtx); view.dispatch(view.state.tr.insertText('新增', view.state.doc.content.size - 1)) })
  const saved = editor.action(getMarkdown())
  expect(saved).toContain(inline)
  expect(saved).toContain(block)
  expect(saved).toContain('新增')
})

it('does not interpret escaped HTML or fenced HTML as a live preview', async () => {
  localStorage.clear(); setActivePinia(createPinia())
  wrapper = mount(VisualMarkdownEditor, { props: { initialContent: '转义：&lt;strong&gt;文本&lt;/strong&gt;\n\n```html\n<div>代码示例</div>\n```' }, attachTo: document.body })
  await vi.waitFor(() => expect(wrapper!.find('.ProseMirror').exists()).toBe(true), { timeout: 5000 })
  expect(wrapper.find('.html-preview-content').exists()).toBe(false)
  expect(wrapper.find('.ProseMirror').text()).toContain('<strong>文本</strong>')
})
