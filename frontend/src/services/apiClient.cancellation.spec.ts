// @vitest-environment happy-dom
import { expect, it, vi, beforeEach, afterEach } from 'vitest'
const { hostInvoke } = vi.hoisted(() => ({ hostInvoke: vi.fn() }))
vi.mock('./platform/desktop', () => ({ isDesktop: () => true, hostInvoke }))
import apiClient from './apiClient'
beforeEach(() => {
  hostInvoke.mockReset()
  hostInvoke.mockImplementation(command => command === 'core_request_prepare' ? Promise.resolve('reservation') :
    command === 'core_request_cancel' ? Promise.resolve() : new Promise(() => {}))
})
afterEach(() => vi.useRealTimers())
it('never invokes Host for a pre-aborted mutation', async () => {
  const abort = new AbortController(); abort.abort()
  await expect(apiClient.post('/api/tasks', {}, { signal: abort.signal })).rejects.toMatchObject({ code: 'REQUEST_CANCELLED', details: { outcome: 'not_sent' } })
  expect(hostInvoke).not.toHaveBeenCalled()
})
it('rejects on deadline and cancels the native work rather than awaiting its response', async () => {
  vi.useFakeTimers()
  const result = expect(apiClient.post('/api/tasks', {}, { timeoutMs: 10 })).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT', details: { outcome: 'unknown' } })
  await vi.advanceTimersByTimeAsync(10); await result
  expect(hostInvoke).toHaveBeenCalledWith('core_request_cancel', { requestId: 'reservation' })
  expect(vi.getTimerCount()).toBe(0)
})
it('cancels a late reservation without dispatching after the caller has aborted', async () => {
  let reserve!: (id: string) => void
  hostInvoke.mockImplementationOnce(() => new Promise(resolve => { reserve = resolve }))
  const abort = new AbortController()
  const result = expect(apiClient.post('/api/tasks', {}, { signal: abort.signal })).rejects.toMatchObject({ code: 'REQUEST_CANCELLED' })
  abort.abort(); await result; reserve('late-reservation')
  await vi.waitFor(() => expect(hostInvoke).toHaveBeenCalledWith('core_request_cancel', { requestId: 'late-reservation' }))
  expect(hostInvoke.mock.calls.some(([command]) => command === 'core_request')).toBe(false)
})
it('propagates native deadline errors even when the browser timer has not fired', async () => {
  hostInvoke.mockImplementation(command => command === 'core_request_prepare' ? Promise.resolve('reservation') : command === 'core_request' ? Promise.reject({code:'REQUEST_TIMEOUT'}) : Promise.resolve())
  await expect(apiClient.get('/api/status')).rejects.toMatchObject({code:'REQUEST_TIMEOUT'})
})
it('dispatches the frozen request envelope and clears its deadline after success', async () => {
  vi.useFakeTimers()
  hostInvoke.mockImplementation(command => Promise.resolve(command === 'core_request_prepare' ? 'reservation' : {status:200,content_type:'application/json',body:'{"ok":true}'}))
  expect(await apiClient.post('/api/tasks', {title:'fixture'}, {token:'not-forwarded'})).toEqual({ok:true})
  expect(hostInvoke).toHaveBeenCalledWith('core_request', {request: expect.objectContaining({requestId:'reservation',method:'POST',path:'/api/tasks',body:{title:'fixture'}})})
  expect(JSON.stringify(hostInvoke.mock.calls)).not.toContain('not-forwarded')
  expect(vi.getTimerCount()).toBe(0)
})
it('retains binary bytes and media type through the cancellable transport', async () => {
  hostInvoke.mockImplementation(command => Promise.resolve(command === 'core_request_prepare' ? 'reservation' : {status:200,content_type:'application/json',body:'{}'}))
  await apiClient.postBinary('/api/packages',new Blob([new Uint8Array([0,255,128])]))
  expect(hostInvoke).toHaveBeenCalledWith('core_request', {request: expect.objectContaining({requestId:'reservation',bodyBase64:'AP+A',contentType:'application/zip'})})
})
