import type { ApiError, ErrorResponse } from '@/contracts'
import { isDesktop } from './platform/desktop'
import { coreRequest, type RequestProgress } from './platform/coreRequest'

// 所有 HTTP 请求都经过此边界，以统一地址、请求追踪和错误契约。
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? import.meta.env.VITE_API_BASE ?? ''

interface DesktopCoreResponse {
  status: number
  content_type: string
  body: string
  body_base64?: string
}

function responseFromDesktopCore(response: DesktopCoreResponse): Response {
  let body: BodyInit = response.body
  if (response.body_base64 !== undefined) {
    const binary = atob(response.body_base64)
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
    body = bytes.buffer
  }
  return new Response(body, {
    status: response.status,
    headers: response.content_type ? { 'Content-Type': response.content_type } : undefined,
  })
}

export function resolveApiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  return `${BASE_URL.replace(/\/$/, '')}/${path.replace(/^\//, '')}`
}

interface RequestOptions extends RequestInit {
  timeoutMs?: number
  params?: Record<string, string | number | boolean | undefined>
  token?: string
}

export class ApiErrorClass extends Error {
  code: string
  details?: Record<string, unknown>

  constructor(code: string, message: string, details?: Record<string, unknown>) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.details = details
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { params, token, headers, timeoutMs, ...rest } = options
  const desktop = isDesktop()
  const deadlineMs = timeoutMs ?? (desktop ? 30_000 : undefined)
  if (deadlineMs !== undefined && (!Number.isFinite(deadlineMs) || deadlineMs <= 0 || (desktop && deadlineMs > 600_000))) {
    throw new ApiErrorClass('CORE_TIMEOUT_INVALID', '请求超时设置无效')
  }
  const controller = new AbortController()
  const progress: RequestProgress = { issued: false }
  let timedOut = false
  const abort = () => controller.abort()
  if (rest.signal?.aborted) abort()
  rest.signal?.addEventListener('abort', abort, { once: true })
  const timer = deadlineMs ? setTimeout(() => { timedOut = true; controller.abort() }, deadlineMs) : undefined

  let url = resolveApiUrl(path)

  if (params) {
    const usp = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) usp.append(k, String(v))
    })
    const qs = usp.toString()
    if (qs) url += `?${qs}`
  }

  const reqHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(headers as Record<string, string>),
  }

  if (token) {
    reqHeaders['Authorization'] = `Bearer ${token}`
  }

  const reqId = crypto.randomUUID()
  reqHeaders['X-Request-Id'] = reqId

  try {
    controller.signal.throwIfAborted()
    if (desktop) {
      const parsed = new URL(url, 'http://localhost')
      let bodyBase64: string | undefined
      if (rest.body instanceof Blob) {
        if (rest.body.size > 64 * 1024 * 1024) throw new ApiErrorClass('CORE_REQUEST_TOO_LARGE', '上传文件超过 64 MiB')
        const bytes = new Uint8Array(await rest.body.arrayBuffer())
        const parts: string[] = []
        for (let offset = 0; offset < bytes.length; offset += 16384) parts.push(String.fromCharCode(...bytes.subarray(offset, offset + 16384)))
        bodyBase64 = btoa(parts.join(''))
      }
      const response = await coreRequest<DesktopCoreResponse>({
        method: rest.method ?? 'GET',
        path: `${parsed.pathname}${parsed.search}`,
        body: typeof rest.body === 'string' ? JSON.parse(rest.body) : undefined,
        bodyBase64,
        contentType: new Headers(reqHeaders).get('Content-Type'),
        idempotencyKey: new Headers(reqHeaders).get('Idempotency-Key') ?? undefined,
      }, controller.signal, Math.ceil(deadlineMs!), progress)
      if (response.status >= 200 && response.status < 300) {
        if (response.status === 204) return undefined as T
        return (response.content_type.includes('application/json')
          ? JSON.parse(response.body)
          : responseFromDesktopCore(response)) as T
      }
      let error: ErrorResponse | null = null
      try { error = JSON.parse(response.body) as ErrorResponse } catch { /* 非 JSON 错误 */ }
      throw new ApiErrorClass(error?.error?.code ?? `HTTP_${response.status}`, error?.error?.message ?? `Request failed with status ${response.status}`, error?.error?.details)
    }
    const resp = await fetch(url, {
      ...rest,
      signal: controller.signal,
      headers: reqHeaders,
    })

    if (resp.ok) {
      if (resp.status === 204) return undefined as T
      const ct = resp.headers.get('content-type') || ''
      if (ct.includes('application/json')) return (await resp.json()) as T
      return resp as unknown as T
    }

    // 后端约定返回 ErrorResponse；代理或网关的非 JSON 错误仍降级为 HTTP 状态码。
    let errBody: ErrorResponse | null = null
    try {
      errBody = (await resp.json()) as ErrorResponse
    } catch {
      /* ignore */
    }

    const code = errBody?.error?.code || `HTTP_${resp.status}`
    const message = errBody?.error?.message || `Request failed with status ${resp.status}`
    const details = errBody?.error?.details

    throw new ApiErrorClass(code, message, details)
  } catch (e) {
    const nativeCode = (e as { code?: string })?.code
    const uncertain = desktop && progress.issued && !['GET', 'HEAD'].includes(rest.method ?? 'GET')
    const details = desktop ? { request_id: progress.requestId, outcome: progress.issued ? 'unknown' : 'not_sent' } : undefined
    if (timedOut || nativeCode === 'REQUEST_TIMEOUT') throw new ApiErrorClass('REQUEST_TIMEOUT', uncertain ? '请求超时，变更可能已提交，请先检查结果。' : '请求超时，请检查连接。', details)
    if (controller.signal.aborted || nativeCode === 'REQUEST_CANCELLED') throw new ApiErrorClass('REQUEST_CANCELLED', uncertain ? '请求已取消，变更可能已提交，请先检查结果。' : '请求已取消。', details)
    if (e instanceof ApiErrorClass) throw e
    throw new ApiErrorClass('NETWORK_ERROR', (e as Error).message || 'Network error')
  } finally {
    if (timer) clearTimeout(timer)
    rest.signal?.removeEventListener('abort', abort)
  }
}

export const apiClient = {
  postBinary<T>(path: string, body: Blob, headers: Record<string, string> = { 'Content-Type': 'application/zip' }) {
    return request<T>(path, { method: 'POST', body, headers })
  },
  get<T>(path: string, options?: Omit<RequestOptions, 'method'>) {
    return request<T>(path, { ...options, method: 'GET' })
  },
  post<T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) {
    return request<T>(path, {
      ...options,
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  },
  patch<T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) {
    return request<T>(path, {
      ...options,
      method: 'PATCH',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  },
  put<T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) {
    return request<T>(path, {
      ...options,
      method: 'PUT',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  },
  delete<T>(path: string, options?: Omit<RequestOptions, 'method'>) {
    return request<T>(path, { ...options, method: 'DELETE' })
  },
}

export default apiClient
