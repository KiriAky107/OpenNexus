import { resolveApiUrl } from './apiClient'
import { isDesktop } from './platform/desktop'
import { coreStream } from './platform/coreStream'

export type SseEventHandler = (
  event: string,
  data: Record<string, unknown>,
  eventId?: string,
) => void

export interface SseClientOptions {
  url: string
  method?: string
  body?: unknown
  token?: string
  lastEventId?: string
  onEvent?: SseEventHandler
  onError?: (error: Error) => void
  onOpen?: () => void
  onDone?: () => void
}

export class SseClient {
  private controller: AbortController
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null
  private options: SseClientOptions
  private buffer = ''
  private connected = false

  constructor(options: SseClientOptions) {
    this.options = options
    this.controller = new AbortController()
  }

  async connect() {
    const { url, method = 'POST', body, token, lastEventId, onEvent, onError, onOpen, onDone } = this.options

    try {
      const headers: Record<string, string> = {
        Accept: 'text/event-stream',
      }
      if (body !== undefined) {
        headers['Content-Type'] = 'application/json'
      }
      if (token) {
        headers['Authorization'] = `Bearer ${token}`
      }
      if (lastEventId !== undefined) {
        headers['Last-Event-ID'] = lastEventId
      }

      const init: RequestInit = {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: this.controller.signal,
      }
      const resp = isDesktop() ? await coreStream(url, init) : await fetch(resolveApiUrl(url), init)

      if (!resp.ok || !resp.body) {
        throw new Error(`SSE connection failed: ${resp.status}`)
      }

      this.reader = resp.body.getReader()
      this.connected = true
      onOpen?.()

      // 一个 UTF-8 字符或 SSE 行可能横跨多个网络分片，必须累积后再按空行派发。
      const decoder = new TextDecoder('utf-8')
      let eventName = 'message'
      let eventId: string | undefined
      let dataLines: string[] = []
      let doneNotified = false

      const dispatchEvent = () => {
        if (!dataLines.length) {
          eventName = 'message'
          eventId = undefined
          return
        }
        try {
          const data = JSON.parse(dataLines.join('\n')) as Record<string, unknown>
          onEvent?.(eventName, data, eventId)
          if (!doneNotified && ['Done', 'RunCompleted', 'RunFailed', 'RunCancelled'].includes(eventName)) {
            doneNotified = true
            onDone?.()
          }
        } catch (error) {
          onError?.(error instanceof Error ? error : new Error('Malformed SSE data'))
        }
        eventName = 'message'
        eventId = undefined
        dataLines = []
      }

      const consumeLine = (line: string) => {
        if (line === '') return dispatchEvent()
        if (line.startsWith(':')) return
        const separator = line.indexOf(':')
        const field = separator === -1 ? line : line.slice(0, separator)
        let fieldValue = separator === -1 ? '' : line.slice(separator + 1)
        if (fieldValue.startsWith(' ')) fieldValue = fieldValue.slice(1)
        if (field === 'event') eventName = fieldValue
        if (field === 'id') eventId = fieldValue
        if (field === 'data') dataLines.push(fieldValue)
      }

      while (true) {
        const { value, done } = await this.reader.read()
        if (done) break

        this.buffer += decoder.decode(value, { stream: true })

        const lines = this.buffer.split(/\r?\n/)
        this.buffer = lines.pop() || ''
        lines.forEach(consumeLine)
      }

      this.buffer += decoder.decode()
      if (this.buffer) consumeLine(this.buffer.replace(/\r$/, ''))
      dispatchEvent()
      if (!doneNotified) onDone?.()
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      onError?.(e as Error)
    } finally {
      this.connected = false
      this.reader = null
    }
  }

  cancel() {
    this.controller.abort()
  }

  // 传输层不自动重试 POST；Agent Store 使用 sequence 游标执行有界 GET 重连。

  isConnected() {
    return this.connected
  }
}

export default SseClient
