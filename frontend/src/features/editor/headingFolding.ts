import { $prose } from '@milkdown/kit/utils'
import { Plugin, PluginKey, TextSelection, type EditorState } from '@milkdown/kit/prose/state'
import { Decoration, DecorationSet } from '@milkdown/kit/prose/view'
import type { Node } from '@milkdown/kit/prose/model'
import { t } from '@/i18n'

export const headingFoldKey = new PluginKey<Set<number>>('heading-folding')
type Section = { from: number; body: number; end: number; level: number }
const sectionCache = new WeakMap<Node, Section[]>()
const decorationCache = new WeakMap<Node, WeakMap<Set<number>, DecorationSet>>()
/** 节以相同或更高级别的下一个同级标题结束。 */
export function headingSections(doc: Node): Section[] {
  const cached = sectionCache.get(doc)
  if (cached) return cached
  const sections: Section[] = []
  function visit(parent: Node, start: number) {
    const children: { node: Node; pos: number }[] = []
    parent.forEach((node, offset) => children.push({ node, pos: start + offset }))
    const following: { pos: number; level: number }[] = []
    for (let i = children.length - 1; i >= 0; i--) {
      const { node, pos } = children[i]!
      if (node.type.name === 'heading') {
        while (following.length && following[following.length - 1]!.level > node.attrs.level) following.pop()
        const next = following[following.length - 1]
        const body = pos + node.nodeSize
        const end = next?.pos ?? start + parent.content.size
        if (end > body) sections.push({ from: pos, body, end, level: Number(node.attrs.level) })
        following.push({ pos, level: Number(node.attrs.level) })
      }
      if (!node.isTextblock && node.childCount) visit(node, pos + 1)
    }
  }
  visit(doc, 0)
  sections.sort((a, b) => a.from - b.from)
  sectionCache.set(doc, sections)
  return sections
}

export function headingFoldTransaction(state: EditorState, action: 'toggle' | 'all' | 'none', position?: number) {
  const sections = headingSections(state.doc)
  const folded = new Set(headingFoldKey.getState(state) ?? [])
  if (action === 'none') folded.clear()
  else if (action === 'all') sections.forEach(section => folded.add(section.from))
  else {
    const section = position === undefined
      ? sections.filter(item => item.from <= state.selection.from && item.end >= state.selection.from).pop()
      : sections.find(item => item.from === position)
    if (!section) return null
    if (folded.has(section.from)) folded.delete(section.from)
    else folded.add(section.from)
  }
  const tr = state.tr
  const enclosing = sections.find(section => folded.has(section.from) && state.selection.to >= section.body && state.selection.from < section.end)
  if (action === 'all' && sections.length) tr.setSelection(TextSelection.near(state.doc.resolve(sections[0]!.from + 1))).scrollIntoView()
  else if (enclosing) tr.setSelection(TextSelection.near(state.doc.resolve(enclosing.from + 1)))
  return tr.setMeta(headingFoldKey, folded).setMeta('addToHistory', false)
}

export const headingFoldingPlugin = $prose(() => new Plugin<Set<number>>({
  key: headingFoldKey,
  state: {
    init: () => new Set(),
    apply(tr, previous) {
      const explicit = tr.getMeta(headingFoldKey) as Set<number> | undefined
      if (explicit) return explicit
      if (!previous.size) return previous
      const sections = headingSections(tr.doc)
      if (!tr.docChanged) {
        if (!tr.selectionSet) return previous
        const opened = sections.filter(section => previous.has(section.from) && tr.selection.to >= section.body && tr.selection.from < section.end)
        if (!opened.length) return previous
        const next = new Set(previous)
        opened.forEach(section => next.delete(section.from))
        return next
      }
      const positions = new Set(sections.map(section => section.from))
      const mapped = new Set<number>()
      for (const old of previous) {
        const result = tr.mapping.mapResult(old, 1)
        if (!result.deleted && positions.has(result.pos)) mapped.add(result.pos)
      }
      // 轮廓跳转、查找和键盘导航绝不能留下隐藏的插入符号。
      if (tr.selectionSet || tr.docChanged) {
        for (const section of sections) if (tr.selection.to >= section.body && tr.selection.from < section.end) mapped.delete(section.from)
      }
      return mapped
    },
  },
  props: {
    decorations(state) {
      const folded = headingFoldKey.getState(state) ?? new Set<number>()
      const cached = decorationCache.get(state.doc)?.get(folded)
      if (cached) return cached
      const sections = headingSections(state.doc)
      const decorations: Decoration[] = []
      for (const section of sections) {
        const collapsed = folded.has(section.from)
        decorations.push(Decoration.widget(section.from + 1, view => {
          const button = document.createElement('button')
          button.type = 'button'; button.className = 'heading-fold-toggle'; button.contentEditable = 'false'
          button.setAttribute('aria-expanded', String(!collapsed))
          button.setAttribute('aria-label', `${collapsed ? t('展开', 'Expand') : t('折叠', 'Collapse')} H${section.level} ${state.doc.nodeAt(section.from)?.textContent ?? ''}`)
          button.onmousedown = event => event.preventDefault()
          button.onclick = event => {
            event.preventDefault()
            const tr = headingFoldTransaction(view.state, 'toggle', section.from)
            if (tr) view.dispatch(tr)
          }
          return button
        }, { key: `${section.from}:${collapsed}:${state.doc.nodeAt(section.from)?.textContent}`, side: -1, stopEvent: () => true }))
      }
      const hidden: { body: number; end: number }[] = []
      for (const section of sections) {
        if (!folded.has(section.from)) continue
        const previous = hidden[hidden.length - 1]
        if (previous && section.body <= previous.end) previous.end = Math.max(previous.end, section.end)
        else hidden.push({ body: section.body, end: section.end })
      }
      let rangeIndex = 0
      if (hidden.length) state.doc.descendants((node, pos) => {
        if (!node.isBlock) return
        while (hidden[rangeIndex] && pos >= hidden[rangeIndex]!.end) rangeIndex++
        const range = hidden[rangeIndex]
        if (range && pos >= range.body && pos + node.nodeSize <= range.end) {
          decorations.push(Decoration.node(pos, pos + node.nodeSize, { class: 'heading-fold-hidden' }))
          return false
        }
      })
      const result = DecorationSet.create(state.doc, decorations)
      let byState = decorationCache.get(state.doc)
      if (!byState) { byState = new WeakMap(); decorationCache.set(state.doc, byState) }
      byState.set(folded, result)
      return result
    },
  },
}))
