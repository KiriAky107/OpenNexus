// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'
const { invoke, channels } = vi.hoisted(() => ({ invoke: vi.fn(), channels: [] as { onmessage: (message: unknown) => void }[] }))
vi.mock('./desktop', () => ({ hostInvoke: invoke }))
vi.mock('@tauri-apps/api/core', () => ({ Channel: class { onmessage = (_message: unknown) => {}; constructor() { channels.push(this) } } }))
import { coreStream } from './coreStream'
beforeEach(() => { channels.length = 0; invoke.mockReset(); invoke.mockResolvedValue(undefined) })

it('preserves UTF-8 byte fragments and cursor without exposing authorization', async () => {
  const pending = coreStream('/api/events', { method: 'GET', headers: { 'Last-Event-ID': '42', Authorization: 'must-not-forward' } })
  channels[0]!.onmessage({ kind: 'headers', status: 200 })
  const response = await pending
  channels[0]!.onmessage({ kind: 'chunk', data: '5A==' })
  channels[0]!.onmessage({ kind: 'chunk', data: 'uK0=' })
  channels[0]!.onmessage({ kind: 'done' })
  expect(await response.text()).toBe('中')
  expect(invoke.mock.calls[0]![1]).toMatchObject({ lastEventId: '42' })
  expect(JSON.stringify(invoke.mock.calls)).not.toContain('must-not-forward')
})

it('cancels a native request even when abort arrives before start acknowledgement', async () => {
  let acknowledge!: () => void
  invoke.mockImplementationOnce(() => new Promise<void>(resolve => { acknowledge = resolve }))
  const abort = new AbortController()
  const pending = coreStream('/api/events', { signal: abort.signal })
  abort.abort()
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  acknowledge()
  await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith('core_stream_cancel', expect.anything()))
})

it('propagates native failure after headers to the response reader', async () => {
  const pending = coreStream('/api/events', {})
  channels[0]!.onmessage({ kind: 'headers', status: 200 })
  const response = await pending
  channels[0]!.onmessage({ kind: 'error', code: 'CORE_RESPONSE_ERROR' })
  await expect(response.text()).rejects.toThrow('CORE_RESPONSE_ERROR')
})
