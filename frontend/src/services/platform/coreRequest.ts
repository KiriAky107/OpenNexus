import { hostInvoke } from './desktop'

export interface RequestProgress { issued: boolean; requestId?: string }

/** A reservation makes cancel-before-dispatch definitive even across IPC ordering. */
export function coreRequest<T>(args: Record<string, unknown>, signal: AbortSignal, timeoutMs: number, progress: RequestProgress): Promise<T> {
  return new Promise((resolve, reject) => {
    let settled = false
    let requestId: string | undefined
    const cancel = () => {
      if (requestId) void hostInvoke('core_request_cancel', { requestId }).catch(() => {})
    }
    const abort = () => {
      if (settled) return
      settled = true
      signal.removeEventListener('abort', abort)
      cancel()
      reject(new DOMException('Request aborted', 'AbortError'))
    }
    if (signal.aborted) { abort(); return }
    signal.addEventListener('abort', abort, { once: true })
    void (async () => {
      try {
        requestId = await hostInvoke<string>('core_request_prepare', { timeoutMs })
        progress.requestId = requestId
        if (settled || signal.aborted) { cancel(); return }
        progress.issued = true
        const result = await hostInvoke<T>('core_request', { request: { ...args, requestId } })
        if (!settled) { settled = true; resolve(result) }
      } catch (error) {
        if (!settled) { settled = true; reject(error) }
      } finally {
        signal.removeEventListener('abort', abort)
        // Also discard a reservation if dispatch failed before Rust claimed it.
        cancel()
      }
    })()
  })
}
