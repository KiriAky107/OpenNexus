import { $nodeSchema, $prose } from '@milkdown/kit/utils'
import { editorViewCtx, parserCtx, serializerCtx, type Editor } from '@milkdown/kit/core'
import { Plugin, TextSelection } from '@milkdown/kit/prose/state'
import { Fragment, type Node as ProseNode } from '@milkdown/kit/prose/model'
import { Decoration, DecorationSet, type EditorView } from '@milkdown/kit/prose/view'
import { closeHistory } from '@milkdown/kit/prose/history'
import { t } from '@/i18n'
import './liveSource.css'

type Kind = 'html' | 'link' | 'image'
type Range = { from: number; to: number; kind: Kind }

// A source editor is document content, not a detached form. Autosave always
// serializes its current value, including an unfinished edit.
export const liveSourceSchema = $nodeSchema('live_source', () => ({
  inline: true, group: 'inline', content: 'text*', marks: '', defining: true,
  attrs: { original: { default: '' }, kind: { default: 'html' } },
  toDOM: node => ['span', { 'data-live-source': node.attrs.kind, 'data-source-original': node.attrs.original, class: 'live-source-editor' }, 0],
  parseDOM: [{ tag: 'span[data-live-source]', preserveWhitespace: 'full', getAttrs: element => ({ kind: (element as HTMLElement).dataset.liveSource, original: (element as HTMLElement).dataset.sourceOriginal ?? '' }) }],
  parseMarkdown: { match: () => false, runner: () => {} },
  toMarkdown: {
    match: node => node.type.name === 'live_source',
    runner: (state, node) => { state.addNode('html', undefined, node.textContent) },
  },
}))

export function sourceRanges(doc: ProseNode): Range[] {
  const ranges: Range[] = []
  doc.descendants((parent, offset) => {
    if (!parent.isTextblock || parent.type.spec.code) return
    const stack: Array<{ tag: string; from: number }> = []
    let link: (Range & { href: string; title: unknown }) | undefined
    parent.forEach((node, relative) => {
      const from = offset + 1 + relative, to = from + node.nodeSize
      if (node.type.name === 'html') {
        const value = String(node.attrs.value).trim()
        const opening = /^<([a-z][\w-]*)(?:\s[^<>]*)?>$/i.exec(value)
        const closing = /^<\/([a-z][\w-]*)\s*>$/i.exec(value)
        if (opening && !/^(br|hr|img|input|meta|link|wbr)$/i.test(opening[1]!)) stack.push({ tag: opening[1]!.toLowerCase(), from })
        else if (closing && stack.at(-1)?.tag === closing[1]!.toLowerCase()) {
          ranges.push({ from: stack.pop()!.from, to, kind: 'html' })
        }
        ranges.push({ from, to, kind: 'html' })
      }
      if (node.type.name === 'image') ranges.push({ from, to, kind: 'image' })
      const mark = node.marks.find(mark => mark.type.name === 'link')
      if (mark && node.type.name !== 'live_source') {
        if (link && link.to === from && link.href === mark.attrs.href && link.title === mark.attrs.title) link.to = to
        else { link = { from, to, kind: 'link', href: mark.attrs.href, title: mark.attrs.title }; ranges.push(link) }
      } else link = undefined
    })
    return false
  })
  return ranges
}

function rangeAt(view: EditorView, position: number): Range | undefined {
  return sourceRanges(view.state.doc).filter(r => r.from <= position && position < r.to)
    .sort((a, b) => (b.to - b.from) - (a.to - a.from))[0]
}

function insertSource(view: EditorView, range: Range, source: string, original = source) {
  const node = view.state.schema.nodes.live_source!.create({ kind: range.kind, original }, source ? view.state.schema.text(source) : null)
  const tr = closeHistory(view.state.tr).replaceWith(range.from, range.to, node)
  tr.setSelection(TextSelection.create(tr.doc, range.from + 1 + source.length))
  view.dispatch(tr)
  view.dispatch(closeHistory(view.state.tr))
  view.focus()
}

