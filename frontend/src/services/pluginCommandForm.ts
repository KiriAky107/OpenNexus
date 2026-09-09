import type { PluginCommand, PluginCommandEffect } from '@/contracts'

/** 命令参数的 JSON Schema 字段定义（后端用 Draft 2020-12 校验）。 */
export interface CommandField {
  key: string
  title: string
  type: string
  required: boolean
  enum?: string[]
  default?: unknown
  description?: string
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/**
 * 把命令的 parameters（object schema）摊平成表单字段。
 *
 * 后端只接受 type=object 的 schema（contributions.py 里显式拒绝其他形态），
 * 所以这里只处理 properties + required 两个键，嵌套对象按文本输入兜底。
 */
export function commandFields(command: PluginCommand): CommandField[] {
  const schema = asRecord(command.parameters)
  const properties = asRecord(schema.properties)
  const requiredKeys = Array.isArray(schema.required) ? schema.required.map(String) : []

  return Object.entries(properties).map(([key, rawDefinition]) => {
    const definition = asRecord(rawDefinition)
    return {
      key,
      title: typeof definition.title === 'string' && definition.title ? definition.title : key,
      type: typeof definition.type === 'string' ? definition.type : 'string',
      required: requiredKeys.includes(key),
      enum: Array.isArray(definition.enum) ? definition.enum.map(String) : undefined,
      default: definition.default,
      description: typeof definition.description === 'string' ? definition.description : undefined,
    }
  })
}

/**
 * 表单初始值。
 *
 * 布尔字段必须显式给 false —— 下拉框默认显示「否」，如果参数对象里
 * 没有这个键，用户看到的和实际提交的就不一致。
 */
export function initialArguments(command: PluginCommand): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const field of commandFields(command)) {
    if (field.default !== undefined) result[field.key] = field.default
    else if (field.type === 'boolean') result[field.key] = false
  }
  return result
}

/** 按字段类型把输入框的字符串转成 schema 期望的类型。 */
export function coerceArgument(field: CommandField, raw: string): unknown {
  if (raw.trim() === '') return undefined
  if (field.type === 'boolean') return raw === 'true'
  if (field.type === 'number' || field.type === 'integer') {
    if (raw.trim() === '') return undefined
    const parsed = Number(raw)
    return Number.isNaN(parsed) ? undefined : parsed
  }
  if (field.type === 'object' || field.type === 'array') {
    try { return JSON.parse(raw) } catch { return raw } // 后端报告模式错误而不丢弃输入。
  }
  return raw
}

function isBlank(value: unknown): boolean {
  if (value === undefined || value === null) return true
  return typeof value === 'string' && value.trim() === ''
}

/**
 * 找出还没填的必填字段。
 *
 * 后端会用 JSON Schema 再校验一次，这里做前置检查只为了别让用户
 * 提交一次才知道少填了什么。布尔的 false 是合法值，不算缺失。
 */
export function missingRequiredFields(
  command: PluginCommand,
  args: Record<string, unknown>,
): CommandField[] {
  return commandFields(command).filter((field) => field.required && isBlank(args[field.key]))
}

/** undefined 的键不该出现在请求体里。 */
export function cleanArguments(args: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(args)) {
    if (value !== undefined) result[key] = value
  }
  return result
}

/** navigate effect 的路由白名单，与 router/index.ts 的路径一一对应。 */
export const EFFECT_ROUTES: Record<string, string> = {
  'vault-entry': '/',
  workspace: '/workspace',
  search: '/search',
  chat: '/chat',
  agent: '/agent/runs',
  tasks: '/tasks',
  skills: '/extensions/skills',
  plugins: '/extensions/plugins',
  themes: '/themes',
  settings: '/settings',
}

export interface EffectHandlers {
  navigate: (path: string) => Promise<unknown> | unknown
  refresh: (scope: 'workspace' | 'commands' | 'settings' | 'plugins') => Promise<unknown> | unknown
  notify: (message: string) => void
}

/**
 * 执行命令返回的 effect。
 *
 * navigate / refresh 必须真的发生 —— 之前这里只是把 effect 拼成一句话
 * 显示给用户，命令等于没生效。未知 type 一律按「已完成」处理，
 * 不猜测语义。
 */
export async function applyCommandEffect(
  effect: PluginCommandEffect,
  handlers: EffectHandlers,
): Promise<void> {
  switch (effect.type) {
    case 'notification':
      handlers.notify(effect.payload.message)
      return
    case 'navigate': {
      const path = EFFECT_ROUTES[effect.payload.route]
      if (!path) {
        handlers.notify(`命令请求跳转到未知路由「${effect.payload.route}」，已忽略。`)
        return
      }
      await handlers.navigate(path)
      return
    }
    case 'refresh':
      await handlers.refresh(effect.payload.scope)
      handlers.notify('相关数据已刷新。')
      return
    case 'job':
      handlers.notify(`已创建后台任务：${effect.payload.job_id}`)
      return
    default:
      handlers.notify('命令执行完成。')
  }
}
