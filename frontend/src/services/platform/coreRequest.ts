import { hostInvoke } from './desktop'

export interface RequestProgress { issued: boolean; requestId?: string }

/** 即使在 IPC 订购中，预订也可以确保发货前取消。 */
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
        // 如果在 Rust 声明保留之前调度失败，则也丢弃保留。
        cancel()
      }
    })()
  })
}