export function beginSourceInsertion(editor: Editor, kind: Kind) {
  editor.action(ctx => {
    const view = ctx.get(editorViewCtx)
    if (view.state.selection.$from.parent.type.name === 'live_source') { view.focus(); return }
    const { from, to } = view.state.selection
    const existing = rangeAt(view, from)
    if (existing) {
      const paragraph = view.state.schema.nodes.paragraph!.create(null, view.state.doc.slice(existing.from, existing.to).content)
      insertSource(view, existing, ctx.get(serializerCtx)(view.state.schema.topNodeType.create(null, paragraph)).trimEnd())
      return
    }
    const label = view.state.doc.textBetween(from, to).replace(/[\\\[\]]/g, '\\$&') || t('显示文本', 'Display text')
    insertSource(view, { from, to, kind }, kind === 'html' ? '<div>内容</div>' : `${kind === 'image' ? '!' : ''}[${label}](https://)`, view.state.doc.textBetween(from, to))
  })
}

export const liveSourcePlugin = $prose(ctx => {
  const reveal = (view: EditorView, position: number) => {
    const range = rangeAt(view, position)
    if (!range) return false
    const paragraph = view.state.schema.nodes.paragraph!.create(null, view.state.doc.slice(range.from, range.to).content)
    const source = ctx.get(serializerCtx)(view.state.schema.topNodeType.create(null, paragraph)).trimEnd()
    insertSource(view, range, source)
    return true
  }
  const insideSource = (view: EditorView) => view.state.selection.$from.parent.type.name === 'live_source'
  const rendered = (node: ProseNode, viewSchema: ProseNode['type']['schema'], cancel = false) => {
    const value = cancel ? String(node.attrs.original) : node.textContent
    const parsed = ctx.get(parserCtx)(value)
    if (parsed?.childCount === 1 && parsed.firstChild?.type.name === 'paragraph') return parsed.firstChild.content
    if (!value) return Fragment.empty
    return Fragment.from(node.attrs.kind === 'html' ? viewSchema.nodes.html!.create({ value }) : viewSchema.text(value))
  }
  return new Plugin({
    appendTransaction(transactions, _old, state) {
      if (!transactions.some(tr => tr.selectionSet || tr.docChanged) || transactions.some(tr => tr.getMeta('live-source-finish'))) return null
      const inactive: Array<{ node: ProseNode; pos: number }> = []
      state.doc.descendants((node, pos) => {
        if (node.type.name !== 'live_source') return
        if (!(state.selection.from >= pos && state.selection.to <= pos + node.nodeSize)) inactive.push({ node, pos })
        return false
      })
      if (!inactive.length) return null
      const tr = state.tr.setMeta('live-source-finish', true)
      for (const { node, pos } of inactive.reverse()) tr.replaceWith(pos, pos + node.nodeSize, rendered(node, state.schema))
      return tr
    },
    props: {
      handleDOMEvents: {
        keydown(view, event) {
        const selection = view.state.selection
        const adjacent = selection.$from.nodeAfter
        if (!insideSource(view) && adjacent?.type.name === 'live_source' && selection.to <= selection.from + adjacent.nodeSize) {
          view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, selection.from + 1, Math.min(selection.to, selection.from + adjacent.nodeSize - 1))))
        }
        if (insideSource(view)) {
          if (event.isComposing || view.composing) return false
          const { $from, empty } = view.state.selection
          const finish = event.key === 'Escape' || event.key === 'Tab'
            || (event.key === 'Enter' && ((event.ctrlKey || event.metaKey) || $from.parent.attrs.kind !== 'html'))
          const left = empty && event.key === 'ArrowLeft' && $from.parentOffset === 0
          const right = empty && event.key === 'ArrowRight' && $from.parentOffset === $from.parent.content.size
          if (finish || left || right) {
            event.preventDefault()
            const pos = $from.before(), node = $from.parent
            const content = rendered(node, view.state.schema, event.key === 'Escape')
            const tr = closeHistory(view.state.tr).replaceWith(pos, pos + node.nodeSize, content).setMeta('live-source-finish', true)
            tr.setSelection(TextSelection.near(tr.doc.resolve(pos + (left ? 0 : content.size))))
            view.dispatch(tr)
            return true
          }
          if (event.key === 'Enter') {
            event.preventDefault()
            view.dispatch(view.state.tr.insertText('\n'))
            return true
          }
          return false
        }
          return false
        },
        blur(view) {
          if (insideSource(view) && !view.composing) {
            const pos = view.state.selection.$from.before()
            const node = view.state.selection.$from.parent
            view.dispatch(view.state.tr.replaceWith(pos, pos + node.nodeSize, rendered(node, view.state.schema)).setMeta('live-source-finish', true))
          }
          return false
        },
      },
      handleClick(view, position, event) {
        if (event.ctrlKey || event.metaKey || event.altKey || event.button !== 0) return false
        if ((event.target as Element)?.closest('summary,.live-source-editor')) return false
        return reveal(view, position)
      },
      handleKeyDown(view, event) {
        if (insideSource(view)) return false
        if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'k') {
          event.preventDefault()
          if (!reveal(view, view.state.selection.from)) {
            const { from, to } = view.state.selection
            const label = view.state.doc.textBetween(from, to).replace(/[\\\[\]]/g, '\\$&') || t('显示文本', 'Display text')
            insertSource(view, { from, to, kind: 'link' }, `[${label}](https://)`, view.state.doc.textBetween(from, to))
          }
          return true
        }
        if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key) && !event.shiftKey) {
          setTimeout(() => { if (!view.isDestroyed && view.hasFocus() && view.state.selection.empty) reveal(view, view.state.selection.from) }, 0)
        }
        return false
      },
      handleTextInput(view, from, to, text) {
        if (insideSource(view)) {
          const point = view.state.selection.$from, node = point.parent
          const start = Math.max(0, from - point.start()), end = Math.min(node.content.size, to - point.start())
          const value = node.textContent.slice(0, start) + text + node.textContent.slice(end)
          const pos = point.before()
          const tr = view.state.tr.replaceWith(pos, pos + node.nodeSize, node.type.create(node.attrs, value ? view.state.schema.text(value) : null))
          tr.setSelection(TextSelection.create(tr.doc, pos + 1 + start + text.length))
          view.dispatch(tr); return true
        }
        if (view.composing) return false
        const point = view.state.doc.resolve(from)
        if (!point.parent.isTextblock || point.parent.type.spec.code || point.marks().some(m => m.type.name === 'inlineCode')) return false
        const before = point.parent.textBetween(0, point.parentOffset, '\n', '\ufffc')
        if (/^<[a-z!/]/i.test(text)) {
          insertSource(view, { from, to, kind: 'html' }, text); return true
        }
        if (/[a-z!/]/i.test(text[0] ?? '') && /(^|[^\\])<$/.test(before)) {
          insertSource(view, { from: from - 1, to, kind: 'html' }, `<${text}`); return true
        }
        return false
      },
      handlePaste(view, event) {
        if (insideSource(view)) {
          const text = event.clipboardData?.getData('text/plain')
          if (text === undefined) return false
          view.dispatch(view.state.tr.insertText(text)); return true
        }
        if (view.state.selection.$from.parent.type.spec.code || view.state.selection.$from.marks().some(m => m.type.name === 'inlineCode')) return false
        const text = event.clipboardData?.getData('text/plain') ?? ''
        if (!/^\s*<[a-z!/]/i.test(text)) return false
        const { from, to } = view.state.selection
        insertSource(view, { from, to, kind: 'html' }, text)
        return true
      },
      decorations(state) {
        const decorations: Decoration[] = []
        state.doc.descendants((node, pos) => {
          if (node.type.name !== 'live_source') return
          for (const match of node.textContent.matchAll(/[\[\]()<>]/g)) {
            const from = pos + 1 + match.index!
            decorations.push(Decoration.inline(from, from + match[0].length, { class: 'live-source-punctuation' }))
          }
          return false
        })
        return DecorationSet.create(state.doc, decorations)
      },
    },
  })
})
