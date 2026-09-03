// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Plugin } from '@/contracts'
import * as pluginService from '@/services/pluginService'
import PluginMcpPanel from './PluginMcpPanel.vue'
import { useWorkspaceStore } from '@/stores/workspace'

vi.mock('@/services/pluginService', async (loadOriginal) => {
  const original = await loadOriginal<typeof import('@/services/pluginService')>()
  return {
    ...original,
    getPluginHostStatus: vi.fn(),
    restartPluginHost: vi.fn(),
    getPluginSettings: vi.fn(),
    updatePluginSettings: vi.fn(),
    putPluginSecret: vi.fn(),
    deletePluginSecret: vi.fn(),
    listPluginCommands: vi.fn(),
    executePluginCommand: vi.fn(),
  }
})

const plugin: Plugin = {
  plugin_id: 'mcp-demo',
  name: 'MCP Demo',
  version: '1.0.0',
  description: 'demo',
  status: 'ready',
  enabled: true,
  permissions: [],
  contributions: [
    { type: 'settings_section', id: 'mcp-demo.general', name: 'settings' },
    { type: 'command', id: 'mcp-demo.run', name: 'run' },
  ],
  backend_type: 'mcp',
  transport: 'stdio',
}

async function render() {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  await router.push('/')
  const wrapper = mount(PluginMcpPanel, {
    props: { plugin },
    global: { plugins: [pinia, router], stubs: { AppIcon: true } },
  })
  const workspaceStore = useWorkspaceStore(pinia)
  workspaceStore.vaultId = 'default'
  workspaceStore.hasVault = true
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(pluginService.getPluginHostStatus).mockResolvedValue({
    plugin_id: 'mcp-demo', backend_type: 'mcp', transport: 'stdio',
    status: 'ready', tools_count: 2, server_name: 'demo',
  })
  vi.mocked(pluginService.getPluginSettings).mockResolvedValue({
    plugin_id: 'mcp-demo',
    schema_version: 1,
    fields: [
      { key: 'limit', label: '数量', description: '', type: 'number', required: true, options: [] },
      { key: 'api_key', label: 'API Key', description: '', type: 'secret', required: true, options: [] },
    ],
    values: { limit: 5 },
    secrets: { api_key: { configured: false } },
  })
  vi.mocked(pluginService.putPluginSecret).mockResolvedValue({
    plugin_id: 'mcp-demo', key: 'api_key', configured: true,
  })
  vi.mocked(pluginService.listPluginCommands).mockResolvedValue([])
})

describe('PluginMcpPanel', () => {
  it('loads MCP Host status and exposes restart controls', async () => {
    const wrapper = await render()
    await flushPromises()
    expect(pluginService.getPluginHostStatus).toHaveBeenCalledWith('mcp-demo')
    expect(wrapper.text()).toContain('demo')
    expect(wrapper.text()).toContain('工具数量')
  })

  it('builds settings fields from schema and writes secrets separately', async () => {
    const wrapper = await render()
    const settingsTab = wrapper.findAll('button').find((button) => button.text() === '设置与密钥')
    expect(settingsTab).toBeTruthy()
    await settingsTab!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('数量')
    expect(wrapper.text()).toContain('API Key')

    await wrapper.get('input[type="password"]').setValue('secret-only-in-request')
    const secretButton = wrapper.findAll('button').find((button) => button.text() === '安全保存')
    expect(secretButton).toBeTruthy()
    await secretButton!.trigger('click')
    await flushPromises()

    expect(pluginService.putPluginSecret).toHaveBeenCalledWith('mcp-demo', 'api_key', 'secret-only-in-request')
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('已配置')
  })
})
