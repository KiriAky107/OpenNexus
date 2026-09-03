// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { McpServer } from '@/contracts'
import * as service from '@/services/mcpServerService'
import McpServersView from './McpServersView.vue'

vi.mock('@/services/mcpServerService', () => ({
  listMcpServers: vi.fn(), createMcpServer: vi.fn(), updateMcpServer: vi.fn(),
  deleteMcpServer: vi.fn(), trustMcpServer: vi.fn(), testMcpServer: vi.fn(),
  enableMcpServer: vi.fn(), disableMcpServer: vi.fn(), putMcpServerSecret: vi.fn(),
}))

const server: McpServer = {
  server_id: 'server-1', version: 2, name: 'Remote', transport: 'streamable_http',
  command: null, args: [], url: 'https://mcp.example.test/mcp', headers: {}, environment: {},
  secret_environment: {}, secret_headers: { Authorization: false }, permissions: [],
  startup_timeout_seconds: 15, tool_timeout_seconds: 30, enabled: false, trusted: true,
  command_digest: 'a'.repeat(64), command_summary: 'https://mcp.example.test/mcp',
  status: 'stopped', tools_count: 1, last_test_succeeded: false,
}

async function render(items: McpServer[] = []) {
  vi.mocked(service.listMcpServers).mockResolvedValue(items)
  const wrapper = mount(McpServersView, { global: { stubs: { AppIcon: true } } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('confirm', vi.fn(() => true))
})

describe('McpServersView', () => {
  it('switches transport templates and round-trips the JSON configuration mode', async () => {
    const wrapper = await render()
    await wrapper.findAll('button').find(button => button.text() === '新增服务器')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text() === 'Streamable HTTP')!.trigger('click')
    expect(wrapper.find('input[placeholder="https://example.com/mcp"]').exists()).toBe(true)

    await wrapper.findAll('button').find(button => button.text() === 'JSON 配置')!.trigger('click')
    const raw = (wrapper.get('.json-editor').element as HTMLTextAreaElement).value
    expect(JSON.parse(raw)).toMatchObject({ transport: 'streamable_http', command: null })
    expect(raw).not.toContain('secret_value')

    await wrapper.findAll('button').find(button => button.text() === '表单配置')!.trigger('click')
    expect(wrapper.text()).toContain('MCP URL')
  })

  it('rejects invalid JSON without sending a create request', async () => {
    const wrapper = await render()
    await wrapper.findAll('button').find(button => button.text() === '新增服务器')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text() === 'JSON 配置')!.trigger('click')
    await wrapper.get('.json-editor').setValue('{invalid')
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('服务器配置不是有效 JSON')
    expect(service.createMcpServer).not.toHaveBeenCalled()
  })

  it('keeps secrets request-only, exposes test failures, and confirms deletion', async () => {
    const wrapper = await render([server])
    const password = wrapper.get('input[type="password"]')
    await password.setValue('request-only-secret')
    vi.mocked(service.putMcpServerSecret).mockResolvedValue({} as never)
    await wrapper.findAll('button').find(button => button.text() === '保存')!.trigger('click')
    await flushPromises()
    expect(service.putMcpServerSecret).toHaveBeenCalledWith('server-1', 'Authorization', 'request-only-secret', 'header')
    expect((password.element as HTMLInputElement).value).toBe('')

    vi.mocked(service.testMcpServer).mockRejectedValue(new Error('连接失败'))
    await wrapper.findAll('button').find(button => button.text().includes('测试连接'))!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('连接失败')

    vi.mocked(service.deleteMcpServer).mockResolvedValue({ status: 'completed' })
    await wrapper.findAll('button').find(button => button.text().includes('删除'))!.trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalled()
    expect(service.deleteMcpServer).toHaveBeenCalledWith('server-1')
  })
})
