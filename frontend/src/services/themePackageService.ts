import type { InstalledTheme, ThemeManifest, ThemePackageInspection } from '@/contracts'
import paperMomentsPackage from '@/assets/themes/paper-moments.theme?raw'
import { valid, gt } from 'semver'
import appPackage from '../../package.json'
import { isMap, parseDocument } from 'yaml'

export const THEME_APP_VERSION = appPackage.version

const STORAGE_KEY = 'installed-themes'
const ACTIVE_CUSTOM_KEY = 'active-custom-theme'
export const MAX_THEME_BYTES = 5 * 1024 * 1024

/** Normalize all transports to the existing single-file inspection format. */
export async function decodeThemePackage(bytes: Uint8Array): Promise<string> {
  if (bytes.length > MAX_THEME_BYTES) throw new Error('主题包不能超过 5 MB')
  const decode = (data: Uint8Array) => new TextDecoder('utf-8', { fatal: true }).decode(data)
  if (bytes[0] !== 0x50 || bytes[1] !== 0x4b) return decode(bytes)
  const { unzipSync } = await import('fflate')
  let total = 0
  let count = 0
  const names = new Set<string>()
  const safePath = (path: string) => path.length > 0 && !path.startsWith('/') && !path.includes('\\') && !path.includes(':') && !path.split('/').some(part => part === '..' || part === '.')
  const files = unzipSync(bytes, { filter: file => {
    if (!safePath(file.name) || names.has(file.name)) throw new Error('ZIP 包含非法或重复路径')
    names.add(file.name)
    total += file.originalSize
    if (++count > 100 || total > 10 * 1024 * 1024) throw new Error('ZIP 解压内容不能超过 10 MB 或 100 个文件')
    return !file.name.endsWith('/')
  } })
  const entries = Object.keys(files)
  const manifests = entries.filter(name => /(^|\/)(theme|manifest)\.ya?ml$/i.test(name))
  if (!manifests.length) {
    const single = entries.filter(name => name.endsWith('.theme'))
    if (single.length !== 1) throw new Error('ZIP 需要唯一的 theme.yaml / manifest.yaml，或一个 .theme 文件')
    return decode(files[single[0]!]!)
  }
  if (manifests.length !== 1) throw new Error('ZIP 中存在多个主题清单，请每包只放一个主题')
  const manifestPath = manifests[0]!
  const yaml = decode(files[manifestPath]!)
  const manifest = inspectYamlContent(yaml)
  if (!safePath(manifest.css_entry)) throw new Error('css_entry 必须是包内相对路径')
  const base = manifestPath.slice(0, manifestPath.lastIndexOf('/') + 1)
  const css = files[base + manifest.css_entry]
  if (!css) throw new Error(`ZIP 中找不到 CSS 文件：${manifest.css_entry}`)
  return `${yaml}\n---\n${decode(css)}`
}

export async function fetchThemePackage(urlText: string, signal?: AbortSignal): Promise<string> {
  const url = new URL(urlText.trim())
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('请输入不含账号密码的 HTTP(S) 主题包直链')
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) abort()
  const timeout = setTimeout(abort, 30000)
  try {
    const response = await fetch(url.href, { signal: controller.signal, credentials: 'omit', referrerPolicy: 'no-referrer' })
    if (!response.ok) throw new Error(`下载失败：HTTP ${response.status}`)
    if (Number(response.headers.get('content-length')) > MAX_THEME_BYTES) throw new Error('主题包不能超过 5 MB')
    if (!response.body) throw new Error('下载内容为空')
    const reader = response.body.getReader()
    const chunks: Uint8Array[] = []
    let size = 0
    try {
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        size += value.length
        if (size > MAX_THEME_BYTES) throw new Error('主题包不能超过 5 MB')
        chunks.push(value)
      }
    } finally { await reader.cancel() }
    const bytes = new Uint8Array(size)
    let offset = 0
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length }
    return await decodeThemePackage(bytes)
  } catch (error) {
    if (controller.signal.aborted) throw new Error('下载已取消或超时，请重试')
    if (error instanceof TypeError) throw new Error('无法下载，请检查直链及服务器是否允许跨域访问（CORS）')
    throw error
  } finally {
    clearTimeout(timeout)
    signal?.removeEventListener('abort', abort)
  }
}

