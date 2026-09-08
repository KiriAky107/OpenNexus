import { afterEach, describe, expect, it, vi } from 'vitest'
import { webcrypto } from 'node:crypto'
import vector from './fixtures/community-python-vector.json'
import type { CommunityRelease, CommunitySource } from '@/contracts/community'
import { discoverKeys, fetchCatalog, verifyRelease } from './communityService'

afterEach(() => vi.unstubAllGlobals())
const release = vector.release as CommunityRelease
const bytes = Uint8Array.from(atob(vector.archive_base64), c => c.charCodeAt(0))

describe('社区 Python / TypeScript 签名契约', () => {
  it('校验 Python canonical JSON 与真实 Ed25519 签名', async () => {
    vi.stubGlobal('crypto', webcrypto)
    await expect(verifyRelease(release, vector.key, bytes)).resolves.toBeUndefined()
  })
  it('权限篡改、错误摘要和撤回均拒绝', async () => {
    vi.stubGlobal('crypto', webcrypto)
    await expect(verifyRelease({ ...release, permissions: ['network.request'] }, vector.key, bytes)).rejects.toThrow('签名')
    await expect(verifyRelease(release, vector.key, new Uint8Array([0]))).rejects.toThrow('摘要')
    await expect(verifyRelease({ ...release, withdrawn: true }, vector.key, bytes)).rejects.toThrow('撤回')
    await expect(verifyRelease(release, { ...vector.key, revoked: true }, bytes)).rejects.toThrow('撤回')
  })
  it('不携带凭据、拒绝重定向并支持取消', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ schema_version: 1, source_id: "fixture", keys: [] })))
    vi.stubGlobal('fetch', fetch)
    await discoverKeys('https://example.org')
    expect(fetch.mock.calls[0]?.[1]).toMatchObject({ credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer' })
    await expect(discoverKeys('file:///tmp/catalog')).rejects.toThrow('HTTPS')
  })
  it('停用来源不发请求', async () => {
    const source: CommunitySource = { id: 'fixture', url: 'https://example.org', enabled: false, keys: [] }
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    await expect(fetchCatalog(source)).rejects.toThrow('停用')
    expect(fetch).not.toHaveBeenCalled()
  })
})
