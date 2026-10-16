import { afterEach, expect, it, vi } from 'vitest'
import { canvasImagePath, createCanvasImages } from './canvasImages'
import type { NativeRequest } from './types'

afterEach(() => vi.restoreAllMocks())
async function tick() { for (let i = 0; i < 80; i++) await Promise.resolve() }
it('reads only legal Vault images, bounds visible previews, reuses originals and revokes URLs', async () => {
  const created = vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:owned-${Math.random()}`)
  const revoked = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  const rpc = vi.fn(async () => ({ content_base64: btoa('original bytes') }))
  const cache = createCanvasImages(rpc as NativeRequest)
  const paths = Array.from({ length: 35 }, (_, i) => `attachments/${i}.png`)
  cache.bind([...paths, 'https://unowned.example/image.png', '../private.png']); await tick()
  expect(rpc).toHaveBeenCalledTimes(32); expect(created).toHaveBeenCalledTimes(32)
  expect(Object.values(cache.items).filter(item => item.error === 'limit')).toHaveLength(3)
  cache.bind(paths); await tick(); expect(rpc).toHaveBeenCalledTimes(32)
  cache.dispose(); expect(revoked).toHaveBeenCalledTimes(32); expect(Object.keys(cache.items)).toHaveLength(0)
  expect(canvasImagePath('.ainote/secret.png')).toBeNull(); expect(canvasImagePath('attachments/中文.png')).toBe('attachments/中文.png')
})
it('keeps at most two native reads in flight even while a viewport repeatedly changes', async () => {
  const pending: ((value: { content_base64: string }) => void)[] = []
  let active = 0, maximum = 0
  const rpc = vi.fn(() => { active++; maximum = Math.max(maximum, active); return new Promise<{ content_base64: string }>(resolve => pending.push(resolve)).then(value => { active--; return value }) })
  const cache = createCanvasImages(rpc as NativeRequest)
  cache.bind(['first.png', 'second.png', 'third.png'])
  for (let i = 0; i < 10; i++) cache.bind([`other-${i}.png`, `last-${i}.png`])
  expect(rpc).toHaveBeenCalledTimes(2)
  pending[0]!({ content_base64: btoa('old original') }); await tick()
  expect(rpc).toHaveBeenCalledTimes(3); expect(maximum).toBe(2); expect(cache.items['first.png']).toBeUndefined()
  cache.dispose(); pending.slice(1).forEach(resolve => resolve({ content_base64: btoa('old original') })); await tick()
  expect(Object.keys(cache.items)).toHaveLength(0)
})
it('never turns a late native response into a Blob URL after a document was unmounted', async () => {
  const created = vi.spyOn(URL, 'createObjectURL')
  let complete!: (value: { content_base64: string }) => void
  const rpc = () => new Promise<{ content_base64: string }>(resolve => { complete = resolve })
  const cache = createCanvasImages(rpc as NativeRequest)
  cache.bind(['owned.png']); cache.dispose(); complete({ content_base64: btoa('late original') }); await tick()
  expect(created).not.toHaveBeenCalled(); expect(Object.keys(cache.items)).toHaveLength(0)
})
