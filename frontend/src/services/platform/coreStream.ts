import { Channel } from '@tauri-apps/api/core'
import { hostInvoke } from './desktop'

type Message = { kind: 'headers'; status: number } | { kind: 'chunk'; data: string }
  | { kind: 'done' } | { kind: 'error'; code: string }

/** 本机会话凭证保留在 Rust 中；该通道仅承载响应字节。 */
export function coreStream(path: string, init: RequestInit): Promise<Response> {
  return new Promise((resolve, reject) => {
    const requestId = crypto.randomUUID()
    let ended = false
    let started = false
    let controller: ReadableStreamDefaultController<Uint8Array>
    const cancelHost = () => hostInvoke('core_stream_cancel', { requestId }).catch(() => {})
    const cleanup = () => init.signal?.removeEventListener('abort', abort)
    const fail = (error: Error) => {
      if (ended) return
      ended = true
      cleanup()
      controller.error(error)
      reject(error)
      if (started) void cancelHost()
    }
    const abort = () => fail(new DOMException('Request aborted', 'AbortError'))
    const stream = new ReadableStream<Uint8Array>({
      start(value) { controller = value },
      cancel() { ended = true; cleanup(); if (started) void cancelHost() },
    })
    const channel = new Channel<Message>()
    channel.onmessage = message => {
      if (ended) return
      if (message.kind === 'headers') resolve(new Response(stream, { status: message.status, headers: { 'Content-Type': 'text/event-stream' } }))
      if (message.kind === 'chunk') {
        const bytes = Uint8Array.from(atob(message.data), c => c.charCodeAt(0))
        controller.enqueue(bytes)
        // 如果消费者停止读取而不取消，则绑定排队数据。
        if ((controller.desiredSize ?? 0) < -4096) fail(new Error('CORE_STREAM_BACKPRESSURE'))
      }
      if (message.kind === 'error') fail(new Error(message.code))
      if (message.kind === 'done') { ended = true; cleanup(); controller.close() }
    }
    init.signal?.addEventListener('abort', abort, { once: true })
    if (init.signal?.aborted) { abort(); return }
    let body: unknown
    try { body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined }
    catch { fail(new Error('CORE_BODY_INVALID')); return }
    void hostInvoke('core_stream', {
      requestId, path, method: init.method ?? 'GET', body,
      lastEventId: new Headers(init.headers).get('Last-Event-ID') ?? undefined, channel,
    }).then(() => { started = true; if (ended) void cancelHost() }).catch(fail)
  })
}
