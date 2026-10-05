// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ExperimentPanel from './ExperimentPanel.vue'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import type { ImportRecord, RunRecord, SelectedFile, Status } from '@/services/experimentService'
const host = vi.hoisted(() => ({ request: vi.fn(), files: vi.fn() }))
vi.mock('@/services/experimentService', async importOriginal => ({ ...await importOriginal<typeof import('@/services/experimentService')>(), __v_isRef: false, request: host.request, files: host.files }))
const selected: SelectedFile = { file_id: 'source', path: 'experiments/中文.py', hash: 'a'.repeat(64), revision: 1 }
const capability: Status = { available: true, runtime: { runtime_id: 'python-3.13.16-windows-x64', version: '3.13.16' }, limits: { wall_seconds: 60, cpu_seconds: 30, memory_mib: 256, processes: 4, disk_mib: 64, output_mib: 16, log_kib: 256, objects: 1024 }, error: null, cleanup: null }
const base: RunRecord = { summary: { request: { vault_id: 'vault-a', operation_id: 'run-a', runtime_id: capability.runtime!.runtime_id, entry: selected, inputs: [], limits: capability.limits }, fingerprint: 'fingerprint', sizes: { [selected.path]: 8 }, total_bytes: 8 }, state: 'awaiting_confirmation', created_ms: 100, updated_ms: 100, approval_id: null, approved_ms: null, error: null, result: null }
let wrapper: VueWrapper | undefined
let run: RunRecord
function page() { return { items: [{ operation_id: 'run-a', fingerprint: 'fingerprint', entry: selected, state: run.state, created_ms: 100 }], next_cursor: null } }
async function start() {
  wrapper = mount(ExperimentPanel)
  await flushPromises()
  return wrapper
}
beforeEach(() => {
  setActivePinia(createPinia()); localStorage.clear(); host.request.mockReset(); host.files.mockReset()
  useWorkspaceStore().vaultId = 'vault-a'; useWorkspaceStore().hasVault = true
  run = structuredClone(base)
  host.files.mockResolvedValue([selected])
  host.request.mockImplementation(async (_vault, action) => {
    switch (action.kind) {
      case 'status': return capability
      case 'history': return page()
      case 'record': case 'prepare': return run
      case 'source_preview': return { source: selected, bytes: 8, current_path: selected.path, preview: { text: 'print(1)', truncated: false, lines_shown: 1 } }
      case 'import_history': case 'all_import_history': return { items: [], next_cursor: null }
      case 'output_preview': return { manifest: run.result?.outputs?.status === 'collected' ? run.result.outputs.summary.files[0] : {}, content: { format: 'text', preview: { text: '<img src=x onerror=alert(1)>', truncated: true, lines_shown: 1 } } }
      default: throw new Error(`Unexpected action ${action.kind}`)
    }
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers(); vi.restoreAllMocks() })
describe('experiment user workflow', () => {
  it('reviews cleanup without deleting and asks the Host to confirm the exact reviewed scope', async () => {
    const pending = { ...capability, available: false, error: 'EXPERIMENT_CLEANUP_REQUIRED', cleanup: { operation_id: 'old-run', phase: 'created', error: null } }
    const review = { fingerprint: 'c'.repeat(64), operation_id: 'old-run', phase: 'created', profile_present: true, temporary_objects: 3, borrowed_objects: 2 }
    let cleaned = false
    const original = host.request.getMockImplementation()!
    host.request.mockImplementation(async (v,a) => {
      if (a.kind === 'status') return cleaned ? capability : pending
      if (a.kind === 'cleanup_review') return review
      if (a.kind === 'recover_cleanup') { cleaned = true; return { cleaned: true } }
      return original(v,a)
    })
    const view = await start()
    expect(view.get('[data-action="prepare-run"]').attributes('disabled')).toBeDefined()
    expect(view.find('[data-action="recover-cleanup"]').exists()).toBe(false)
    await view.get('[data-action="review-cleanup"]').trigger('click'); await flushPromises()
    expect(view.get('.cleanup-review').text()).toContain('临时文件和文件夹：3')
    expect(host.request.mock.calls.some(([,a]) => a.kind === 'recover_cleanup')).toBe(false)
    await view.get('[data-action="recover-cleanup"]').trigger('click'); await flushPromises()
    expect(host.request).toHaveBeenCalledWith('vault-a', { kind: 'recover_cleanup', fingerprint: review.fingerprint })
    expect(view.find('.cleanup-review').exists()).toBe(false)
    expect(view.get('[data-action="prepare-run"]').attributes('disabled')).toBeUndefined()
  })
  it('retains pending cleanup on native cancellation and requires a fresh review', async () => {
    const original = host.request.getMockImplementation()!
    host.request.mockImplementation(async (v,a) => {
      if (a.kind === 'status') return { ...capability, available: false, error: 'EXPERIMENT_CLEANUP_REQUIRED', cleanup: { operation_id: 'old-run', phase: 'created', error: null } }
      if (a.kind === 'cleanup_review') return { fingerprint: 'c'.repeat(64), temporary_objects: 3, borrowed_objects: 2 }
      if (a.kind === 'recover_cleanup') throw new Error('EXPERIMENT_USER_CANCELLED')
      return original(v,a)
    })
    const view = await start()
    await view.get('[data-action="review-cleanup"]').trigger('click'); await flushPromises()
    await view.get('[data-action="recover-cleanup"]').trigger('click'); await flushPromises()
    expect(view.get('[role="alert"]').text()).toContain('已取消操作')
    expect(view.find('.cleanup-review').exists()).toBe(true)
    expect(view.find('[data-action="recover-cleanup"]').exists()).toBe(false)
    expect(view.get('[data-action="prepare-run"]').attributes('disabled')).toBeDefined()
  })
  it('discards a late cleanup review after switching vaults', async () => {
    let settle!: (v: unknown) => void
    const original = host.request.getMockImplementation()!
    host.request.mockImplementation((v,a) => {
      if (a.kind === 'status' && v === 'vault-a') return Promise.resolve({ ...capability, available: false, error: 'EXPERIMENT_CLEANUP_REQUIRED', cleanup: { operation_id: 'old-run', phase: 'created', error: null } })
      if (a.kind === 'cleanup_review') return new Promise(r => { settle = r })
      return original(v,a)
    })
    const view = await start()
    await view.get('[data-action="review-cleanup"]').trigger('click')
    useWorkspaceStore().vaultId = 'vault-b'; await flushPromises()
    settle({ fingerprint: 'c'.repeat(64), temporary_objects: 3, borrowed_objects: 2 }); await flushPromises()
    expect(view.find('[data-action="recover-cleanup"]').exists()).toBe(false)
    expect(host.request.mock.calls.some(([,a]) => a.kind === 'recover_cleanup')).toBe(false)
  })
  it('shows a rejected native run decision without starting or reporting a completed experiment', async () => {
    const original = host.request.getMockImplementation()!
    host.request.mockImplementation((v,a) => a.kind === 'confirm_run' ? Promise.resolve({ ...run, state: 'rejected' }) : original(v,a))
    const view = await start()
    await view.get('.history-row').trigger('click'); await flushPromises()
    await view.get('[data-action="confirm-run"]').trigger('click'); await flushPromises()
    expect(view.get('.run-detail').text()).toContain('已拒绝')
    expect(view.find('[data-action="stop-run"]').exists()).toBe(false)
    expect(host.request.mock.calls.some(([,a]) => ['start_approved','import_prepare','import_next'].includes(a.kind))).toBe(false)
  })
  it('finds an approved import after run cleanup, starts only on Continue and preserves a cancellation over a late file receipt', async () => {
    const manifest = { path: 'report.md', bytes: 5, sha256: 'b'.repeat(64), kind: 'markdown' as const }
    const plan: ImportRecord = { plan: { request: { vault_id: 'vault-a', operation_id: 'import-a', run_id: 'run-a', selections: [{ output_path: 'report.md', destination: 'one.md' }, { output_path: 'second.md', destination: 'two.md' }] }, source: base.summary, execution: { outcome: 'completed', exit_code: 0, elapsed_ms: 2 }, items: [{ output: manifest, target: { file_id: 'first', path: 'one.md', hash: '', revision: 0 }, write_id: 'child1' }, { output: { ...manifest, path: 'second.md' }, target: { file_id: 'second', path: 'two.md', hash: '', revision: 0 }, write_id: 'child2' }] }, state: 'approved', fingerprint: 'import-fp', created_ms: 100, confirmed_ms: 101, atomic_scope: 'file', items: [{ state: 'pending', entry: null, error: null }, { state: 'pending', entry: null, error: null }] }
    const cancelled = structuredClone(plan); cancelled.state = 'cancelled'; cancelled.items = [{ state: 'committed', entry: { file_id: 'first', path: 'one.md', hash: manifest.sha256, revision: 1, deleted: false }, error: null }, { state: 'cancelled', entry: null, error: null }]
    let settle!: (value: ImportRecord) => void, isCancelled = false
    const original = host.request.getMockImplementation()!
    host.request.mockImplementation((v,a) => {
      if (a.kind === 'history') return Promise.resolve({ items: [], next_cursor: null })
      if (['all_import_history','import_history'].includes(a.kind)) return Promise.resolve({ items: [{ operation_id: 'import-a', run_id: 'run-a', entry: selected, state: isCancelled ? 'cancelled' : 'approved', files: 2, committed: isCancelled ? 1 : 0, created_ms: 100 }], next_cursor: null })
      if (a.kind === 'record') return Promise.reject(new Error('EXPERIMENT_RECORD_FORGOTTEN'))
      if (a.kind === 'import_record') return Promise.resolve(isCancelled ? cancelled : plan)
      if (a.kind === 'import_next') return new Promise(r => { settle = r })
      if (a.kind === 'cancel_import') { isCancelled = true; return Promise.resolve(cancelled) }
      return original(v,a)
    })
    const refresh = vi.spyOn(useWorkspaceStore(), 'refreshFileTree').mockResolvedValue(undefined)
    const view = await start()
    await view.get('.all-imports .history-row').trigger('click'); await flushPromises()
    expect(view.get('.import-plan').text()).toContain('运行记录已清理')
    expect(host.request.mock.calls.some(([,a]) => a.kind === 'import_next')).toBe(false)
    await view.findAll('button').find(b => b.text() === '继续未完成项')!.trigger('click'); await flushPromises()
    await view.findAll('button').find(b => b.text() === '取消导入')!.trigger('click'); await flushPromises()
    const late = structuredClone(plan); late.items[0] = cancelled.items[0]!
    settle(late); await flushPromises()
    expect(view.get('.import-plan').text()).toContain('已取消')
    expect(view.get('.import-plan').text()).toContain('已导入')
    expect(host.request.mock.calls.filter(([,a]) => a.kind === 'import_next')).toHaveLength(1)
    expect(host.request.mock.calls.some(([,a]) => a.kind === 'confirm_import')).toBe(false)
    expect(refresh).toHaveBeenCalledOnce()
  })
  it('prepares saved evidence without authorizing execution and reconciles an uncertain prepare with the same operation', async () => {
    let requests = 0
    host.request.mockImplementation(async (_vault, action) => {
      if (action.kind === 'status') return capability
      if (action.kind === 'history') return page()
      if (action.kind === 'prepare') { requests++; if (requests === 1) throw new Error('IPC_INTERRUPTED'); return run }
      if (action.kind === 'source_preview') return { source: selected, bytes: 8, current_path: selected.path, preview: { text: 'print(1)', truncated: false, lines_shown: 1 } }
      if (['import_history','all_import_history'].includes(action.kind)) return { items: [], next_cursor: null }
      throw new Error('Unexpected')
    })
    const view = await start()
    await view.get('[data-action="prepare-run"]').trigger('click'); await flushPromises()
    const original = host.request.mock.calls.find(([,a]) => a.kind === 'prepare')?.[1].request
    expect(view.text()).toContain('IPC_INTERRUPTED')
    await view.get('[data-action="prepare-run"]').trigger('click'); await flushPromises()
    expect(host.request.mock.calls.filter(([,a]) => a.kind === 'prepare').map(([,a]) => a.request)).toEqual([original, original])
    expect(view.find('[data-action="confirm-run"]').exists()).toBe(true)
    expect(host.request.mock.calls.some(([,a]) => ['confirm_run','start_approved','import_next'].includes(a.kind))).toBe(false)
  })
  it('discards a delayed run from the previous vault before showing it or advancing work', async () => {
    let resolve!: (value: RunRecord) => void
    const original = host.request.getMockImplementation()!
    host.request.mockImplementation((vault, action) => action.kind === 'record' ? new Promise(r => { resolve = r }) : original(vault, action))
    const view = await start()
    await view.get('.history-row').trigger('click')
    useWorkspaceStore().vaultId = 'vault-b'; await flushPromises()
    resolve(run); await flushPromises()
    expect(view.find('.run-detail').exists()).toBe(false)
    expect(host.request.mock.calls.filter(([,a]) => a.kind === 'source_preview')).toHaveLength(0)
  })
  it('keeps unsaved conflicting source untouched and never prepares or runs it', async () => {
    const editor = useEditorStore(); editor.currentFilePath = '/experiments/中文.py'; editor.content = 'private edits'; editor.saveStatus = 'conflict'
    const save = vi.spyOn(editor, 'save')
    const view = await start()
    await view.get('[data-action="prepare-run"]').trigger('click'); await flushPromises()
    expect(editor.content).toBe('private edits'); expect(save).not.toHaveBeenCalled()
    expect(host.request.mock.calls.some(([,a]) => a.kind === 'prepare')).toBe(false)
  })
  it('renders hostile output as text and requires an independent import confirmation', async () => {
    const stream = { text: '', bytes_seen: 0, retained_bytes: 0, truncated: false, invalid_utf8: false, complete: true, read_error: false }
    run.state = 'completed'; run.result = { outcome: 'completed', exit_code: 0, elapsed_ms: 2, error: null, user_cpu_ticks: 1, peak_memory_bytes: 2, final_disk_bytes: 3, logs: { stdout: stream, stderr: stream }, outputs: { status: 'collected', summary: { files: [{ path: 'report.md', bytes: 28, sha256: 'b'.repeat(64), kind: 'markdown' }], skipped: [], total_bytes: 28 } } }
    const manifest = { path: 'report.md', bytes: 28, sha256: 'b'.repeat(64), kind: 'markdown' as const }
    const plan: ImportRecord = { plan: { request: { vault_id: 'vault-a', operation_id: 'import-a', run_id: 'run-a', selections: [{ output_path: 'report.md', destination: '实验成果/run-a/report.md' }] }, source: run.summary, execution: { outcome: 'completed', exit_code: 0, elapsed_ms: 2 }, items: [{ output: manifest, target: { ...selected, path: '实验成果/run-a/report.md', hash: '', revision: 0 }, write_id: 'child' }] }, state: 'awaiting_confirmation', fingerprint: 'import-fp', created_ms: 100, confirmed_ms: null, atomic_scope: 'file', items: [{ state: 'pending', entry: null, error: null }] }
    const original = host.request.getMockImplementation()!
    host.request.mockImplementation((v,a) => a.kind === 'import_prepare' ? Promise.resolve(plan) : original(v,a))
    const view = await start()
    await view.get('.history-row').trigger('click'); await flushPromises()
    const preview = view.findAll('button').find(b => b.text() === '预览')!
    await preview.trigger('click'); await flushPromises()
    expect(view.get('.output-preview pre').text()).toContain('<img src=x onerror=alert(1)>')
    expect(view.find('.output-preview img').exists()).toBe(false)
    await view.get('.outputs input[type="checkbox"]').setValue(true)
    await view.get('[data-action="prepare-import"]').trigger('click'); await flushPromises()
    expect(view.find('[data-action="confirm-import"]').exists()).toBe(true)
    expect(host.request.mock.calls.some(([,a]) => ['confirm_import','import_next'].includes(a.kind))).toBe(false)
  })
})