function loadStoredThemes(): InstalledTheme[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as InstalledTheme[]) : []
  } catch {
    return []
  }
}

function saveThemes(themes: InstalledTheme[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(themes))
}

function validateManifest(raw: Record<string, unknown>): { manifest: ThemeManifest; warnings: string[] } {
  const warnings: string[] = []
  const required = ['theme_id', 'name', 'version', 'author', 'min_app_version', 'css_entry']
  for (const field of required) {
    if (!raw[field]) {
      throw new Error(`THEME_MANIFEST_INVALID: missing required field '${field}'`)
    }
  }
  if (!/^[a-z0-9_-]+$/.test(String(raw.theme_id))) {
    throw new Error('THEME_MANIFEST_INVALID: theme_id must match [a-z0-9_-]+')
  }
  for (const field of ['version', 'min_app_version']) {
    if (typeof raw[field] !== 'string' || !valid(raw[field] as string)) throw new Error(`THEME_MANIFEST_INVALID: ${field} 必须是有效的 semver 版本号`)
  }
  if (gt(raw.min_app_version as string, THEME_APP_VERSION)) throw new Error(`THEME_VERSION_INCOMPATIBLE: 主题需要应用 ${raw.min_app_version}，当前版本为 ${THEME_APP_VERSION}`)
  if (raw.is_dark !== undefined && typeof raw.is_dark !== 'boolean') throw new Error('THEME_MANIFEST_INVALID: is_dark 必须是布尔值')
  const cssEntry = String(raw.css_entry)
  if (cssEntry.includes('://') || cssEntry.startsWith('data:')) {
    throw new Error('THEME_SECURITY_VIOLATION: css_entry must be a relative path within the package')
  }
  const manifest: ThemeManifest = {
    theme_id: String(raw.theme_id),
    name: String(raw.name),
    version: String(raw.version),
    author: String(raw.author),
    description: raw.description ? String(raw.description) : undefined,
    min_app_version: String(raw.min_app_version),
    is_dark: Boolean(raw.is_dark ?? false),
    css_entry: cssEntry,
    preview: raw.preview ? String(raw.preview) : undefined,
    tags: Array.isArray(raw.tags) ? raw.tags.map(String) : undefined,
    homepage: raw.homepage ? String(raw.homepage) : undefined,
    license: raw.license ? String(raw.license) : undefined,
  }
  return { manifest, warnings }
}

function validateCssSafety(css: string): string[] {
  const warnings: string[] = []
  const lower = css.toLowerCase()
  if (lower.includes('@import')) {
    throw new Error('THEME_SECURITY_VIOLATION: @import is not allowed in theme CSS')
  }
  if (lower.includes('url(') && !lower.includes('url(data:')) {
    warnings.push('CSS 包含远程资源引用，预览时可能无法加载')
  }
  if (lower.includes('expression(') || lower.includes('javascript:')) {
    throw new Error('THEME_SECURITY_VIOLATION: CSS expressions are not allowed')
  }
  return warnings
}

function applyThemeCss(themeId: string, css: string) {
  let styleEl = document.getElementById(`theme-style-${themeId}`) as HTMLStyleElement | null
  if (!styleEl) {
    styleEl = document.createElement('style')
    styleEl.id = `theme-style-${themeId}`
    document.head.appendChild(styleEl)
  }
  styleEl.textContent = css
}

function removeThemeCss(themeId: string) {
  const styleEl = document.getElementById(`theme-style-${themeId}`)
  if (styleEl) styleEl.remove()
}

function inspectYamlContent(yamlText: string): ThemeManifest {
  const document = parseDocument(yamlText)
  if (document.errors.length || document.warnings.length || !isMap(document.contents)) throw new Error('THEME_MANIFEST_INVALID: 主题清单必须是有效的 YAML 映射')
  const result = document.toJS({ maxAliasCount: 20 }) as Record<string, unknown>
  const { manifest } = validateManifest(result)
  return manifest
}

/**
 * 主题包是单文件文本格式：YAML 清单 + 一行 `---` + 主题 CSS。
 *
 *   theme_id: my-theme
 *   name: My Theme
 *   ...
 *   ---
 *   [data-theme="my-theme"] { --color-... }
 *
 * ZIP 必须先通过 decodeThemePackage 解码；此函数只处理规范化后的文本。
 */
