import type { CommunityCatalog, CommunitySource } from '@/contracts/community'

const prefix = 'community-cache-v2:'
const maxPages = 32
export const catalogPageSize = 30
export function normalizedCommunitySource(url: string): string {
  const base = new URL(url)
  if (base.username || base.password || base.search || base.hash || (base.protocol !== 'https:' && !(base.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(base.hostname)))) throw new Error('来源必须是 HTTPS；本机开发可用 HTTP。')
  return base.href
}

export function catalogIdentity(source: CommunitySource, q: string, kind: string, offset: number, limit: number): string {
  if ([...q].length > 120 || !Number.isSafeInteger(offset) || offset < 0 || offset > 2147483647 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new Error('目录查询或页码超出范围')
  const keys = source.keys.map(key => ({ namespace: key.namespace, key_id: key.key_id, public_key: key.public_key, revoked: key.revoked })).sort((a, b) => {
    const x = `${a.namespace}/${a.key_id}`, y = `${b.namespace}/${b.key_id}`
    return x < y ? -1 : x > y ? 1 : 0
  })
  return JSON.stringify({ source: source.id, sourceId: source.source_id ?? '', url: normalizedCommunitySource(source.url), keys, q, kind, offset, limit })
}

export function validateCatalog(value: unknown, offset: number, limit: number): CommunityCatalog {
  const page = value as Partial<CommunityCatalog> | null
  if (!page || page.schema_version !== 1 || !Array.isArray(page.items) || page.items.length > limit || !Number.isSafeInteger(page.total) || page.total! < 0 || page.offset !== offset || (page.limit !== undefined && page.limit !== limit)) throw new Error('不支持的社区目录协议')
  // Validate the public locator before the UI or install path can consume it.
  const ids = new Set<string>()
  for (const item of page.items) {
    if (!item || typeof item.release_id !== 'string' || !item.release_id || ids.has(item.release_id) || typeof item.namespace !== 'string' || typeof item.package_id !== 'string' || typeof item.version !== 'string' || typeof item.name !== 'string' || typeof item.description !== 'string' || typeof item.withdrawn !== 'boolean') throw new Error('社区目录包含无效发行')
    ids.add(item.release_id)
  }
  if (page.items.length > Math.max(0, page.total! - offset)) throw new Error('社区目录计数不一致')
  return { schema_version: 1, items: page.items, total: page.total!, offset, limit }
}

export interface CatalogCacheRecord {
  identity: string; etag: string | null; fetchedAt: string; checkedAt: string; data: CommunityCatalog
}
export function readCatalogCache(identity: string, offset: number, limit: number): CatalogCacheRecord | null {
  try {
    const record = JSON.parse(localStorage.getItem(prefix + encodeURIComponent(identity)) ?? 'null') as CatalogCacheRecord | null
    if (!record || record.identity !== identity || !Number.isFinite(Date.parse(record.fetchedAt)) || !Number.isFinite(Date.parse(record.checkedAt)) || (record.etag !== null && typeof record.etag !== 'string')) return null
    record.data = validateCatalog(record.data, offset, limit)
    return record
  } catch { return null }
}
export function writeCatalogCache(record: CatalogCacheRecord): void {
  // Storage failure must not discard a successfully verified live response.
  try {
    const key = prefix + encodeURIComponent(record.identity)
    const old = Object.keys(localStorage).filter(name => name.startsWith(prefix) && name !== key).map(name => {
      try { return { name, checkedAt: String(JSON.parse(localStorage.getItem(name) ?? '{}').checkedAt ?? '') } } catch { return { name, checkedAt: '' } }
    }).sort((a, b) => a.checkedAt.localeCompare(b.checkedAt))
    while (old.length >= maxPages) localStorage.removeItem(old.shift()!.name)
    try { localStorage.setItem(key, JSON.stringify(record)) }
    catch {
      // Recover space using only pages owned by this cache, never other settings.
      while (old.length) {
        localStorage.removeItem(old.shift()!.name)
        try { localStorage.setItem(key, JSON.stringify(record)); break } catch { /* next older page */ }
      }
    }
  } catch { /* private browsing or storage unavailable */ }
}
export function cachedPage(record: CatalogCacheRecord, revalidated = false): CommunityCatalog {
  return { ...record.data, cache: { fetchedAt: record.fetchedAt, checkedAt: record.checkedAt, revalidated } }
}
