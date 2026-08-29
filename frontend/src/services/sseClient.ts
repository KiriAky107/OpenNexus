import { resolveApiUrl } from './apiClient'

export type SseEventHandler = (event: string, data: Record<string, unknown>) => void

export interface SseClientOptions {
  url: string
  method?: string
  body?: unknown
  token?: string
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
    const { url, method = 'POST', body, token, onEvent, onError, onOpen, onDone } = this.options

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

      const resp = await fetch(resolveApiUrl(url), {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: this.controller.signal,
      })

      if (!resp.ok || !resp.body) {
        throw new Error(`SSE connection failed: ${resp.status}`)
      }

      this.reader = resp.body.getReader()
      this.connected = true
      onOpen?.()

      const decoder = new TextDecoder('utf-8')
      let eventName = 'message'
      let dataLines: string[] = []
      let doneNotified = false

      const dispatchEvent = () => {
        if (!dataLines.length) {
          eventName = 'message'
          return
        }
        try {
          const data = JSON.parse(dataLines.join('\n')) as Record<string, unknown>
          onEvent?.(eventName, data)
          if (!doneNotified && ['Done', 'RunCompleted', 'RunFailed', 'RunCancelled'].includes(eventName)) {
            doneNotified = true
            onDone?.()
          }
        } catch (error) {
          onError?.(error instanceof Error ? error : new Error('Malformed SSE data'))
        }
        eventName = 'message'
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

  isConnected() {
    return this.connected
  }
}

export default SseClient
