import { $prose } from '@milkdown/kit/utils'
import { Plugin } from '@milkdown/kit/prose/state'
import { Decoration, DecorationSet } from '@milkdown/kit/prose/view'
import type { Node as ProseNode } from '@milkdown/kit/prose/model'
import { parseCallout } from '@/utils/callouts'
import '@/styles/callouts.css'
import { remarkStringifyOptionsCtx, type Editor } from '@milkdown/kit/core'

export const configureCalloutSerialization: Parameters<Editor['config']>[0] = ctx => {
  ctx.update(remarkStringifyOptionsCtx, options => ({
    ...options,
    handlers: { ...options.handlers, blockquote(node, _parent, state, info) {
      const exit = state.enter('blockquote')
      const tracker = state.createTracker(info)
      tracker.move('> ')
      tracker.shift(2)
      const result = state.indentLines(state.containerFlow(node, tracker.current()), (line, _index, blank) => `>${blank ? '' : ' '}${line}`)
      exit()
      // 仅删除前导标注标记的转义，绝不删除正文文字。
      return result.replace(/^(> )\\\[!([\w-]+)\\?\]/, '$1[!$2]')
    } },
  }))
}

const markerCache = new WeakMap<ProseNode, { from: number; to: number }[]>()
function calloutMarkers(doc: ProseNode) {
  const cached = markerCache.get(doc)
  if (cached) return cached
  const markers: { from: number; to: number }[] = []
  doc.descendants((node, position) => {
    if (node.type.name !== 'blockquote' || node.firstChild?.type.name !== 'paragraph' || node.firstChild.firstChild?.marks.length) return
    const callout = parseCallout(node.firstChild.textBetween(0, node.firstChild.content.size, '\n', '\n'))
    if (callout) markers.push({ from: position + 2, to: position + 2 + callout.markerLength })
  })
  markerCache.set(doc, markers)
  return markers
}

// 在文档中保留本机块引用：键入、撤消和 Markdown 序列化保留 Milkdown 事务；该视图永远不会重写用户的标注源。
export const calloutPlugin = $prose(() => new Plugin({
  props: {
    decorations(state) {
      const decorations: Decoration[] = []
      for (const { from, to } of calloutMarkers(state.doc)) {
        const editing = state.selection.from <= to && state.selection.to >= from
        decorations.push(Decoration.inline(from, to, { class: editing ? 'callout-marker-editing' : 'callout-marker' }))
      }
      return DecorationSet.create(state.doc, decorations)
    },
    nodeViews: {
      blockquote(initialNode) {
        const dom = document.createElement('blockquote')
        const header = document.createElement('button')
        header.type = 'button'
        header.className = 'callout-title'
        header.contentEditable = 'false'
        const contentDOM = document.createElement('div')
        contentDOM.className = 'callout-body'
        dom.append(header, contentDOM)
        let signature = ''
        let foldable = false
        const update = (node: typeof initialNode) => {
          if (node.type.name !== 'blockquote') return false
          const callout = node.firstChild?.type.name === 'paragraph' && !node.firstChild.firstChild?.marks.length
            ? parseCallout(node.firstChild.textBetween(0, node.firstChild.content.size, '\n', '\n')) : null
          dom.className = callout ? 'markdown-callout' : ''
          header.hidden = !callout
          foldable = !!callout?.fold
          header.disabled = !foldable
          if (callout) {
            dom.dataset.callout = callout.type
            const next = `${callout.name}:${callout.fold}`
            if (signature !== next) dom.dataset.collapsed = String(callout.fold === '-')
            signature = next
            header.textContent = `${foldable ? (dom.dataset.collapsed === 'true' ? '▸ ' : '▾ ') : ''}${callout.title}`
            if (foldable) header.setAttribute('aria-expanded', String(dom.dataset.collapsed !== 'true'))
            else header.removeAttribute('aria-expanded')
          } else {
            delete dom.dataset.callout
            delete dom.dataset.collapsed
            signature = ''
          }
          return true
        }
        let current = initialNode
        header.onclick = () => {
          if (!foldable) return
          dom.dataset.collapsed = String(dom.dataset.collapsed !== 'true')
          update(current)
        }
        update(initialNode)
        return { dom, contentDOM, update(node) { current = node; return update(node) },
          stopEvent: event => header.contains(event.target as Node),
          ignoreMutation: mutation => mutation.type !== 'selection' && !contentDOM.contains(mutation.target) }
      },
    },
  },
}))
