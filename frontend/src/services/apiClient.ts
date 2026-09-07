import type { ApiError, ErrorResponse } from '@/contracts'
import { hostInvoke, isDesktop } from './platform/desktop'

// 所有 HTTP 请求都经过此边界，以统一地址、请求追踪和错误契约。
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? import.meta.env.VITE_API_BASE ?? (isDesktop() ? 'http://127.0.0.1:8000' : '')

interface DesktopCoreResponse { status: number; content_type: string; body: string }

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
  const controller = timeoutMs ? new AbortController() : null
  let timedOut = false
  const abort = () => controller?.abort()
  if (rest.signal?.aborted) abort()
  rest.signal?.addEventListener('abort', abort, { once: true })
  const timer = timeoutMs ? setTimeout(() => { timedOut = true; controller?.abort() }, timeoutMs) : undefined

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
    if (isDesktop()) {
      const parsed = new URL(url)
      const response = await hostInvoke<DesktopCoreResponse>('core_request', {
        method: rest.method ?? 'GET',
        path: `${parsed.pathname}${parsed.search}`,
        body: typeof rest.body === 'string' ? JSON.parse(rest.body) : undefined,
        authorization: token ? `Bearer ${token}` : undefined,
      })
      if (response.status >= 200 && response.status < 300) {
        if (response.status === 204) return undefined as T
        return (response.content_type.includes('application/json') ? JSON.parse(response.body) : response.body) as T
      }
      let error: ErrorResponse | null = null
      try { error = JSON.parse(response.body) as ErrorResponse } catch { /* 非 JSON 错误 */ }
      throw new ApiErrorClass(error?.error?.code ?? `HTTP_${response.status}`, error?.error?.message ?? `Request failed with status ${response.status}`, error?.error?.details)
    }
    const resp = await fetch(url, {
      ...rest,
      signal: controller?.signal ?? rest.signal,
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
    if (timedOut) throw new ApiErrorClass('REQUEST_TIMEOUT', '请求超时，请检查后端状态后重试。')
    if (e instanceof ApiErrorClass) throw e
    throw new ApiErrorClass('NETWORK_ERROR', (e as Error).message || 'Network error')
  } finally {
    if (timer) clearTimeout(timer)
    rest.signal?.removeEventListener('abort', abort)
  }
}

export const apiClient = {
  postBinary<T>(path: string, body: Blob) {
    return request<T>(path, { method: 'POST', body, headers: { 'Content-Type': 'application/zip' } })
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
