import { describe, expect, it, vi } from 'vitest'
import {
  applyCommandEffect,
  cleanArguments,
  coerceArgument,
  commandFields,
  EFFECT_ROUTES,
  initialArguments,
  missingRequiredFields,
} from './pluginCommandForm'
import type { PluginCommand, PluginCommandEffect } from '@/contracts'

function command(parameters: Record<string, unknown>): PluginCommand {
  return {
    command_id: 'demo.run',
    plugin_id: 'demo',
    title: '示例命令',
    description: '',
    locations: [],
    when: [],
    parameters,
    enabled: true,
  }
}

/** 后端只接受 type=object 的 JSON Schema（contributions.py 显式拒绝其他形态）。 */
const schema = command({
  type: 'object',
  properties: {
    path: { type: 'string', title: '笔记路径', description: '相对于库根目录' },
    count: { type: 'integer', default: 3 },
    recursive: { type: 'boolean' },
    mode: { type: 'string', enum: ['fast', 'full'] },
  },
  required: ['path', 'mode'],
})

describe('commandFields', () => {
  it('摊平 properties 并标记 required', () => {
    const fields = commandFields(schema)

    expect(fields.map((f) => f.key)).toEqual(['path', 'count', 'recursive', 'mode'])
    expect(fields[0]).toMatchObject({ title: '笔记路径', type: 'string', required: true })
    expect(fields[1]).toMatchObject({ type: 'integer', required: false, default: 3 })
    expect(fields[3].enum).toEqual(['fast', 'full'])
  })

  it('没有 title 时用字段名兜底，没有 type 时按 string 处理', () => {
    const fields = commandFields(command({ type: 'object', properties: { raw: {} } }))

    expect(fields[0]).toMatchObject({ key: 'raw', title: 'raw', type: 'string', required: false })
  })

  it('parameters 为空或形态异常时返回空数组而不是抛错', () => {
    expect(commandFields(command({}))).toEqual([])
    expect(commandFields(command({ type: 'object' }))).toEqual([])
    // properties 被写成数组等非法形态时按空处理
    expect(commandFields(command({ type: 'object', properties: ['nope'] as unknown as Record<string, unknown> }))).toEqual([])
  })
})

describe('initialArguments', () => {
  it('布尔字段显式初始化为 false，保证 UI 显示与提交值一致', () => {
    // 回归：之前布尔下拉框显示「否」，但参数对象里没有这个键，
    // 用户没手动切换过就会漏发这个参数。
    const args = initialArguments(schema)

    expect(args.recursive).toBe(false)
    expect('recursive' in args).toBe(true)
  })

  it('有 default 的字段用 default，没有的不塞键', () => {
    const args = initialArguments(schema)

    expect(args.count).toBe(3)
    expect('path' in args).toBe(false)
    expect('mode' in args).toBe(false)
  })

  it('布尔字段的 default 优先于 false', () => {
    const args = initialArguments(
      command({ type: 'object', properties: { flag: { type: 'boolean', default: true } } }),
    )

    expect(args.flag).toBe(true)
  })
})

describe('coerceArgument', () => {
  const field = (type: string) => ({ key: 'k', title: 'k', type, required: false })

  it('布尔只认字符串 "true"', () => {
    expect(coerceArgument(field('boolean'), 'true')).toBe(true)
    expect(coerceArgument(field('boolean'), 'false')).toBe(false)
  })

  it('数字字段转成 number，空串与非法输入转成 undefined', () => {
    expect(coerceArgument(field('integer'), '42')).toBe(42)
    expect(coerceArgument(field('number'), '1.5')).toBe(1.5)
    expect(coerceArgument(field('number'), '')).toBeUndefined()
    expect(coerceArgument(field('number'), 'abc')).toBeUndefined()
  })

  it('字符串原样保留（含空格）', () => {
    expect(coerceArgument(field('string'), ' notes/a.md ')).toBe(' notes/a.md ')
  })
})

