/** 只请求用户配置来源；公钥固定、签名及摘要检查先于任何安装 API。 */
import type { CommunityCatalog, CommunityRelease, CommunitySource, CommunityKey } from '@/contracts/community'
import { valid, gt, lt } from 'semver'
import appPackage from '../../package.json'
import { decodeThemePackage, inspectThemePackage, installTheme } from './themePackageService'
import { installSkill } from './skillService'
import { installPlugin } from './pluginService'

const sourceStorage = 'community-sources-v1'
export function loadSources(): CommunitySource[] {
  try { return JSON.parse(localStorage.getItem(sourceStorage) ?? '[]') as CommunitySource[] } catch { return [] }
}
export function saveSources(sources: CommunitySource[]) { localStorage.setItem(sourceStorage, JSON.stringify(sources)) }

function sourceUrl(source: CommunitySource, path: string) {
  const base = new URL(source.url)
  if (base.username || base.password || base.search || base.hash || (base.protocol !== 'https:' && !(base.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(base.hostname)))) throw new Error('来源必须是 HTTPS；本机开发可用 HTTP。')
  const url = new URL(path, base)
  if (url.origin !== base.origin || !url.pathname.startsWith('/catalog/v1/')) throw new Error('发行地址不属于已固定的社区来源')
  return url
}

async function download(source: CommunitySource, path: string, maxSize: number, signal?: AbortSignal): Promise<Uint8Array> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) abort()
  const timeout = setTimeout(abort, 30000)
  try {
    const response = await fetch(sourceUrl(source, path), { credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal })
    if (!response.ok || !response.body) throw new Error(`社区请求失败 (${response.status})`)
    const reader = response.body.getReader(), chunks: Uint8Array[] = []
    let size = 0
    try {
      while (true) {
        const part = await reader.read()
        if (part.done) break
        size += part.value.length
        if (size > maxSize) throw new Error('社区响应超过大小限制')
        chunks.push(part.value)
      }
    } finally { await reader.cancel().catch(() => undefined) }
    const bytes = new Uint8Array(size)
    let offset = 0
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
    return bytes
  } finally { clearTimeout(timeout); signal?.removeEventListener('abort', abort) }
}

export async function discoverKeys(url: string, signal?: AbortSignal): Promise<CommunityKey[]> {
  const source: CommunitySource = { id: 'candidate', url, enabled: true, keys: [] }
  const value = JSON.parse(new TextDecoder().decode(await download(source, '/catalog/v1/sources', 1024 * 1024, signal)))
  if (value.schema_version !== 1 || !Array.isArray(value.keys)) throw new Error('不支持的社区来源协议')
  return value.keys
}

export async function fetchCatalog(source: CommunitySource, q = '', kind = '', signal?: AbortSignal): Promise<CommunityCatalog> {
  if (!source.enabled) throw new Error('来源已停用')
  const value = JSON.parse(new TextDecoder().decode(await download(source, `/catalog/v1/packages?q=${encodeURIComponent(q)}${kind ? `&type=${encodeURIComponent(kind)}` : ''}&limit=100`, 2 * 1024 * 1024, signal)))
  if (value.schema_version !== 1 || !Array.isArray(value.items)) throw new Error('不支持的社区目录协议')
  // 缓存只用于离线浏览；安装仍会重新拉取发行与撤回状态。
  localStorage.setItem(`community-cache:${source.id}`, JSON.stringify(value))
  return value
}
export function cachedCatalog(source: CommunitySource): CommunityCatalog | null {
  try { return JSON.parse(localStorage.getItem(`community-cache:${source.id}`) ?? 'null') } catch { return null }
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`).join(',')}}`
  return JSON.stringify(value)
}
const bytes64 = (value: string) => Uint8Array.from(atob(value), char => char.charCodeAt(0))
const signedFields = ['schema_version', 'namespace', 'package_id', 'type', 'version', 'name', 'author_id', 'license', 'description', 'sha256', 'size', 'platforms', 'architectures', 'min_app_version', 'max_app_version', 'dependencies', 'permissions', 'changelog', 'published_at', 'key_id'] as const

