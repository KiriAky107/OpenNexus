// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'
import { beforeEach, expect, it, vi } from 'vitest'
import NoteHistory from './NoteHistory.vue'
import { listNoteChanges, getNoteChange, restoreNoteChange } from '@/services/noteService'
const state = vi.hoisted(() => ({ editor: null as unknown as { currentNoteId: string; saveStatus: string; checkExternalFile: ReturnType<typeof vi.fn> }, workspace: null as unknown as { vaultId: string } }))
vi.mock('@/stores/editor', () => ({ useEditorStore: () => state.editor }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => state.workspace }))
vi.mock('@/services/noteService', () => ({ listNoteChanges: vi.fn(), getNoteChange: vi.fn(), restoreNoteChange: vi.fn() }))
const change = { change_id: 'change', note_id: 'note', file_path: 'A.md', origin: 'agent:run:notes.update', before_hash: 'before', after_hash: 'after', created_at: '2026-09-30T01:00:00Z', applied_at: '2026-09-30T01:00:00Z' }
const detail = { ...change, can_restore: true, restore_reason: '' as const, before_metadata: null, after_metadata: null, diff: { lines: [{ kind: '-' as const, line: 1, text: 'before' }, { kind: '+' as const, line: 1, text: 'after' }], added_chars: 5, removed_chars: 6, before_chars: 6, after_chars: 5, truncated: false } }
beforeEach(() => {
  vi.clearAllMocks()
  state.workspace = reactive({ vaultId: 'one' })
  state.editor = reactive({ currentNoteId: 'note', saveStatus: 'saved', checkExternalFile: vi.fn() })
  vi.mocked(listNoteChanges).mockResolvedValue({ items: [change] })
  vi.mocked(getNoteChange).mockResolvedValue(detail)
  vi.mocked(restoreNoteChange).mockResolvedValue({} as never)
})
const setup = () => mount(NoteHistory, { props: { noteId: 'note' }, global: { stubs: { AppDialog: { template: '<div><slot /></div>' } } } })
const button = (wrapper: ReturnType<typeof mount>, text: string) => wrapper.findAll('button').find(item => item.text() === text)!
it('shows persisted source and diff, confirms restore, then exposes the new revision', async () => {
  const wrapper = setup(); await flushPromises()
  await wrapper.get('.history-entry').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('agent:run:notes.update')
  expect(wrapper.text()).toContain('before')
  await button(wrapper, '恢复到此次写入前').trigger('click')
  expect(restoreNoteChange).not.toHaveBeenCalled()
  vi.mocked(listNoteChanges).mockResolvedValue({ items: [{ ...change, change_id: 'undo', origin: 'restore:change' }, change] })
  await button(wrapper, '确认恢复').trigger('click'); await flushPromises()
  expect(restoreNoteChange).toHaveBeenCalledWith(detail)
  expect(state.editor.checkExternalFile).toHaveBeenCalledOnce()
  expect(wrapper.text()).toContain('用户恢复')
  expect(wrapper.text()).toContain('可打开该记录撤销本次恢复')
  wrapper.unmount()
})
it('blocks restore when edits become dirty after confirmation was opened', async () => {
  const wrapper = setup(); await flushPromises()
  await wrapper.get('.history-entry').trigger('click'); await flushPromises()
  await button(wrapper, '恢复到此次写入前').trigger('click')
  state.editor.saveStatus = 'dirty'; await flushPromises()
  expect(wrapper.text()).toContain('请先保存')
  expect(button(wrapper, '确认恢复')).toBeUndefined()
  expect(restoreNoteChange).not.toHaveBeenCalled()
  wrapper.unmount()
})
it('shows a conflict and never reloads the editor after a rejected restore', async () => {
  const wrapper = setup(); await flushPromises()
  await wrapper.get('.history-entry').trigger('click'); await flushPromises()
  await button(wrapper, '恢复到此次写入前').trigger('click')
  vi.mocked(restoreNoteChange).mockRejectedValueOnce(new Error('后续内容已变化'))
  await button(wrapper, '确认恢复').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('后续内容已变化')
  expect(state.editor.checkExternalFile).not.toHaveBeenCalled()
  wrapper.unmount()
})
