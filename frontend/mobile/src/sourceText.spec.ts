import { EditorState, Transaction, type StateCommand, type TransactionSpec } from '@codemirror/state'
import { history, isolateHistory, redo, undo } from '@codemirror/commands'
import { describe, expect, it } from 'vitest'
import { mobileSourceText } from './sourceText'

function editor(raw: string) {
  const source = mobileSourceText(raw)
  let state = EditorState.create({ doc: source.doc, extensions: [history(), source.extension] })
  return {
    get state() { return state }, read: () => source.read(state),
    change: (spec: TransactionSpec) => { state = state.update(spec).state },
    command: (command: StateCommand) => command({ get state() { return state }, dispatch: transaction => { state = transaction.state } }),
  }
}

describe('mobile Markdown original line endings', () => {
  it('keeps mixed source and emoji unchanged while inserted line breaks follow the existing style', () => {
    const original = '# 中文\r\n😀\nlast\rnext', e = editor(original)
    expect(e.read()).toBe(original)
    e.change({ changes: { from: e.state.doc.length, insert: '!\nnew' } })
    expect(e.read()).toBe(original + '!\r\nnew')
    expect(e.command(undo)).toBe(true); expect(e.read()).toBe(original)
    expect(e.command(redo)).toBe(true); expect(e.read()).toBe(original + '!\r\nnew')
  })

  it('restores exact deleted LF/CRLF/CR bytes through undo and redo of a multi-line replacement', () => {
    const original = 'a\r\nb\nc\rd', e = editor(original)
    e.change({ changes: { from: 1, to: 6, insert: '\n替换\n' } })
    expect(e.read()).toBe('a\r\n替换\r\nd')
    e.command(undo); expect(e.read()).toBe(original)
    e.command(redo); expect(e.read()).toBe('a\r\n替换\r\nd')
  })

  it('preserves both default and alternate endings in a grouped history deletion', () => {
    const original = 'one\r\ntwo\nthree\rend', e = editor(original)
    e.change({ changes: { from: 0, to: 4 }, annotations: [Transaction.userEvent.of('delete.forward'), Transaction.time.of(100)] })
    e.change({ changes: { from: 0, to: 4 }, annotations: [Transaction.userEvent.of('delete.forward'), Transaction.time.of(150)] })
    expect(e.read()).toBe('three\rend')
    e.command(undo); expect(e.read()).toBe(original)
    e.command(redo); expect(e.read()).toBe('three\rend')
  })

  it('maps original ending metadata through many independent edits and complete undo/redo', () => {
    const original = '😀\r\n中文\nthird\rlast\r\n', e = editor(original)
    for (let index = 0; index < 24; index++) {
      const pos = index % 2 ? 0 : e.state.doc.length
      e.change({ changes: { from: pos, insert: `字${index}\n` }, annotations: isolateHistory.of('full') })
    }
    const final = e.read()
    for (let index = 0; index < 24; index++) expect(e.command(undo)).toBe(true)
    expect(e.read()).toBe(original)
    for (let index = 0; index < 24; index++) expect(e.command(redo)).toBe(true)
    expect(e.read()).toBe(final)
  })
})
