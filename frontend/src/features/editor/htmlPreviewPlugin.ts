import DOMPurify from 'dompurify'
import { $prose } from '@milkdown/kit/utils'
import { Plugin } from '@milkdown/kit/prose/state'
import { Decoration, DecorationSet } from '@milkdown/kit/prose/view'
import type { Node as ProseNode } from '@milkdown/kit/prose/model'
import { loadWorkspaceImage, resolveWorkspaceAssetPath } from '@/services/workspaceService'
import './htmlPreview.css'

const inlineTags = new Set(['strong', 'b', 'em', 'i', 's', 'del', 'u', 'mark', 'sub', 'sup', 'small', 'kbd', 'span', 'a', 'code'])
const tags = [...inlineTags, 'div', 'p', 'br', 'hr', 'blockquote', 'pre', 'code', 'ul', 'ol', 'li',
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
  'details', 'summary', 'a', 'img', 'figure', 'figcaption', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']
const styles = new Set(['color', 'background-color', 'font-size', 'font-weight', 'font-style', 'font-family',
  'text-align', 'text-decoration', 'line-height', 'letter-spacing', 'white-space', 'vertical-align',
  'border', 'border-color', 'border-width', 'border-style', 'border-radius', 'border-collapse',
  'padding', 'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
  'margin', 'margin-top', 'margin-right', 'margin-bottom', 'margin-left', 'width', 'max-width', 'height'])

/** Render only inert document HTML; never mount raw HTML or allow page-level CSS. */
export function safeHtmlFragment(source: string): DocumentFragment {
  const fragment = DOMPurify.sanitize(source, {
    ALLOWED_TAGS: tags,
    ALLOWED_ATTR: ['style', 'title', 'href', 'src', 'alt', 'width', 'height', 'colspan', 'rowspan', 'open', 'start'],
    ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false, RETURN_DOM_FRAGMENT: true,
  })
  for (const element of fragment.querySelectorAll<HTMLElement>('[style]')) {
    const input = element.style
    const accepted: Array<[string, string]> = []
    for (let index = 0; index < input.length; index++) {
      const name = input.item(index)
      const value = input.getPropertyValue(name)
      if (styles.has(name) && !/url\s*\(|expression\s*\(|var\s*\(|[\\@]/i.test(value)) accepted.push([name, value])
    }
    element.removeAttribute('style')
    for (const [name, value] of accepted) element.style.setProperty(name, value)
  }
  for (const image of fragment.querySelectorAll('img')) image.referrerPolicy = 'no-referrer'
  return fragment
}

export function htmlPreviewPlugin(context: () => { path: string | null; noteId: string | null }) {
  const cache = new WeakMap<ProseNode, DecorationSet>()
  return $prose(() => new Plugin({
    props: {
      decorations(state) {
        const cached = cache.get(state.doc)
        if (cached) return cached
        const decorations: Decoration[] = []
        state.doc.descendants((parent, offset) => {
          if (!parent.isTextblock) return
          const stack: Array<{ tag: string; start: number; end: number; attrs: Record<string, string> }> = []
          parent.forEach((node, relative) => {
            if (node.type.name !== 'html') return
            const value = String(node.attrs.value ?? '').trim()
            const pos = offset + 1 + relative
            const open = /^<([a-z][\w-]*)(?:\s[^<>]*)?>$/i.exec(value)
            const close = /^<\/([a-z][\w-]*)\s*>$/i.exec(value)
            if (open && inlineTags.has(open[1]!.toLowerCase())) {
              const tag = open[1]!.toLowerCase()
              const safe = safeHtmlFragment(`${value}</${tag}>`).firstElementChild
              const attrs: Record<string, string> = {}
              for (const name of ['style', 'title', 'href']) {
                const value = safe?.getAttribute(name)
                if (value) attrs[name] = value
              }
              stack.push({ tag, start: pos, end: pos + node.nodeSize, attrs })
            } else if (close && stack.at(-1)?.tag === close[1]!.toLowerCase()) {
              const opening = stack.pop()!
              decorations.push(Decoration.node(opening.start, opening.end, { class: 'html-source-marker' }))
              decorations.push(Decoration.node(pos, pos + node.nodeSize, { class: 'html-source-marker' }))
              if (opening.end < pos) decorations.push(Decoration.inline(opening.end, pos, {
                nodeName: opening.tag, ...opening.attrs,
              }))
            }
          })
          return false
        })
        const result = DecorationSet.create(state.doc, decorations)
        cache.set(state.doc, result)
        return result
      },
      nodeViews: {
        html(initial) {
          const dom = document.createElement('span')
          dom.dataset.type = 'html'
          dom.contentEditable = 'false'
          let generation = 0
          const urls: string[] = []
          const clean = () => { generation++; urls.splice(0).forEach(url => URL.revokeObjectURL(url)) }
          const update = (node: typeof initial) => {
            if (node.type.name !== 'html') return false
            clean()
            const current = generation
            const source = String(node.attrs.value ?? '')
            dom.dataset.value = source
            // Unpaired inline tags stay visible/editable through source mode;
            // matched tags are hidden by decorations without changing the document.
            if (/^<\/?(?:strong|b|em|i|s|del|u|mark|sub|sup|small|kbd|span|a|code)(?:\s[^<>]*)?\s*>$/i.test(source.trim())) {
              dom.className = 'html-source-tag'
              dom.textContent = source
              return true
            }
            dom.className = 'html-preview-content'
            dom.replaceChildren(safeHtmlFragment(source))
            if (/<(?:div|p|table|details|figure|ul|ol|blockquote|pre|h[1-6])(?:\s|>)/i.test(source)) dom.classList.add('html-preview-block')
            const target = context()
            for (const image of dom.querySelectorAll('img')) {
              const path = target.path && resolveWorkspaceAssetPath(target.path, image.getAttribute('src') ?? '')
              if (!path) continue
              image.removeAttribute('src')
              void loadWorkspaceImage(path, target.path!, target.noteId).then(blob => {
                if (generation !== current) return
                const url = URL.createObjectURL(blob); urls.push(url); image.src = url
              }).catch(() => { if (generation === current) image.title = 'Image could not be loaded' })
            }
            return true
          }
          update(initial)
          return { dom, update, ignoreMutation: () => true, destroy: clean }
        },
      },
    },
  }))
}
