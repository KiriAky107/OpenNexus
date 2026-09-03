import type { McpServerInput } from '@/contracts'

export type SecretKind = 'environment' | 'header'
export interface ImportedSecret { kind: SecretKind; key: string; value: string }

export function mergeImportedSecrets(config: McpServerInput, previous: ImportedSecret[], incoming: ImportedSecret[]): ImportedSecret[] {
  const merged = new Map<string, ImportedSecret>()
  for (const item of [...previous, ...incoming]) {
    const normalize = (key: string) => item.kind === 'header' ? key.toLowerCase() : key
    const keys = item.kind === 'header' ? config.secret_header_keys : config.secret_environment_keys
    const declared = keys.find(key => normalize(key) === normalize(item.key))
    if (declared === undefined) continue
    // HTTP identity is case-insensitive, but the Secret API requires the current
    // declared spelling. New inline values replace older drafts of that identity.
    merged.set(`${item.kind}:${normalize(declared)}`, { ...item, key: declared })
  }
  return [...merged.values()]
}

export function emptyMcpConfig(): McpServerInput {
  return {
    name: '', transport: 'stdio', command: '', args: [], url: null, headers: {},
    environment: {}, secret_environment_keys: [], secret_header_keys: [], permissions: [],
    startup_timeout_seconds: 15, tool_timeout_seconds: 30,
  }
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(`${label}必须是 JSON 对象`)
  return value as Record<string, unknown>
}

function strings(value: unknown, label: string): string[] {
  if (value === undefined) return []
  if (!Array.isArray(value) || value.some(item => typeof item !== 'string')) throw new Error(`${label}必须是字符串数组`)
  return [...value]
}

function entries(value: unknown, label: string): Record<string, string> {
  if (value === undefined) return {}
  const result = object(value, label)
  if (Object.values(result).some(item => typeof item !== 'string')) throw new Error(`${label}必须是字符串键值 JSON 对象`)
  return { ...result } as Record<string, string>
}

function timeout(value: unknown, fallback: number, max: number, label: string): number {
  if (value === undefined) return fallback
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 1 || value > max) throw new Error(`${label}必须是 1–${max} 秒之间的数字`)
  return value
}

