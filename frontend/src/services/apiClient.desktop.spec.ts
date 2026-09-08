// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from 'vitest'

const { hostInvoke } = vi.hoisted(() => ({ hostInvoke: vi.fn() }))
vi.mock('./platform/desktop', () => ({ isDesktop: () => true, hostInvoke }))

import apiClient from './apiClient'

beforeEach(() => {
  hostInvoke.mockReset()
  hostInvoke.mockResolvedValueOnce('test-reservation')
})

it('restores binary desktop responses as browser-compatible response objects', async () => {
  hostInvoke.mockResolvedValue({
    status: 200,
    content_type: 'application/pdf',
    body: '',
    body_base64: 'JVBERi0xLjc=',
  })

  const response = await apiClient.get<Response>('/api/exports/job/file')

  expect(response).toBeInstanceOf(Response)
  expect(response.headers.get('content-type')).toBe('application/pdf')
  expect([...new Uint8Array(await response.arrayBuffer())]).toEqual([37, 80, 68, 70, 45, 49, 46, 55])
})

it('keeps downloadable text responses wrapped in a response object', async () => {
  hostInvoke.mockResolvedValue({
    status: 200,
    content_type: 'text/html; charset=utf-8',
    body: '',
    body_base64: 'PGgxPuWvvOWHujwvaDE+',
  })

  const response = await apiClient.get<Response>('/api/exports/job/file')

  expect(response).toBeInstanceOf(Response)
  expect(await response.text()).toBe('<h1>导出</h1>')
})
