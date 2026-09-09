// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SourceMarkdownEditor from './SourceMarkdownEditor.vue'
import { executeEditorCommand } from '@/services/editorCommandService'
import { useEditorStore } from '@/stores/editor'
import * as workspace from '@/services/workspaceService'

let wrapper: VueWrapper | undefined
const original = '***\ntitle: 中文\ntags: [一, 二, 一]\ncustom: [1, false]\n---\n# 正文\n'
beforeEach(async () => {
  localStorage.clear(); setActivePinia(createPinia())
  vi.spyOn(workspace, 'readFileContent').mockResolvedValue(original)
  vi.spyOn(workspace, 'getNoteId').mockResolvedValue('note-fixture')
  vi.spyOn(workspace, 'saveFileContent').mockResolvedValue()
  const store = useEditorStore(); store.setMode('source'); await store.loadFile('/fixture.md')
  wrapper = mount(SourceMarkdownEditor, { props: { initialContent: store.content }, attachTo: document.body })
})
afterEach(() => { wrapper?.unmount(); useEditorStore().closeFile(); vi.restoreAllMocks() })

it('完整属性转换是一笔可撤销重做的源码事务', async () => {
  const store = useEditorStore()
  expect(await executeEditorCommand('editor.import-note-properties')).toEqual({ ok: true })
  expect(store.content).toMatch(/^---\n/)
  const converted = store.content
  expect(store.saveStatus).toBe('dirty')
  expect(await executeEditorCommand('editor.undo')).toEqual({ ok: true })
  expect(store.content).toBe(original)
  expect(await executeEditorCommand('editor.redo')).toEqual({ ok: true })
  expect(store.content).toBe(converted)
})

it('保存失败保留转换后的内存正文，仍可撤销', async () => {
  const store = useEditorStore()
  vi.mocked(workspace.saveFileContent).mockRejectedValue(new Error('fixture disk full'))
  await executeEditorCommand('editor.import-note-properties')
  await store.save()
  expect(store.saveStatus).toBe('save_failed')
  expect(store.content).toMatch(/^---/)
  await executeEditorCommand('editor.undo')
  expect(store.content).toBe(original)
})

it('冲突文档禁用命令，保持原始内容', async () => {
  const store = useEditorStore(); store.saveStatus = 'conflict'
  expect(await executeEditorCommand('editor.import-note-properties')).toMatchObject({ ok: false, reason: 'unavailable' })
  expect(store.content).toBe(original)
})
