import { describe, expect, it } from 'vitest'
import { emptyMcpConfig, mergeImportedSecrets, normalizeMcpConfig, parseMcpJson } from './configuration'

describe('MCP configuration normalization', () => {
  it('retains renamed HTTP drafts with the latest spelling and value', () => {
    const config = { ...emptyMcpConfig(), secret_header_keys: ['authorization'] }
    const previous = [{ kind: 'header' as const, key: 'Authorization', value: 'old-value' }]
    expect(mergeImportedSecrets(config, previous, [])).toEqual([{ kind: 'header', key: 'authorization', value: 'old-value' }])
    expect(mergeImportedSecrets(config, previous, [{ kind: 'header', key: 'AUTHORIZATION', value: 'new-value' }])).toEqual([{ kind: 'header', key: 'authorization', value: 'new-value' }])
    expect(mergeImportedSecrets(emptyMcpConfig(), previous, [])).toEqual([])
  })

  it('does not transfer an environment draft across a case-only rename', () => {
    const config = { ...emptyMcpConfig(), secret_environment_keys: ['TOKEN', 'token'] }
    const previous = [{ kind: 'environment' as const, key: 'TOKEN', value: 'upper' }, { kind: 'environment' as const, key: 'token', value: 'lower' }]
    expect(mergeImportedSecrets(config, previous, [])).toEqual(previous)
    expect(mergeImportedSecrets({ ...config, secret_environment_keys: ['token'] }, [previous[0]!], [])).toEqual([])
  })
  it('fills backend defaults for minimal JSON', () => {
    const { config } = parseMcpJson('{"name":"demo","command":"uvx"}')
    expect(config).toMatchObject({ transport: 'stdio', args: [], headers: {}, environment: {}, permissions: [], secret_header_keys: [] })
  })

  it('extracts a key pasted into environment despite its existing secret declaration', () => {
    const { config, secrets } = normalizeMcpConfig({
      name: 'MiniMax', command: 'uvx', secret_environment_keys: ['MINIMAX_API_KEY'],
      environment: { MINIMAX_API_KEY: 'synthetic-key', MINIMAX_API_HOST: 'https://api.minimaxi.com' },
    })
    expect(config.environment).toEqual({ MINIMAX_API_HOST: 'https://api.minimaxi.com' })
    expect(config.secret_environment_keys).toEqual(['MINIMAX_API_KEY'])
    expect(JSON.stringify(config)).not.toContain('synthetic-key')
    expect(secrets).toEqual([{ kind: 'environment', key: 'MINIMAX_API_KEY', value: 'synthetic-key' }])
  })

  it('imports a standard single-server wrapper and legacy timeouts', () => {
    const { config, secrets } = normalizeMcpConfig({ mcpServers: { MiniMax: {
      command: 'uvx', args: ['--with', 'mcp<2', 'minimax-coding-plan-mcp', '-y'],
      env: { MINIMAX_API_KEY: 'synthetic-key' }, timeout: 120, sse_read_timeout: 300,
    } } })
    expect(config).toMatchObject({ name: 'MiniMax', transport: 'stdio', environment: {}, startup_timeout_seconds: 120, tool_timeout_seconds: 300 })
    expect(secrets).toHaveLength(1)
  })

  it('extracts case-insensitive HTTP credentials without duplicate declarations', () => {
    const { config, secrets } = normalizeMcpConfig({ url: 'https://example.test/mcp', headers: { authorization: 'synthetic' }, secret_header_keys: ['Authorization'] })
    expect(config.headers).toEqual({})
    expect(config.secret_header_keys).toEqual(['Authorization'])
    expect(secrets[0]?.key).toBe('Authorization')
  })

  it.each([
    [{ command: 'uvx', args: 'not-array' }, 'args'],
    [{ command: 'uvx', environment: [] }, 'environment'],
    [{ command: 'uvx', timeout: 121 }, '启动超时'],
    [{ command: 'uvx', args: ['[https://example.test](https://example.test)'] }, '纯 URL'],
    [{ command: 'uvx', api_key: 'do-not-echo' }, '顶层'],
    [{ command: 'uvx', env: {}, environment: {} }, '只保留一个'],
    [{ mcpServers: { one: {}, two: {} } }, '一次导入一个'],
  ])('rejects invalid fields without leaking their values', (input, hint) => {
    expect(() => normalizeMcpConfig(input)).toThrow(hint)
    try { normalizeMcpConfig(input) } catch (error) { expect(String(error)).not.toContain('do-not-echo') }
  })
})
