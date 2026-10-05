// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { EditorView } from '@codemirror/view'
import { insertNewlineAndIndent } from '@codemirror/commands'
import { syntaxTree } from '@codemirror/language'
import ExperimentFileEditor from './ExperimentFileEditor.vue'
import { useEditorStore } from '@/stores/editor'
import { executeEditorCommand, getEditorCommandCapabilities } from '@/services/editorCommandService'
import { MAX_EXPERIMENT_EDITOR_BYTES } from '@/services/workspaceDocuments'
import * as workspaceService from '@/services/workspaceService'

let wrapper: VueWrapper | undefined
function setup(path: string, content: string) {
  localStorage.clear(); setActivePinia(createPinia())
  const editor = useEditorStore()
  editor.currentFilePath = path; editor.content = content; editor.saveStatus = 'saved'
  const autosave = vi.spyOn(editor, 'scheduleAutoSave').mockImplementation(() => {})
  wrapper = mount(ExperimentFileEditor, { attachTo: document.body })
  return { editor, autosave }
}
function view() { return EditorView.findFromDOM(wrapper!.get('.cm-editor').element as HTMLElement)! }
function replace(content: string) { const target = view(); target.dispatch({ changes: { from: 0, to: target.state.doc.length, insert: content } }) }
afterEach(() => {
  wrapper?.unmount(); wrapper = undefined; useEditorStore().cancelPendingAutoSave()
  vi.restoreAllMocks(); vi.useRealTimers()
})