export function parseThemePackage(packageData: string): { manifestText: string; css: string } {
  if (looksLikeZip(packageData)) {
    throw new Error(
      'THEME_PACKAGE_UNSUPPORTED_FORMAT: 暂不支持 ZIP 主题包，请提供「YAML 清单 + --- + CSS」的单文件主题。',
    )
  }

  const lines = packageData.split(/\r?\n/)
  const separatorIndex = lines.findIndex((line) => line.trim() === '---')
  if (separatorIndex < 0) {
    throw new Error(
      'THEME_PACKAGE_INVALID: 主题包缺少 `---` 分隔行，无法区分清单与 CSS。',
    )
  }

  const manifestText = lines.slice(0, separatorIndex).join('\n')
  const css = lines.slice(separatorIndex + 1).join('\n').trim()
  if (!css) {
    throw new Error('THEME_CSS_INVALID: 主题包内没有 CSS 内容。')
  }
  return { manifestText, css }
}

/** ZIP 的魔数是 PK\x03\x04；base64 形式（readAsDataURL）开头是 UEsDB。 */
function looksLikeZip(data: string): boolean {
  if (data.startsWith('PK')) return true
  return /^data:.*;base64,UEsDB/.test(data) || data.startsWith('UEsDB')
}

export async function selectThemePackage(): Promise<string | null> {
  return new Promise((resolve, reject) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.yaml,.yml,.theme,.zip'
    input.multiple = false
    input.onchange = () => {
      const file = input.files?.[0]
      if (!file) { resolve(null); return }
      if (file.size > MAX_THEME_BYTES) { reject(new Error('主题包不能超过 5 MB')); return }
      const reader = new FileReader()
      reader.onload = () => { void decodeThemePackage(new Uint8Array(reader.result as ArrayBuffer)).then(resolve, reject) }
      reader.onerror = () => resolve(null)
      reader.readAsArrayBuffer(file)
    }
    input.oncancel = () => resolve(null)
    input.click()
  })
}

