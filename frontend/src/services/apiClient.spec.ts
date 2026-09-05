// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import apiClient from './apiClient'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('aborts a stuck request with an actionable timeout', async () => {
  vi.useFakeTimers()
  let signal: AbortSignal | undefined
  vi.stubGlobal('fetch', vi.fn((_url, options) => new Promise((_resolve, reject) => {
    signal = options.signal
    signal?.addEventListener('abort', () => reject(new Error('aborted')))
  })))
  const assertion = expect(apiClient.get('/api/workspace', { timeoutMs: 15000 })).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT' })
  await vi.advanceTimersByTimeAsync(15000)
  await assertion
  expect(signal?.aborted).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
})

it('clears the deadline after success', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"ok":true}', { headers: { 'Content-Type': 'application/json' } })))
  expect(await apiClient.get('/health', { timeoutMs: 10000 })).toEqual({ ok: true })
  expect(vi.getTimerCount()).toBe(0)
})