describe('experiment code editor', () => {
  it('keeps the CSV source, selection and undo history through the bounded table preview', async () => {
    const { editor, autosave } = setup('/experiments/input.csv', 'name,value\n"A, B",3\n')
    const target = view(); replace('name,value\n"A, B",4\n'); target.dispatch({ selection: { anchor: 8 } })
    await wrapper!.get('button[aria-pressed="false"]').trigger('click')
    expect(wrapper!.get('[role="region"]').text()).toContain('A, B4')
    expect(wrapper!.get('.experiment-editor__source').isVisible()).toBe(false)
    expect(await executeEditorCommand('editor.undo')).toMatchObject({ ok: false, reason: 'unavailable' })
    await wrapper!.get('button[aria-pressed="true"]').trigger('click')
    expect(view()).toBe(target); expect(target.state.selection.main.head).toBe(8)
    expect(await executeEditorCommand('editor.undo')).toEqual({ ok: true }); expect(editor.content).toBe('name,value\n"A, B",3\n')
    expect(await executeEditorCommand('editor.redo')).toEqual({ ok: true }); expect(editor.content).toBe('name,value\n"A, B",4\n')
    expect(autosave).toHaveBeenCalledTimes(3)
  })

  it('retains Windows line endings and Chinese text through edits, preview, undo and redo', async () => {
    const original = 'name,value\r\n甲,3\r\n'
    const { editor } = setup('/experiments/中文#%.csv', original)
    replace('name,value\n甲,4\n乙,5\n'); expect(editor.content).toBe('name,value\r\n甲,4\r\n乙,5\r\n')
    await wrapper!.get('button[aria-pressed="false"]').trigger('click'); expect(wrapper!.get('table').text()).toContain('甲4乙5')
    await wrapper!.get('button[aria-pressed="true"]').trigger('click')
    await executeEditorCommand('editor.undo'); expect(editor.content).toBe(original)
    await executeEditorCommand('editor.redo'); expect(editor.content).toBe('name,value\r\n甲,4\r\n乙,5\r\n')
    expect(wrapper!.get('.experiment-editor__format').text()).toContain('CRLF')
    const save = vi.spyOn(workspaceService, 'saveFileContent').mockResolvedValue()
    await editor.save()
    expect(save).toHaveBeenCalledWith('/experiments/中文#%.csv', 'name,value\r\n甲,4\r\n乙,5\r\n', undefined)
    expect(editor.saveStatus).toBe('saved')
  })

  it('uses the advertised Ctrl+Shift+Z redo shortcut and keeps Ctrl+Y on Windows', () => {
    const original = 'print("中文")\r\n', changed = original + '# 原生输入中文😀'
    const { editor } = setup('/experiments/课程/示例 #%.py', original)
    const target = view(); replace(changed)
    const key = (value: string, shift = false) => target.contentDOM.dispatchEvent(
      new KeyboardEvent('keydown', { key: value, code: `Key${value.toUpperCase()}`, keyCode: value.toUpperCase().charCodeAt(0), ctrlKey: true, shiftKey: shift, bubbles: true, cancelable: true }),
    )
    key('z'); expect(editor.content).toBe(original)
    key('Z', true); expect(editor.content).toBe(changed)
    key('z'); expect(editor.content).toBe(original)
    key('y'); expect(editor.content).toBe(changed)
  })

  it('cancels a pending disk save and waits until Chinese composition ends', async () => {
    const { editor, autosave } = setup('/experiments/中文.py', 'print("")\r\n')
    vi.useFakeTimers(); autosave.mockRestore()
    const realAutosave = vi.spyOn(editor, 'scheduleAutoSave')
    replace('print("z")\n'); expect(realAutosave).toHaveBeenCalledOnce()
    const content = wrapper!.get('.cm-content'), cancel = vi.spyOn(editor, 'cancelPendingAutoSave')
    await content.trigger('compositionstart'); expect(cancel).toHaveBeenCalled()
    replace('print("中文")\n'); expect(editor.content).toBe('print("中文")\r\n'); expect(realAutosave).toHaveBeenCalledOnce()
    await content.trigger('compositionend'); expect(realAutosave).toHaveBeenCalledTimes(2)
    editor.cancelPendingAutoSave()
  })

  it('rejects stale view input and delayed composition after a different file opens', async () => {
    const { editor, autosave } = setup('/experiments/old.py', 'old')
    const target = view(), content = wrapper!.get('.cm-content')
    await content.trigger('compositionstart')
    editor.currentFilePath = '/experiments/new.py'; editor.content = 'keep new source'; editor.saveStatus = 'saved'
    target.dispatch({ changes: { from: 0, insert: 'late old input' } }); await content.trigger('compositionend')
    expect(editor.content).toBe('keep new source'); expect(editor.saveStatus).toBe('saved')
    expect(autosave).not.toHaveBeenCalled(); expect(target.state.doc.toString()).toBe('old')
  })

  it.each(['conflict', 'external_changed'] as const)('blocks keyboard/programmatic changes and menu history during %s', async status => {
    const { editor } = setup('/experiments/main.py', 'print(1)\n')
    replace('print(2)\n'); editor.saveStatus = status; await nextTick(); replace('overwrite conflict')
    expect(view().contentDOM.getAttribute('contenteditable')).toBe('false'); expect(view().state.readOnly).toBe(true)
    expect(editor.content).toBe('print(2)\n')
    expect(await executeEditorCommand('editor.undo')).toMatchObject({ ok: false, reason: 'unavailable' })
    expect(getEditorCommandCapabilities().find(item => item.id === 'editor.bold')?.supported).toBe(false)
  })

  it.each([
    ['main.py', 'if True:\n    print("中文")\n', 'Script'],
    ['data.json', '{"中文": [1, true]}', 'JsonText'],
  ])('loads the actual %s language parser without treating it as Markdown', (path, content, node) => {
    setup('/experiments/' + path, content); expect(syntaxTree(view().state).topNode.name).toBe(node)
    expect(wrapper!.get('.cm-content').attributes('aria-label')).toBe('实验文件源码'); expect(view().state.doc.toString()).toBe(content)
  })

  it('indents Python blocks and supports native-menu undo/redo for the same transaction', async () => {
    const { editor } = setup('/experiments/main.py', 'if True:')
    view().dispatch({ selection: { anchor: view().state.doc.length } }); expect(insertNewlineAndIndent(view())).toBe(true)
    expect(editor.content).toBe('if True:\n    ')
    await executeEditorCommand('editor.undo'); expect(editor.content).toBe('if True:')
    await executeEditorCommand('editor.redo'); expect(editor.content).toBe('if True:\n    ')
  })

  it('enforces the UTF-8 byte limit while keeping the original source and its undo history', async () => {
    const { editor, autosave } = setup('/experiments/main.py', 'print("中文")\n')
    replace('print("保留")\n'); const preserved = editor.content
    replace('甲'.repeat(Math.ceil(MAX_EXPERIMENT_EDITOR_BYTES / 3))); await nextTick()
    expect(editor.content).toBe(preserved); expect(view().state.doc.toString()).toBe(preserved)
    expect(wrapper!.get('[role="alert"]').text()).toContain('2 MiB'); expect(autosave).toHaveBeenCalledOnce()
    await executeEditorCommand('editor.undo'); expect(editor.content).toBe('print("中文")\n')
  })

  it('does not instantiate or silently truncate an oversized opened source', () => {
    const original = 'x'.repeat(MAX_EXPERIMENT_EDITOR_BYTES + 1), { editor, autosave } = setup('/experiments/huge.json', original)
    expect(wrapper!.find('.cm-editor').exists()).toBe(false); expect(wrapper!.get('[role="alert"]').text()).toContain('完整保留')
    expect(editor.content).toBe(original); expect(editor.saveStatus).toBe('saved'); expect(autosave).not.toHaveBeenCalled()
  })
})