export async function inspectThemePackage(packageData: string): Promise<ThemePackageInspection> {
  const package_id = `theme_pkg_${Date.now()}`
  try {
    const { manifestText, css } = parseThemePackage(packageData)
    const manifest = inspectYamlContent(manifestText)
    // CSS 的安全校验放在这里，不合规的包在「预览」阶段就该被拒，
    // 而不是等到用户点安装。
    const warnings = validateCssSafety(css)
    if (!css.includes(`[data-theme="${manifest.theme_id}"]`)) {
      warnings.push(`CSS 未包含 [data-theme="${manifest.theme_id}"] 选择器，主题可能不会生效。`)
    }
    return {
      package_id,
      manifest,
      preview_url: '',
      warnings,
      compatible: true,
      css,
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : '未知错误'
    const error_code = message.startsWith('THEME_') ? message.split(':')[0] : 'THEME_MANIFEST_INVALID'
    return {
      package_id,
      manifest: {} as ThemeManifest,
      preview_url: '',
      warnings: [message],
      compatible: false,
      error_code,
      css: '',
    }
  }
}

export async function installTheme(
  manifest: ThemeManifest,
  cssContent: string,
): Promise<InstalledTheme> {
  validateManifest(manifest as unknown as Record<string, unknown>)
  // validateCssSafety 会对 @import / expression() / javascript: 抛错，
  // 必须在 applyThemeCss 之前调用 —— 未校验的 CSS 一律不许进入页面。
  const warnings = validateCssSafety(cssContent)
  if (warnings.length > 0) {
    console.warn('[theme] CSS validation warnings:', warnings)
  }
  const installed: InstalledTheme = {
    theme_id: manifest.theme_id,
    name: manifest.name,
    version: manifest.version,
    author: manifest.author,
    description: manifest.description,
    is_dark: manifest.is_dark,
    builtin: false,
    enabled: false,
    installed_at: new Date().toISOString(),
    manifest,
    code_theme: manifest.is_dark ? 'github-dark' : 'github-light',
  }
  const existing = loadStoredThemes()
  const idx = existing.findIndex((t) => t.theme_id === manifest.theme_id)
  if (idx >= 0) existing[idx] = installed
  else existing.push(installed)
  localStorage.setItem(`${STORAGE_KEY}-css-${manifest.theme_id}`, cssContent)
  saveThemes(existing)
  return installed
}

export async function listInstalledThemes(): Promise<InstalledTheme[]> {
  return loadStoredThemes()
}

export async function enableTheme(themeId: string): Promise<InstalledTheme> {
  const themes = loadStoredThemes()
  const theme = themes.find((t) => t.theme_id === themeId)
  if (!theme) throw new Error('THEME_PACKAGE_NOT_FOUND')
  validateManifest(theme.manifest as unknown as Record<string, unknown>)
  theme.enabled = true
  saveThemes(themes)
  return theme
}

export async function disableTheme(themeId: string): Promise<void> {
  const themes = loadStoredThemes()
  const theme = themes.find((t) => t.theme_id === themeId)
  if (theme) {
    theme.enabled = false
    saveThemes(themes)
  }
}

export async function uninstallTheme(themeId: string): Promise<void> {
  const themes = loadStoredThemes()
  const idx = themes.findIndex((t) => t.theme_id === themeId)
  if (idx >= 0) {
    themes.splice(idx, 1)
    saveThemes(themes)
  }
  removeThemeCss(themeId)
  localStorage.removeItem(`${STORAGE_KEY}-css-${themeId}`)
  const active = localStorage.getItem(ACTIVE_CUSTOM_KEY)
  if (active === themeId) localStorage.removeItem(ACTIVE_CUSTOM_KEY)
}

export function getActiveCustomTheme(): string | null {
  return localStorage.getItem(ACTIVE_CUSTOM_KEY)
}

export function setActiveCustomTheme(themeId: string | null) {
  if (themeId) {
    const theme = loadStoredThemes().find(item => item.theme_id === themeId)
    if (!theme) throw new Error('THEME_PACKAGE_NOT_FOUND')
    validateManifest(theme.manifest as unknown as Record<string, unknown>)
  }
  const css = themeId ? localStorage.getItem(`${STORAGE_KEY}-css-${themeId}`) : null
  // Validate before changing the current page. Only the selected theme owns a style node.
  if (css) validateCssSafety(css)
  document.head.querySelectorAll('style[id^="theme-style-"]').forEach(style => style.remove())
  if (themeId && css) applyThemeCss(themeId, css)
  if (themeId) localStorage.setItem(ACTIVE_CUSTOM_KEY, themeId)
  else localStorage.removeItem(ACTIVE_CUSTOM_KEY)
}

const paperMoments = parseThemePackage(paperMomentsPackage)

export const mockCommunityThemes: ThemeManifest[] = [
  { ...inspectYamlContent(paperMoments.manifestText), tags: ['浅色', '手帐', '纸张'] },
  {
    theme_id: 'ocean-blue',
    name: 'Ocean Blue',
    version: '1.6.0',
    author: 'community',
    description: '宁静的海洋蓝色主题，适合长时间阅读',
    min_app_version: '0.2.0',
    is_dark: false,
    css_entry: 'theme.css',
    tags: ['浅色', '蓝色', '阅读'],
    license: 'MIT',
  },
  {
    theme_id: 'midnight-purple',
    name: 'Midnight Purple',
    version: '2.4.0',
    author: 'night-owl',
    description: '深紫色暗夜主题，适合编码',
    min_app_version: '0.2.0',
    is_dark: true,
    css_entry: 'theme.css',
    tags: ['深色', '紫色', '极客'],
    license: 'Apache-2.0',
  },
]

function buildCommunityThemeCss(themeId: string, isDark: boolean): string {
  const palettes: Record<string, { primary: string; soft: string; hover: string }> = {
    'ocean-blue': { primary: '#0077b6', soft: '#e0f0fa', hover: '#005f92' },
    'midnight-purple': { primary: '#9d4edd', soft: '#2b1a3e', hover: '#7b2cbf' },
  }
  const p = palettes[themeId] ?? palettes['ocean-blue']
  if (isDark) {
    return `[data-theme="${themeId}"] {
  --color-background-primary: #1a1b26;
  --color-background-secondary: #24283b;
  --color-background-tertiary: #2f334d;
  --color-background-hover: #2d2f45;
  --color-background-active: #3d4261;
  --color-surface-primary: #24283b;
  --color-surface-secondary: #1a1b26;
  --color-surface-elevated: #2f334d;
  --color-text-primary: #c0caf5;
  --color-text-secondary: #9aa5ce;
  --color-text-tertiary: #565f89;
  --color-text-link: ${p.primary};
  --color-accent-primary: ${p.primary};
  --color-accent-primary-hover: ${p.hover};
  --color-accent-soft: ${p.soft};
  --color-border-default: #3b3f5c;
  --color-border-subtle: #2f334d;
  --color-border-focus: ${p.primary};
  --color-success: #9ece6a;
  --color-success-soft: #1f2a1a;
  --color-warning: #e0af68;
  --color-warning-soft: #2d2418;
  --color-error: #f7768e;
  --color-error-soft: #2d1a1f;
  --color-info: #7aa2f7;
  --color-info-soft: #1a2030;
}`
  }
  return `[data-theme="${themeId}"] {
  --color-background-primary: #ffffff;
  --color-background-secondary: #f8fafc;
  --color-background-tertiary: #eef2f7;
  --color-background-hover: #f1f5f9;
  --color-background-active: #e2e8f0;
  --color-surface-primary: #ffffff;
  --color-surface-secondary: #fafbfc;
  --color-surface-elevated: #ffffff;
  --color-text-primary: #1e293b;
  --color-text-secondary: #64748b;
  --color-text-tertiary: #94a3b8;
  --color-text-link: ${p.primary};
  --color-accent-primary: ${p.primary};
  --color-accent-primary-hover: ${p.hover};
  --color-accent-soft: ${p.soft};
  --color-border-default: #e2e8f0;
  --color-border-subtle: #f1f5f9;
  --color-border-focus: ${p.primary};
  --color-success: #10b981;
  --color-success-soft: #d1fae5;
  --color-warning: #f59e0b;
  --color-warning-soft: #fef3c7;
  --color-error: #ef4444;
  --color-error-soft: #fee2e2;
  --color-info: #3b82f6;
  --color-info-soft: #dbeafe;
}`
}

export async function installCommunityTheme(themeId: string): Promise<InstalledTheme> {
  const themeManifest = mockCommunityThemes.find((t) => t.theme_id === themeId)
  if (!themeManifest) throw new Error('THEME_PACKAGE_NOT_FOUND')
  const css = getCommunityThemePreviewCss(themeId)
  return installTheme(themeManifest, css)
}

export function getCommunityThemePreviewCss(themeId: string): string {
  if (themeId === 'paper-moments') return paperMoments.css
  const t = mockCommunityThemes.find((m) => m.theme_id === themeId)
  if (!t) return ''
  return buildCommunityThemeCss(themeId, t.is_dark) + `
[data-theme="${themeId}"] {
  color-scheme: ${t.is_dark ? 'dark' : 'light'};
  --color-markdown-selection: ${t.is_dark ? '#443252' : '#d4eaf5'};
  --color-editor-scroll-background: var(--color-surface-elevated);
  --color-editor-scroll-text: var(--color-accent-primary);
  --color-callout-info: ${t.is_dark ? '#9dbbff' : '#126589'};
  --color-callout-success: ${t.is_dark ? '#a7d58c' : '#267049'};
  --color-callout-warning: ${t.is_dark ? '#efc886' : '#885c13'};
  --color-callout-danger: ${t.is_dark ? '#ff9caf' : '#b13d4d'};
  --color-callout-important: ${t.is_dark ? '#d4afff' : '#7050a3'};
  --color-callout-quote: ${t.is_dark ? '#b0b9dd' : '#53697d'};
  --color-text-inverse: ${t.is_dark ? '#1a1b26' : '#ffffff'};
  --color-text-disabled: color-mix(in srgb, var(--color-text-primary) 45%, var(--color-surface-primary));
  --color-background-overlay: ${t.is_dark ? '#000000a6' : '#00000073'};
  --color-accent-primary-active: color-mix(in srgb, var(--color-accent-primary) 80%, var(--color-text-primary));
  --color-accent-secondary: var(--color-accent-primary);
  --color-accent-soft-hover: color-mix(in srgb, var(--color-accent-soft) 80%, var(--color-accent-primary));
  --color-border-disabled: var(--color-border-subtle);
  --color-markdown-grid: var(--color-border-default);
  --color-markdown-marker: var(--color-text-secondary);
  --color-markdown-table-header: var(--color-background-tertiary);
}`
}
