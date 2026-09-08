import { beforeEach, expect, it, vi } from 'vitest'
const native = vi.hoisted(() => ({ invoke: vi.fn() }))
vi.mock('@tauri-apps/api/core', () => ({ invoke: native.invoke }))
import { confirmTrust, reviewTrust } from './extensionTrustService'
beforeEach(() => native.invoke.mockReset())
const key = { key_id: 'key', namespace: 'examples', public_key: btoa('a'.repeat(32)), revoked: false }
it('reviews normalized source and confirms only Host-issued id and fingerprint', async () => {
  native.invoke.mockResolvedValueOnce({ review_id: 'review', fingerprint: 'digest', previous: null })
  const reviews = await reviewTrust('https://catalog.example', 'catalog', [key], true)
  expect(native.invoke).toHaveBeenCalledWith('extension_trust_review', { setting: { source: 'https://catalog.example/', source_id: 'catalog', key_id: 'key', namespace: 'examples', public_key: Array(32).fill(97), enabled: true } })
  expect(native.invoke).toHaveBeenCalledTimes(1)
  await confirmTrust(reviews)
  expect(native.invoke).toHaveBeenLastCalledWith('extension_trust_confirm', { request: { review_id: 'review', fingerprint: 'digest' } })
})
it('rejects invalid and revoked input before invoking Host', async () => {
  await expect(reviewTrust('http://example.com', 'catalog', [key], true)).rejects.toThrow()
  await expect(reviewTrust('https://example.com', 'catalog', [{ ...key, revoked: true }], true)).rejects.toThrow()
  await expect(reviewTrust('https://example.com', 'catalog', [{ ...key, public_key: 'bad' }], true)).rejects.toThrow()
  await expect(confirmTrust([])).rejects.toThrow()
  expect(native.invoke).not.toHaveBeenCalled()
})
