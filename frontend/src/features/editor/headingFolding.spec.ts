// @vitest-environment happy-dom
import { expect, it } from 'vitest'
import { Schema } from '@milkdown/kit/prose/model'
import { EditorState, TextSelection } from '@milkdown/kit/prose/state'
import { headingSections, headingFoldTransaction } from './headingFolding'

const schema = new Schema({ nodes: {
  doc: { content: 'block+' }, text: { group: 'inline' },
  heading: { group: 'block', content: 'inline*', attrs: { level: { default: 1 } } },
  paragraph: { group: 'block', content: 'inline*' },
  blockquote: { group: 'block', content: 'block+' },
} })
const h = (level: number, text: string) => schema.nodes.heading!.create({ level }, schema.text(text))
const p = (text: string) => schema.nodes.paragraph!.create(null, schema.text(text))
it('ends sections at same-or-higher headings and confines nested quotes to their parent', () => {
  const doc = schema.nodes.doc!.create(null, [h(1, 'A'), p('a'), h(2, 'B'), p('b'), h(1, 'C'), p('c'), schema.nodes.blockquote!.create(null, [h(2, 'D'), p('d')])])
  const sections = headingSections(doc)
  expect(sections.map(section => doc.nodeAt(section.from)?.textContent)).toEqual(['A', 'B', 'C', 'D'])
  expect(sections[0]!.end).toBe(sections[2]!.from)
  expect(sections[1]!.end).toBe(sections[2]!.from)
  expect(sections[3]!.end).toBe(doc.content.size - 1)
})
it('moves the caret out of collapsed content without changing document content or history', () => {
  const doc = schema.nodes.doc!.create(null, [h(1, 'A'), p('body'), h(1, 'B')])
  const state = EditorState.create({ doc, selection: TextSelection.create(doc, 5) })
  const tr = headingFoldTransaction(state, 'all')!
  expect(tr.doc.eq(doc)).toBe(true)
  expect(tr.docChanged).toBe(false)
  expect(tr.selection.from).toBe(1)
  expect(tr.getMeta('addToHistory')).toBe(false)
  expect(headingSections(doc)).toHaveLength(1)
})
