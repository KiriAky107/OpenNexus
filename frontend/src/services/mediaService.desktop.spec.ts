// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest'

vi.mock('./platform/desktop', () => ({ isDesktop: () => true }))
vi.mock('./apiClient', () => ({
  apiClient: { postBinary: vi.fn(), get: vi.fn() },
  resolveApiUrl: (path: string) => path,
}))

import { apiClient } from './apiClient'
import { mediaService } from './mediaService'

it('uploads a large desktop recording in bounded chunks with a stable identity', async () => {
  const total = 64 * 1024 * 1024 + 1
  const chunkSize = 4 * 1024 * 1024
  const file = { name: 'lecture.wav', size: total, slice: vi.fn(() => new Blob(['chunk'])) } as unknown as File
  const post = vi.mocked(apiClient.postBinary)
  post.mockImplementation(async path => {
    const url = new URL(path, 'http://localhost')
    const offset = Number(url.searchParams.get('offset'))
    expect(url.searchParams.get('upload_id')).toBe('stable-upload-key')
    expect(url.searchParams.get('total')).toBe(String(total))
    const next_offset = Math.min(offset + chunkSize, total)
    return { attachment_id: 'media_complete', next_offset, complete: next_offset === total } as never
  })

  expect(await mediaService.upload(file, 'stable-upload-key')).toEqual({ attachment_id: 'media_complete' })
  expect(post).toHaveBeenCalledTimes(17)
  expect(file.slice).toHaveBeenLastCalledWith(64 * 1024 * 1024, total)
  expect(post.mock.calls.every(call => (call[1] as Blob).size <= chunkSize)).toBe(true)
})

it('requests large desktop playback in chunks rather than one bridge response', async () => {
  const get = vi.mocked(apiClient.get)
  get.mockImplementation(async path => {
    if (path.endsWith('/info')) return { size: 64 * 1024 * 1024 + 1, content_type: 'audio/wav' } as never
    expect(path).toContain('/chunks?offset=0&length=4194304')
    return { blob: async () => new Blob(['incomplete']) } as never
  })
  await expect(mediaService.downloadAudio('media_large.wav')).rejects.toThrow('音频分块读取不完整')
  expect(get).toHaveBeenCalledTimes(2)
})
