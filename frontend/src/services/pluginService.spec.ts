// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as pluginService from './pluginService'

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('pluginService contribution adapter', () => {
  it('lists and executes Plugin Commands with scoped wire fields', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ items: [{ command_id: 'text-tools.uppercase-selection' }] }))
      .mockResolvedValueOnce(jsonResponse({
        command_id: 'text-tools.uppercase-selection',
        status: 'completed',
        effect: { type: 'notification', payload: { level: 'success', message: 'HELLO' } },
      }))

    const commands = await pluginService.listPluginCommands('command_palette')
    const result = await pluginService.executePluginCommand(
      'text-tools.uppercase-selection',
      {},
      { note_id: 'note-1', selection: 'hello' },
    )

    expect(commands[0].command_id).toBe('text-tools.uppercase-selection')
    if (result.effect.type !== 'notification') throw new Error('expected notification effect')
    expect(result.effect.payload.message).toBe('HELLO')
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/plugin-contributions/commands?location=command_palette',
    )
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      arguments: {},
      context: { note_id: 'note-1', selection: 'hello' },
    })
  })

  it('uses separate Settings and Secret endpoints', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({
        plugin_id: 'text-tools', schema_version: 1, fields: [],
        values: { result_limit: 10 }, secrets: { api_key: { configured: false } },
      }))
      .mockResolvedValueOnce(jsonResponse({
        plugin_id: 'text-tools', schema_version: 1, fields: [],
        values: { result_limit: 20 }, secrets: { api_key: { configured: false } },
      }))
      .mockResolvedValueOnce(jsonResponse({ plugin_id: 'text-tools', key: 'api_key', configured: true }))
      .mockResolvedValueOnce(jsonResponse({ plugin_id: 'text-tools', key: 'api_key', configured: false }))

    await pluginService.getPluginSettings('text-tools')
    await pluginService.updatePluginSettings('text-tools', 1, { result_limit: 20 })
    await pluginService.putPluginSecret('text-tools', 'api_key', 'request-only-secret')
    await pluginService.deletePluginSecret('text-tools', 'api_key')

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/plugins/text-tools/settings',
      '/api/plugins/text-tools/settings',
      '/api/plugins/text-tools/settings/api_key/secret',
      '/api/plugins/text-tools/settings/api_key/secret',
    ])
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      schema_version: 1,
      values: { result_limit: 20 },
    })
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({
      secret: 'request-only-secret',
    })
    expect(fetchMock.mock.calls[3][1]?.method).toBe('DELETE')
  })
})
