import { beforeEach, describe, expect, it, vi } from 'vitest'
const native = vi.hoisted(() => ({ enabled: true, invoke: vi.fn() }))
vi.mock('@tauri-apps/api/core', () => ({ isTauri: () => native.enabled, invoke: native.invoke }))
import { defaultDestination, originalBytes, request, runRequest, type SelectedFile } from './experimentService'
beforeEach(() => { native.enabled = true; native.invoke.mockReset() })
describe('experiment Host client', () => {
  it('keeps the original vault binding and never fabricates web execution', async () => {
    native.invoke.mockResolvedValue({ state: 'approved' })
    await request('original', { kind: 'confirm_run', operation_id: 'run', fingerprint: 'hash' })
    expect(native.invoke).toHaveBeenCalledWith('experiment_request', { request: { vault_id: 'original', action: { kind: 'confirm_run', operation_id: 'run', fingerprint: 'hash' } } })
    native.enabled = false
    await expect(request('original', { kind: 'status' })).rejects.toThrow('DESKTOP_UNAVAILABLE')
    expect(native.invoke).toHaveBeenCalledOnce()
  })
  it('downloads original byte chunks across UTF-8 boundaries without normalizing CRLF', async () => {
    const bytes = new TextEncoder().encode('中\r\n文'), first = bytes.slice(0, 2), second = bytes.slice(2)
    const encoded = (value: Uint8Array) => btoa(String.fromCharCode(...value))
    native.invoke.mockResolvedValueOnce({ bytes: bytes.length, offset: 0, next_offset: 2, content_base64: encoded(first) }).mockResolvedValueOnce({ bytes: bytes.length, offset: 2, next_offset: null, content_base64: encoded(second) })
    expect(await originalBytes('vault', 'run', 'experiments/中文.csv', true)).toEqual(bytes)
    expect(native.invoke.mock.calls[1]?.[1].request).toMatchObject({ vault_id: 'vault', action: { offset: 2, limit: 262144 } })
  })
  it('rejects repeated offsets and short terminal receipts instead of looping or silently truncating', async () => {
    native.invoke.mockResolvedValue({ manifest: { bytes: 5 }, offset: 0, next_offset: 0, content_base64: btoa('a') })
    await expect(originalBytes('vault', 'run', 'report.md', false)).rejects.toThrow('EXPERIMENT_RESPONSE_INVALID')
    native.invoke.mockResolvedValue({ manifest: { bytes: 5 }, offset: 0, next_offset: null, content_base64: btoa('a') })
    await expect(originalBytes('vault', 'run', 'report.md', false)).rejects.toThrow('EXPERIMENT_RESPONSE_INVALID')
  })
  it('copies the selected identity and budgets, using visible supported destinations', () => {
    const files: SelectedFile[] = [{ file_id: 'source', path: 'experiments/源.py', hash: 'a', revision: 4 }]
    const limits = { wall_seconds: 60, cpu_seconds: 30, memory_mib: 256, processes: 4, disk_mib: 64, output_mib: 16, log_kib: 256, objects: 1024 }
    const prepared = runRequest('v', '/experiments/源.py', [], files, 'runtime', limits)
    limits.wall_seconds = 1; files[0]!.revision = 5
    expect(prepared.entry.revision).toBe(4); expect(prepared.limits.wall_seconds).toBe(60)
    expect(defaultDestination({ path: 'a/report.txt', kind: 'text', bytes: 1, sha256: 'h' }, '12345678-x')).toBe('实验成果/12345678/report.md')
    expect(defaultDestination({ path: 'a/report.json', kind: 'json', bytes: 1, sha256: 'h' }, '12345678-x')).toBe('experiments/results/12345678/report.json')
  })
})
