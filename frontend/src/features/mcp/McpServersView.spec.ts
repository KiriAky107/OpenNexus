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

  it('confirms permission changes before updating an existing server', async () => {
    const wrapper = await render([server])
    vi.mocked(service.updateMcpServer).mockResolvedValue(server)
    await wrapper.findAll('button').find(button => button.text().includes('编辑'))!.trigger('click')
    await wrapper.get('input[placeholder="network.request, notes.read"]').setValue('notes.read')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('旧测试与授权会失效'))
    expect(service.updateMcpServer).toHaveBeenCalled()
  })

  it('saves an environment API key via the encrypted endpoint, not the config body', async () => {
    const wrapper = await render()
    vi.mocked(service.createMcpServer).mockResolvedValue({ ...server, server_id: 'new-server' })
    vi.mocked(service.putMcpServerSecret).mockResolvedValue({})
    await wrapper.findAll('button').find(button => button.text() === '新增服务器')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text() === 'JSON 配置')!.trigger('click')
    await wrapper.get('.json-editor').setValue(JSON.stringify({ command: 'uvx', environment: { MINIMAX_API_KEY: 'synthetic-only' }, secret_environment_keys: ['MINIMAX_API_KEY'] }))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.createMcpServer).toHaveBeenCalledWith(expect.objectContaining({ environment: {}, secret_environment_keys: ['MINIMAX_API_KEY'] }))
    expect(JSON.stringify(vi.mocked(service.createMcpServer).mock.calls)).not.toContain('synthetic-only')
    expect(service.putMcpServerSecret).toHaveBeenCalledWith('new-server', 'MINIMAX_API_KEY', 'synthetic-only', 'environment')
    expect(wrapper.find('.modal-backdrop').exists()).toBe(false)
  })

  it('retains imported keys over mode switches and retries partial saves without duplicates', async () => {
    const wrapper = await render()
    vi.mocked(service.createMcpServer).mockResolvedValue({ ...server, server_id: 'new-server', version: 1 })
    vi.mocked(service.updateMcpServer).mockResolvedValue({ ...server, server_id: 'new-server', version: 2 })
    vi.mocked(service.putMcpServerSecret).mockRejectedValueOnce(new Error('credential store unavailable')).mockResolvedValue({})
    await wrapper.findAll('button').find(button => button.text() === '新增服务器')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text() === 'JSON 配置')!.trigger('click')
    await wrapper.get('.json-editor').setValue(JSON.stringify({ command: 'uvx', env: { API_KEY: 'retry-value' } }))
    await wrapper.findAll('button').find(button => button.text() === '表单配置')!.trigger('click')
    expect(wrapper.text()).toContain('已识别 1 项密钥')
    await wrapper.findAll('button').find(button => button.text() === 'JSON 配置')!.trigger('click')
    expect((wrapper.get('.json-editor').element as HTMLTextAreaElement).value).not.toContain('retry-value')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('.modal-card [role="alert"]').text()).toContain('服务器配置已保存，但密钥保存失败')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.createMcpServer).toHaveBeenCalledTimes(1)
    expect(service.updateMcpServer).toHaveBeenCalledWith('new-server', expect.objectContaining({ version: 1 }))
    expect(service.putMcpServerSecret).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.modal-backdrop').exists()).toBe(false)
  })

  it('clears staged keys on cancel and accepts minimal JSON while editing', async () => {
    const wrapper = await render([server])
    await wrapper.findAll('button').find(button => button.text() === '新增服务器')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text() === 'JSON 配置')!.trigger('click')
    await wrapper.get('.json-editor').setValue('{"command":"uvx","env":{"API_KEY":"cancelled-value"}}')
    await wrapper.findAll('button').find(button => button.text() === '表单配置')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text() === '取消')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text().includes('编辑'))!.trigger('click')
    await wrapper.findAll('button').find(button => button.text() === 'JSON 配置')!.trigger('click')
    await wrapper.get('.json-editor').setValue('{"name":"Minimal","url":"https://example.test/mcp"}')
    vi.mocked(service.updateMcpServer).mockResolvedValue(server)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.updateMcpServer).toHaveBeenCalledWith('server-1', expect.objectContaining({ version: 2, headers: {}, args: [] }))
    expect(service.putMcpServerSecret).not.toHaveBeenCalled()
  })

  it('saves an imported Header secret after a case-only declaration rename', async () => {
    const wrapper = await render()
    vi.mocked(service.createMcpServer).mockResolvedValue({ ...server, secret_headers: { authorization: false } })
    vi.mocked(service.putMcpServerSecret).mockResolvedValue({})
    await wrapper.findAll('button').find(button => button.text() === '新增服务器')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text() === 'JSON 配置')!.trigger('click')
    await wrapper.get('.json-editor').setValue(JSON.stringify({ url: 'https://example.test/mcp', headers: { Authorization: 'synthetic-draft' } }))
    await wrapper.findAll('button').find(button => button.text() === '表单配置')!.trigger('click')
    await wrapper.get('textarea[placeholder="Authorization"]').setValue('authorization')
    await wrapper.findAll('button').find(button => button.text() === 'JSON 配置')!.trigger('click')
    expect(wrapper.text()).toContain('已识别 1 项密钥')
    expect((wrapper.get('.json-editor').element as HTMLTextAreaElement).value).not.toContain('synthetic-draft')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(service.createMcpServer).toHaveBeenCalledWith(expect.objectContaining({ headers: {}, secret_header_keys: ['authorization'] }))
    expect(service.putMcpServerSecret).toHaveBeenCalledWith('server-1', 'authorization', 'synthetic-draft', 'header')
    expect(wrapper.find('.modal-backdrop').exists()).toBe(false)
  })
})