// Do not silently rewrite executable arguments or secret values copied from chat.
function checkUrl(value: string, label: string) {
  if (/^\[https?:\/\//i.test(value)) throw new Error(`${label}请填写纯 URL，不要粘贴 Markdown 链接`)
}

export function parseMcpJson(raw: string, fallbackName = '', requireConnection = true) {
  let parsed: unknown
  try { parsed = JSON.parse(raw) }
  catch { throw new Error('服务器配置不是有效 JSON；请检查逗号、引号和无效的 \\_ 转义') }
  return normalizeMcpConfig(parsed, fallbackName, requireConnection)
}

/** Normalize external client JSON before it reaches either the form or the API.
 * Inline secrets leave the public config here and are sent only to the Secret API.
 */
export function normalizeMcpConfig(parsed: unknown, fallbackName = '', requireConnection = true) {
  let raw = object(parsed, '服务器配置')
  if ('mcpServers' in raw) {
    const servers = Object.entries(object(raw.mcpServers, 'mcpServers'))
    if (servers.length !== 1) throw new Error('请一次导入一个 MCP 服务器')
    fallbackName = servers[0]![0]
    raw = object(servers[0]![1], '服务器配置')
  }
  const allowed = new Set([...Object.keys(emptyMcpConfig()), 'version', 'env', 'type', 'timeout', 'sse_read_timeout'])
  if (Object.keys(raw).some(key => !allowed.has(key))) {
    // Never echo arbitrary unknown keys: pasted secrets sometimes become JSON keys.
    throw new Error('服务器配置含不支持的字段；API Key 请放在 env/environment 的对应变量中，不要放在顶层')
  }
  if (raw.env !== undefined && raw.environment !== undefined) throw new Error('env 与 environment 请只保留一个，避免覆盖配置')
  const transport = raw.transport ?? raw.type ?? (raw.url ? 'streamable_http' : 'stdio')
  if (!['stdio', 'streamable_http', 'sse'].includes(transport as string)) throw new Error('transport 必须是 stdio、streamable_http 或 sse')
  const config = emptyMcpConfig()
  config.transport = transport as McpServerInput['transport']
  const name = raw.name ?? (fallbackName || (typeof raw.command === 'string' ? raw.command : 'MCP 服务器'))
  if (typeof name !== 'string' || (requireConnection && !name.trim()) || name.trim().length > 80) throw new Error('服务器名称必须为 1–80 个字符')
  config.name = name.trim()
  for (const key of ['command', 'url'] as const) {
    const value = raw[key]
    if (value !== undefined && value !== null && typeof value !== 'string') throw new Error(`${key}必须是字符串`)
    config[key] = typeof value === 'string' ? value.trim() : null
  }
  config.args = strings(raw.args, 'args')
  if (config.args.length > 64) throw new Error('args 最多允许 64 项')
  for (const value of config.args) checkUrl(value, 'args 中的地址')
  config.environment = entries(raw.environment ?? raw.env, 'environment/env')
  config.headers = entries(raw.headers, 'headers')
  config.secret_environment_keys = [...new Set(strings(raw.secret_environment_keys, 'secret_environment_keys'))]
  config.secret_header_keys = [...new Set(strings(raw.secret_header_keys, 'secret_header_keys'))]
  config.permissions = strings(raw.permissions, 'permissions')
  config.startup_timeout_seconds = timeout(raw.startup_timeout_seconds ?? raw.timeout, 15, 120, '启动超时')
  // Compatibility policy: legacy read timeout becomes the tool wait budget, not an SSE transport setting.
  config.tool_timeout_seconds = timeout(raw.tool_timeout_seconds ?? raw.sse_read_timeout, 30, 300, '工具超时')
  if (config.transport === 'stdio') {
    if (requireConnection && !config.command) throw new Error('stdio 配置必须填写 command')
    if (config.url || Object.keys(config.headers).length || config.secret_header_keys.length) throw new Error('stdio 配置不能包含 URL 或 HTTP Header')
  } else {
    if (requireConnection && !config.url) throw new Error('HTTP/SSE 配置必须填写 url')
    if (config.url) {
      checkUrl(config.url, 'url')
      let url: URL
      try { url = new URL(config.url) } catch { throw new Error('url 必须是有效的 HTTP(S) 地址') }
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash) throw new Error('url 必须为不含账号密码或片段的 HTTP(S) 地址')
    }
    if (config.command || config.args.length || Object.keys(config.environment).length || config.secret_environment_keys.length) throw new Error('HTTP/SSE 配置不能包含 command、args 或环境变量')
  }
  const secrets: ImportedSecret[] = []
  for (const kind of ['environment', 'header'] as const) {
    const values = kind === 'environment' ? config.environment : config.headers
    const keys = kind === 'environment' ? config.secret_environment_keys : config.secret_header_keys
    const identity = (key: string) => kind === 'header' ? key.toLowerCase() : key
    const allKeys = [...Object.keys(values), ...keys]
    if (kind === 'header' && (new Set(keys.map(identity)).size !== keys.length || new Set(Object.keys(values).map(identity)).size !== Object.keys(values).length)) throw new Error('HTTP Header 名称不能仅大小写不同而重复声明')
    const validKey = kind === 'environment' ? /^[A-Za-z_][A-Za-z0-9_]{0,127}$/ : /^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$/
    if (allKeys.some(key => !validKey.test(key))) throw new Error(`${kind === 'environment' ? '环境变量' : 'Header'}名称无效；敏感变量名只能填名称，不能填密钥值`)
    for (const [key, value] of Object.entries(values)) {
      const declared = keys.find(item => identity(item) === identity(key))
      const sensitive = /api[_-]?key|token|secret|password|authorization|cookie|credential/i.test(key)
      if (declared || sensitive) {
        if (!value || value.length > 32768) throw new Error('密钥值必须为 1–32768 个字符')
        const secretKey = declared ?? key
        if (!declared) keys.push(key)
        secrets.push({ kind, key: secretKey, value })
        delete values[key]
      } else if (/host|url|endpoint/i.test(key)) checkUrl(value, '环境变量或 Header 地址')
    }
  }
  return { config, secrets }
}
