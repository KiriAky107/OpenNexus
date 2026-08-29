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

      const resp = await fetch(url, {
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

      while (true) {
        const { value, done } = await this.reader.read()
        if (done) break

        this.buffer += decoder.decode(value, { stream: true })

        const lines = this.buffer.split('\n')
        this.buffer = lines.pop() || ''

        let eventName = 'message'
        let dataStr = ''

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed) {
            if (dataStr) {
              try {
                const data = JSON.parse(dataStr)
                onEvent?.(eventName, data)
                if (eventName === 'Done' || eventName === 'RunCompleted' || eventName === 'RunFailed' || eventName === 'RunCancelled') {
                  onDone?.()
                }
              } catch {
                /* ignore malformed json */
              }
              eventName = 'message'
              dataStr = ''
            }
            continue
          }

          if (trimmed.startsWith('event:')) {
            eventName = trimmed.slice(6).trim()
          } else if (trimmed.startsWith('data:')) {
            const d = trimmed.slice(5).trim()
            dataStr += dataStr ? '\n' + d : d
          }
        }
      }
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