describe('missingRequiredFields', () => {
  it('列出未填的必填字段', () => {
    const missing = missingRequiredFields(schema, initialArguments(schema))

    expect(missing.map((f) => f.key)).toEqual(['path', 'mode'])
  })

  it('空白字符串算没填', () => {
    const missing = missingRequiredFields(schema, { path: '   ', mode: 'fast' })

    expect(missing.map((f) => f.key)).toEqual(['path'])
  })

  it('布尔 false 是合法值，不算缺失', () => {
    const boolSchema = command({
      type: 'object',
      properties: { flag: { type: 'boolean' } },
      required: ['flag'],
    })

    expect(missingRequiredFields(boolSchema, { flag: false })).toEqual([])
  })

  it('全部填好时返回空数组', () => {
    expect(missingRequiredFields(schema, { path: 'a.md', mode: 'fast' })).toEqual([])
  })
})

describe('cleanArguments', () => {
  it('丢掉 undefined 的键，保留 false / 0 / 空串', () => {
    const cleaned = cleanArguments({ a: undefined, b: false, c: 0, d: '', e: null })

    expect(cleaned).toEqual({ b: false, c: 0, d: '', e: null })
    expect('a' in cleaned).toBe(false)
  })
})

describe('applyCommandEffect', () => {
  function handlers() {
    return { navigate: vi.fn(), refresh: vi.fn(), notify: vi.fn() }
  }

  it('navigate 真的触发跳转，而不是只提示一句话', async () => {
    // 回归：之前只把 effect 拼成描述文本显示，命令等于没生效。
    const h = handlers()
    await applyCommandEffect({ type: 'navigate', payload: { route: 'workspace' } }, h)

    expect(h.navigate).toHaveBeenCalledWith('/workspace')
    expect(h.notify).not.toHaveBeenCalled()
  })

  it('每个白名单路由都能解析出路径', async () => {
    for (const route of Object.keys(EFFECT_ROUTES)) {
      const h = handlers()
      await applyCommandEffect(
        { type: 'navigate', payload: { route } } as PluginCommandEffect,
        h,
      )
      expect(h.navigate).toHaveBeenCalledWith(EFFECT_ROUTES[route])
    }
  })

  it('未知路由只提示不跳转，避免 router.push(undefined)', async () => {
    const h = handlers()
    await applyCommandEffect(
      { type: 'navigate', payload: { route: 'nope' } } as unknown as PluginCommandEffect,
      h,
    )

    expect(h.navigate).not.toHaveBeenCalled()
    expect(h.notify.mock.calls[0][0]).toContain('nope')
  })

  it('refresh 真的触发对应 scope 的刷新', async () => {
    const h = handlers()
    await applyCommandEffect({ type: 'refresh', payload: { scope: 'workspace' } }, h)

    expect(h.refresh).toHaveBeenCalledWith('workspace')
  })

  it('等待异步 refresh 完成后才返回', async () => {
    const h = handlers()
    let done = false
    h.refresh.mockImplementation(async () => {
      await Promise.resolve()
      done = true
    })

    await applyCommandEffect({ type: 'refresh', payload: { scope: 'commands' } }, h)

    expect(done).toBe(true)
  })

  it('notification 原样透出插件消息', async () => {
    const h = handlers()
    await applyCommandEffect(
      { type: 'notification', payload: { level: 'info', message: '索引已重建' } },
      h,
    )

    expect(h.notify).toHaveBeenCalledWith('索引已重建')
  })

  it('job 提示任务 id', async () => {
    const h = handlers()
    await applyCommandEffect({ type: 'job', payload: { job_id: 'job_7' } }, h)

    expect(h.notify.mock.calls[0][0]).toContain('job_7')
  })

  it('none 或未知 type 按「已完成」处理，不猜测语义', async () => {
    const h = handlers()
    await applyCommandEffect({ type: 'none', payload: {} }, h)

    expect(h.notify).toHaveBeenCalledWith('命令执行完成。')
    expect(h.navigate).not.toHaveBeenCalled()
    expect(h.refresh).not.toHaveBeenCalled()
  })
})
