// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'
import { beforeEach, expect, it, vi } from 'vitest'
import ExperimentEvent from './ExperimentEvent.vue'
import { request, type RunRecord } from '@/services/experimentService'
const state = vi.hoisted(() => ({ workspace: null as unknown as { vaultId: string } }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => state.workspace }))
vi.mock('@/services/experimentService', async importOriginal => ({ ...await importOriginal<typeof import('@/services/experimentService')>(), request: vi.fn() }))
const source = { file_id: 'source-id', path: 'experiments/旧名.py', revision: 1, hash: 'a'.repeat(64) }
const data = { kind: 'experiment_run', state: 'running', operation_id: 'owned-run', vault_id: 'one', entry: source }
const button = (wrapper: ReturnType<typeof mount>, text: string) => wrapper.findAll('button').find(item => item.text().startsWith(text))!
beforeEach(() => { vi.mocked(request).mockReset(); state.workspace = reactive({ vaultId: 'one' }) })
it('opens the stable file ID at its current path after a rename', async () => {
  vi.mocked(request).mockResolvedValue('experiments/新名 #%.py')
  const wrapper = mount(ExperimentEvent, { props: { data } })
  await button(wrapper, '打开源文件').trigger('click'); await flushPromises()
  expect(request).toHaveBeenCalledWith('one', { kind: 'file_path', file_id: 'source-id' })
  expect(wrapper.emitted('open-file')).toEqual([[{ file_path: 'experiments/新名 #%.py', file_id: 'source-id', vault_id: 'one' }]])
  wrapper.unmount()
})
it('does not navigate when the vault changes during file lookup', async () => {
  let finish!: (path: string) => void
  vi.mocked(request).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const wrapper = mount(ExperimentEvent, { props: { data } })
  await button(wrapper, '打开源文件').trigger('click'); await flushPromises()
  state.workspace.vaultId = 'two'; await flushPromises(); finish('experiments/old.py'); await flushPromises()
  expect(wrapper.emitted('open-file')).toBeUndefined()
  expect(button(wrapper, '打开源文件').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})
it('shows partial imports and keeps successfully imported files accessible by ID', async () => {
  vi.mocked(request).mockResolvedValue('成果/改名.md')
  const wrapper = mount(ExperimentEvent, { props: { data: { kind: 'experiment_import', state: 'partial', operation_id: 'owned-import', vault_id: 'one', source_run_id: 'owned-run',
    items: [{ state: 'committed', entry: { ...source, path: '成果/report.md' }, error: null }, { state: 'failed', entry: null, error: 'EXPERIMENT_IMPORT_TARGET_CHANGED' }] } } })
  expect(wrapper.text()).toContain('部分成功')
  expect(wrapper.text()).toContain('已导入')
  await button(wrapper, '打开导入文件').trigger('click'); await flushPromises()
  expect(wrapper.emitted('open-file')?.[0]?.[0]).toMatchObject({ file_path: '成果/改名.md', file_id: 'source-id' })
  wrapper.unmount()
})
it('distinguishes display truncation from retained bytes and copies the actual retained log', async () => {
  const text = '<script>中文</script>\r\n' + '完整'.repeat(12000)
  const stream = { text: text.slice(0, 100), bytes_seen: 100000, retained_bytes: new TextEncoder().encode(text).length, truncated: true, display_truncated: true, invalid_utf8: false, complete: true, read_error: false }
  const result = { outcome: 'limited', exit_code: null, elapsed_ms: 120, error: 'EXPERIMENT_LOG_LIMIT', logs: { stdout: stream, stderr: { ...stream, text: '' } }, outputs: null }
  const record = { summary: { request: { vault_id: 'one', operation_id: 'owned-run' } }, result: { ...result, logs: { ...result.logs, stdout: { ...stream, text } } } } as RunRecord
  vi.mocked(request).mockResolvedValue(record)
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  const wrapper = mount(ExperimentEvent, { props: { data: { ...data, state: 'limited', result } } })
  expect(wrapper.text()).toContain('已超限停止')
  expect(wrapper.text()).toContain('仅展示前 16 KiB')
  expect(wrapper.text()).toContain('后续字节未保留')
  expect(wrapper.find('script').exists()).toBe(false)
  await button(wrapper, '复制已保留日志').trigger('click'); await flushPromises()
  expect(writeText).toHaveBeenCalledWith(text)
  expect(request).toHaveBeenCalledWith('one', { kind: 'record', operation_id: 'owned-run' })
  wrapper.unmount()
})