export async function verifyRelease(release: CommunityRelease, pinned: CommunityKey, bytes: Uint8Array) {
  if (release.withdrawn || pinned.revoked || pinned.key_id !== release.key_id || pinned.namespace !== release.namespace) throw new Error('发行或签名密钥已撤回，或来源不匹配')
  if (!valid(release.version) || !valid(release.min_app_version) || gt(release.min_app_version, appPackage.version)
      || (release.max_app_version && (!valid(release.max_app_version) || lt(release.max_app_version, appPackage.version)))) throw new Error('发行版本与当前应用不兼容')
  const key = await crypto.subtle.importKey('raw', bytes64(pinned.public_key), { name: 'Ed25519' }, false, ['verify'])
  const metadata = Object.fromEntries(signedFields.map(field => [field, release[field]]))
  if (!await crypto.subtle.verify('Ed25519', key, bytes64(release.signature), new TextEncoder().encode(canonical(metadata)))) throw new Error('发行签名无效')
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes as Uint8Array<ArrayBuffer>))).map(x => x.toString(16).padStart(2, '0')).join('')
  if (digest !== release.sha256 || bytes.length !== release.size) throw new Error('发行内容摘要或长度无效')
}

export async function installRelease(source: CommunitySource, selected: CommunityRelease, signal?: AbortSignal): Promise<string> {
  const catalog = await fetchCatalog(source, '', selected.type, signal)
  const release = catalog.items.find(item => item.release_id === selected.release_id)
  if (!release || release.sha256 !== selected.sha256 || release.withdrawn) throw new Error('发行已变更或撤回，请刷新目录')
  const liveKeys = await discoverKeys(source.url, signal)
  const pinned = source.keys.find(key => key.key_id === release.key_id && key.namespace === release.namespace)
  const live = liveKeys.find(key => key.key_id === release.key_id)
  if (!pinned || !live || live.revoked || live.public_key !== pinned.public_key) throw new Error('签名密钥未固定或已变更，需重新检查来源')
  const bytes = await download(source, release.download_path, release.type === 'theme' ? 5 * 1024 * 1024 : 10 * 1024 * 1024, signal)
  await verifyRelease(release, pinned, bytes)
  signal?.throwIfAborted()
  if (Object.keys(release.dependencies).length) throw new Error('该包存在依赖，请先在扩展管理中核对依赖版本；不会自动启用依赖。')
  if (release.type === 'theme') {
    const inspection = await inspectThemePackage(await decodeThemePackage(bytes))
    if (!inspection.compatible || inspection.manifest.theme_id !== release.package_id || inspection.manifest.version !== release.version) throw new Error('主题包类型或身份校验失败')
    await installTheme(inspection.manifest, inspection.css ?? '')
  } else if (release.type === 'skill' || release.type === 'plugin') {
    const file = new File([bytes as Uint8Array<ArrayBuffer>], `${release.package_id}.zip`, { type: 'application/zip' })
    await (release.type === 'skill' ? installSkill(file) : installPlugin(file))
  } else {
    // 声明式包仅存为候选，用户可预览/删除；不会替换全局人设或启动模型/MCP。
    const { unzipSync } = await import('fflate')
    let total = 0, count = 0
    const files = unzipSync(bytes, { filter: entry => {
      total += entry.originalSize
      if (++count > 2048 || total > 50 * 1024 * 1024) throw new Error('包展开超限')
      return entry.name.endsWith(`${release.type}.json`)
    } })
    const values = Object.values(files)
    if (values.length !== 1) throw new Error('类型清单不唯一')
    const candidate = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(values[0]))
    localStorage.setItem(`community-candidate:${release.namespace}/${release.package_id}`, JSON.stringify({ release, candidate }))
  }
  return release.type === 'theme' || release.type === 'skill' || release.type === 'plugin' ? '已安装，尚未启用' : '已保存为候选，尚未应用'
}
