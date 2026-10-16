import { reactive } from 'vue'
import { resolveCanvasFile } from '../../src/services/workspaceDocuments'
import { mobileRequest } from './native'
import type { NativeRequest } from './types'

interface Preview { url?: string; bytes: number; error?: 'unavailable' | 'limit' }
const budget = 12 * 1024 * 1024, maximum = 5 * 1024 * 1024
export function canvasImagePath(value: unknown): string | null {
  return typeof value === 'string' && /\.(png|jpe?g|gif|webp)$/i.test(value) ? resolveCanvasFile(value)?.slice(1) ?? null : null
}
/** Bounded visible-image cache. Paths stay native Vault paths; no web image requests. */
export function createCanvasImages(rpc: NativeRequest = mobileRequest) {
  const items = reactive<Record<string, Preview>>(Object.create(null))
  let disposed = false, signature = '', used = 0, running = 0
  let desired = new Set<string>()
  const inFlight = new Set<string>()
  function release(path: string) { const item = items[path]; if (item?.url) URL.revokeObjectURL(item.url); used -= item?.bytes ?? 0; delete items[path] }
  function bind(values: readonly string[]) {
    if (disposed) return
    const paths = [...new Set(values.map(canvasImagePath).filter((path): path is string => Boolean(path)))].sort()
    const key = paths.join('\n'); if (key === signature) return; signature = key
    desired = new Set(paths.slice(0, 32))
    for (const path of Object.keys(items)) if (!desired.has(path)) release(path)
    for (const path of paths.slice(32)) items[path] = { bytes: 0, error: 'limit' }
    pump()
  }
  function pump() {
    while (!disposed && running < 2) {
      const path = [...desired].find(path => !items[path] && !inFlight.has(path)); if (!path) return
      running++; inFlight.add(path)
      void (async () => {
        try {
          if (used >= budget) { items[path] = { bytes: 0, error: 'limit' }; return }
          const result = await rpc<{ content_base64: string }>({ kind: 'read_image', path })
          if (disposed || !desired.has(path)) return
          if (!result.content_base64 || result.content_base64.length > 4 * Math.ceil(maximum / 3)) throw Error('MOBILE_IMAGE_TOO_LARGE')
          const bytes = Uint8Array.from(atob(result.content_base64), character => character.charCodeAt(0))
          if (used + bytes.length > budget) { items[path] = { bytes: 0, error: 'limit' }; return }
          const extension = path.split('.').at(-1)!.toLowerCase(), mime = /jpe?g/.test(extension) ? 'image/jpeg' : `image/${extension}`
          const url = URL.createObjectURL(new Blob([bytes], { type: mime })); used += bytes.length; items[path] = { url, bytes: bytes.length }
        } catch { if (!disposed && desired.has(path)) items[path] = { bytes: 0, error: 'unavailable' } }
        finally { inFlight.delete(path); running--; pump() }
      })()
    }
  }
  function failed(path: string) { if (disposed || !desired.has(path)) return; release(path); items[path] = { bytes: 0, error: 'unavailable' } }
  function dispose() { disposed = true; desired.clear(); for (const path of Object.keys(items)) release(path) }
  return { items, bind, failed, dispose }
}
