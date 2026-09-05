import type { ApiError, ErrorResponse } from '@/contracts'

// 所有 HTTP 请求都经过此边界，以统一地址、请求追踪和错误契约。
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? import.meta.env.VITE_API_BASE ?? ''

export function resolveApiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  return `${BASE_URL.replace(/\/$/, '')}/${path.replace(/^\//, '')}`
}

interface RequestOptions extends RequestInit {
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
  const { params, token, headers, ...rest } = options

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
    const resp = await fetch(url, {
      ...rest,
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
    if (e instanceof ApiErrorClass) throw e
    throw new ApiErrorClass('NETWORK_ERROR', (e as Error).message || 'Network error')
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
