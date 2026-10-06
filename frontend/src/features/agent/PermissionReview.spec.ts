// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'
import { beforeEach, expect, it, vi } from 'vitest'
import PermissionReview from './PermissionReview.vue'
import { getPermissionPreview, respondToPermission } from '@/services/agentService'
import { request, type AgentExperimentReview, type NativeAgentReview } from '@/services/experimentService'
const state = vi.hoisted(() => ({ workspace: null as unknown as { vaultId: string } }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => state.workspace }))
vi.mock('@/services/agentService', () => ({ getPermissionPreview: vi.fn(), respondToPermission: vi.fn() }))
vi.mock('@/services/experimentService', async importOriginal => ({ ...await importOriginal<typeof import('@/services/experimentService')>(), request: vi.fn() }))
const preview = { token: 'revision-one', file_path: 'lesson.md', note_id: 'id', operation: 'replace' as const, metadata: { title: null, tags: ['keep'] }, diff: { lines: [{ kind: '+' as const, line: 1, text: '<script>text</script>' }], added_chars: 10, removed_chars: 0, before_chars: 0, after_chars: 10, truncated: false } }
const props = { runId: 'run', requestId: 'request', call: { name: 'notes.update', arguments: { markdown: 'proposed' } } }
beforeEach(() => {
  vi.clearAllMocks(); state.workspace = reactive({ vaultId: 'one' })
  vi.mocked(request).mockReset()
  vi.mocked(getPermissionPreview).mockResolvedValue(preview)
  vi.mocked(respondToPermission).mockResolvedValue({} as never)
})
const button = (wrapper: ReturnType<typeof mount>, text: string) => wrapper.findAll('button').find(item => item.text() === text)!
it('requires loaded preview and sends its token, rendering diff as escaped text', async () => {
  const wrapper = mount(PermissionReview, { props })
  expect(button(wrapper, '允许本次').attributes('disabled')).toBeDefined()
  await flushPromises()
  expect(wrapper.find('script').exists()).toBe(false)
  expect(wrapper.text()).toContain('<script>text</script>')
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  expect(respondToPermission).toHaveBeenCalledWith('run', 'request', 'allow_once', 'revision-one')
  expect(wrapper.emitted('resolved')).toHaveLength(1)
  wrapper.unmount()
})
it('invalidates rejected previews and requires an explicit new preview', async () => {
  const wrapper = mount(PermissionReview, { props }); await flushPromises()
  vi.mocked(respondToPermission).mockRejectedValueOnce(new Error('笔记已变化'))
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('笔记已变化')
  expect(button(wrapper, '允许本次').attributes('disabled')).toBeDefined()
  vi.mocked(getPermissionPreview).mockResolvedValue({ ...preview, token: 'revision-two' })
  await button(wrapper, '重新预览').trigger('click'); await flushPromises()
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  expect(respondToPermission).toHaveBeenLastCalledWith('run', 'request', 'allow_once', 'revision-two')
  wrapper.unmount()
})
it('allows denial when a preview cannot be produced', async () => {
  vi.mocked(getPermissionPreview).mockRejectedValueOnce(new Error('目标不存在'))
  const wrapper = mount(PermissionReview, { props }); await flushPromises()
  await button(wrapper, '拒绝').trigger('click'); await flushPromises()
  expect(respondToPermission).toHaveBeenCalledWith('run', 'request', 'deny')
  wrapper.unmount()
})
it('discards a preview fetched under the previous vault', async () => {
  let resolve!: (value: typeof preview) => void
  vi.mocked(getPermissionPreview).mockImplementationOnce(() => new Promise(done => { resolve = done }))
  const wrapper = mount(PermissionReview, { props })
  vi.mocked(getPermissionPreview).mockRejectedValueOnce(new Error('不可访问'))
  state.workspace.vaultId = 'two'; await flushPromises()
  resolve(preview); await flushPromises()
  expect(wrapper.text()).not.toContain('lesson.md')
  expect(button(wrapper, '允许本次').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})
it('reviews exact source-file bytes and offers only a one-time write decision', async () => {
  vi.mocked(getPermissionPreview).mockResolvedValue({ ...preview, kind: 'experiment_file',
    file_path: 'experiments/课程 #%.py', overwrite: true, before_bytes: 18, after_bytes: 24 })
  const wrapper = mount(PermissionReview, { props: { ...props, allowSession: true,
    call: { name: 'experiments.files.write', arguments: { content: 'print(2)' } } } })
  expect(button(wrapper, '允许本次').attributes('disabled')).toBeDefined()
  await flushPromises()
  expect(wrapper.text()).toContain('替换实验文件')
  expect(wrapper.text()).toContain('18 → 24 B')
  expect(wrapper.text()).toContain('运行与成果导入需要另行确认')
  expect(wrapper.findAll('button').some(item => item.text() === '本次会话允许')).toBe(false)
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  expect(respondToPermission).toHaveBeenCalledWith('run', 'request', 'allow_once', 'revision-one')
  wrapper.unmount()
})
it.each(['experiments.run', 'experiments.import'])('does not offer session approval for %s', async name => {
  const wrapper = mount(PermissionReview, { props: { ...props, allowSession: true, call: { name } } })
  expect(wrapper.findAll('button').some(item => item.text() === '本次会话允许')).toBe(false)
  wrapper.unmount()
})

function runPreview(): AgentExperimentReview & { kind: 'experiment_run' } {
  return { kind: 'experiment_run', token: 'bound-native-review', vault_id: 'one', operation_id: 'owned-run', fingerprint: 'a'.repeat(64),
    context: { agent_run_id: 'run', tool_call_id: 'call_digest', request_id: 'request' },
    record: { state: 'awaiting_confirmation', approval_id: null, approved_ms: null, created_ms: 1, updated_ms: 1, error: null, result: null,
      summary: { fingerprint: 'a'.repeat(64), sizes: { 'experiments/课程 #%.py': 20 }, total_bytes: 20,
        request: { vault_id: 'one', operation_id: 'owned-run', runtime_id: 'bundled-python-3.13.12',
          entry: { file_id: 'source-id', path: 'experiments/课程 #%.py', hash: 'b'.repeat(64), revision: 3 }, inputs: [],
          limits: { wall_seconds: 60, cpu_seconds: 30, memory_mib: 256, processes: 4, disk_mib: 64, output_mib: 16, log_kib: 256, objects: 1024 } } } } }
}
const actionProps = { ...props, allowSession: true, call: { name: 'experiments.run', tool_call_id: 'provider/id' } }
function approved(value: AgentExperimentReview): NativeAgentReview {
  return { ...value, record: { ...value.record, state: 'approved' } } as NativeAgentReview
}
it('requires native consent before sending the one-time Core token', async () => {
  const value = runPreview()
  vi.mocked(getPermissionPreview).mockResolvedValue(value)
  let finish!: (value: NativeAgentReview) => void
  vi.mocked(request).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const wrapper = mount(PermissionReview, { props: actionProps }); await flushPromises()
  expect(wrapper.text()).toContain('网络关闭')
  expect(wrapper.text()).toContain('60 s')
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  expect(request).toHaveBeenCalledWith('one', { kind: 'confirm_agent_run', operation_id: 'owned-run', fingerprint: value.fingerprint })
  expect(respondToPermission).not.toHaveBeenCalled()
  finish(approved(value)); await flushPromises()
  expect(respondToPermission).toHaveBeenCalledWith('run', 'request', 'allow_once', value.token)
  wrapper.unmount()
})
it.each(['vault', 'ticket'])('discards a native response after the %s changes', async change => {
  const value = runPreview()
  vi.mocked(getPermissionPreview).mockResolvedValue(value)
  let finish!: (value: NativeAgentReview) => void
  vi.mocked(request).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const wrapper = mount(PermissionReview, { props: actionProps }); await flushPromises()
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  vi.mocked(getPermissionPreview).mockRejectedValueOnce(new Error('新请求不可用'))
  if (change === 'vault') state.workspace.vaultId = 'two'
  else await wrapper.setProps({ requestId: 'next-ticket' })
  await flushPromises(); finish(approved(value)); await flushPromises()
  expect(respondToPermission).not.toHaveBeenCalled()
  wrapper.unmount()
})
it('native rejection denies the Core ticket and cannot start the run', async () => {
  const value = runPreview()
  vi.mocked(getPermissionPreview).mockResolvedValue(value)
  vi.mocked(request).mockResolvedValue({ ...value, record: { ...value.record, state: 'rejected' } })
  const wrapper = mount(PermissionReview, { props: actionProps }); await flushPromises()
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  expect(respondToPermission).toHaveBeenCalledWith('run', 'request', 'deny')
  wrapper.unmount()
})
it('rejects native consent belonging to another operation', async () => {
  const value = runPreview()
  vi.mocked(getPermissionPreview).mockResolvedValue(value)
  vi.mocked(request).mockResolvedValue({ ...approved(value), operation_id: 'another-run' })
  const wrapper = mount(PermissionReview, { props: actionProps }); await flushPromises()
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  expect(respondToPermission).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('原生确认与当前请求不匹配')
  expect(button(wrapper, '允许本次').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})
function importPreview(): AgentExperimentReview & { kind: 'experiment_import' } {
  const run = runPreview()
  return { ...run, kind: 'experiment_import', operation_id: 'owned-import', record: { state: 'awaiting_confirmation', fingerprint: run.fingerprint,
    atomic_scope: 'file', created_ms: 1, confirmed_ms: null, items: [{ state: 'pending', entry: null, error: null }],
    plan: { request: { vault_id: 'one', operation_id: 'owned-import', run_id: 'owned-run', selections: [{ output_path: 'report.md', destination: '成果/report.md' }] },
      source: run.record.summary, execution: { outcome: 'completed', exit_code: 0, elapsed_ms: 12 }, items: [{ write_id: 'write-once',
        output: { path: 'report.md', kind: 'markdown', bytes: 20, sha256: 'c'.repeat(64) },
        target: { file_id: 'destination', path: '成果/report.md', revision: 2, hash: 'd'.repeat(64) } }] } } }
}
it('loads the actual import replacement before opening separate native import confirmation', async () => {
  const value = importPreview(), after = '<script>新</script>\r\n'
  const bytes = new TextEncoder().encode(after); value.record.plan.items[0]!.output.bytes = bytes.length
  vi.mocked(getPermissionPreview).mockResolvedValue(value)
  vi.mocked(request).mockImplementation(async (_vault, action) => {
    if (action.kind === 'import_target_preview') return { target: value.record.plan.items[0]!.target, bytes: 12, preview: { text: '<img>旧\r\n', truncated: true, lines_shown: 1 } }
    if (action.kind === 'output_read') return { manifest: value.record.plan.items[0]!.output, offset: 0, next_offset: null, content_base64: btoa(String.fromCharCode(...bytes)) }
    if (action.kind === 'confirm_agent_import') return approved(value)
    throw new Error('Unexpected request')
  })
  const wrapper = mount(PermissionReview, { props: { ...actionProps, call: { name: 'experiments.import' } } })
  expect(button(wrapper, '允许本次').attributes('disabled')).toBeDefined()
  await flushPromises()
  expect(wrapper.text()).toContain('覆盖文件')
  expect(wrapper.text()).toContain('<script>新</script>')
  expect(wrapper.find('script').exists()).toBe(false)
  expect(wrapper.text()).toContain('实际替换整个文件')
  expect(request).toHaveBeenCalledWith('one', expect.objectContaining({ kind: 'output_read', limit: 32768 }))
  await button(wrapper, '允许本次').trigger('click'); await flushPromises()
  expect(request).toHaveBeenLastCalledWith('one', { kind: 'confirm_agent_import', operation_id: 'owned-import', fingerprint: value.fingerprint })
  expect(respondToPermission).toHaveBeenCalledWith('run', 'request', 'allow_once', value.token)
  wrapper.unmount()
})
it('a stale replacement preview keeps import confirmation disabled while denial remains available', async () => {
  vi.mocked(getPermissionPreview).mockResolvedValue(importPreview())
  vi.mocked(request).mockRejectedValue(new Error('EXPERIMENT_IMPORT_TARGET_CHANGED'))
  const wrapper = mount(PermissionReview, { props: { ...actionProps, call: { name: 'experiments.import' } } }); await flushPromises()
  expect(button(wrapper, '允许本次').attributes('disabled')).toBeDefined()
  expect(wrapper.text()).toContain('目标文件已变化')
  await button(wrapper, '拒绝').trigger('click'); await flushPromises()
  expect(respondToPermission).toHaveBeenCalledWith('run', 'request', 'deny')
  wrapper.unmount()
})
