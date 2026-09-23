// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { apiClient } from '@/services/apiClient'
import LocalModelSettings from './LocalModelSettings.vue'

vi.mock('@/services/apiClient', () => ({apiClient:{get:vi.fn(),post:vi.fn()}}))
beforeEach(() => vi.clearAllMocks())
it('shows an optional CUDA installer and live installation stage', async () => {
  vi.mocked(apiClient.get).mockImplementation(async (url) => url.includes('runtime-components')
    ? {status:'not_installed', stage:'尚未安装', supported:true, cuda_available:null,custom_interpreter:false}
    : {items:[],config:null,runtime_installed:true,last_inference:null})
  vi.mocked(apiClient.post).mockResolvedValue({status:'installing',stage:'下载并安装 PyTorch CUDA（约 3 GB）',supported:true})
  const wrapper = mount(LocalModelSettings)
  try {
    await flushPromises()
    const button = wrapper.findAll('button').find(b => b.text() === '下载并安装 CUDA 组件')!
    expect(button.exists()).toBe(true)
    expect(apiClient.post).not.toHaveBeenCalled()
    await button.trigger('click')
    await flushPromises()
    expect(apiClient.post).toHaveBeenCalledWith('/api/local-models/runtime-components/cuda')
    expect(wrapper.text()).toContain('下载并安装 PyTorch CUDA')
    expect(wrapper.get('progress').attributes('value')).toBeUndefined()
    expect(button.attributes('disabled')).toBeDefined()
  } finally {wrapper.unmount()}
})

it('offers CPU installation on a clean machine without source checkout instructions', async () => {
  vi.mocked(apiClient.get).mockImplementation(async (url) => url.includes('runtime-components')
    ? {status:'not_installed', stage:'尚未安装', supported:true, cuda_available:null,custom_interpreter:false}
    : {items:[],config:null,runtime_installed:false,last_inference:null})
  vi.mocked(apiClient.post).mockResolvedValue({status:'installing',stage:'下载并安装独立 Python',supported:true})
  const wrapper = mount(LocalModelSettings)
  try {
    await flushPromises()
    expect(wrapper.text()).not.toContain('./backend/scripts')
    expect(wrapper.text()).toContain('无需预装 uv 或 Python')
    const button = wrapper.findAll('button').find(b => b.text() === '下载并安装 CPU 组件')!
    await button.trigger('click')
    await flushPromises()
    expect(apiClient.post).toHaveBeenCalledWith('/api/local-models/runtime-components/cpu')
    expect(wrapper.text()).toContain('下载并安装独立 Python')
    const cuda = wrapper.findAll('button').find(b => b.text() === '下载并安装 CUDA 组件')!
    expect(cuda.attributes('disabled')).toBeDefined()
  } finally {wrapper.unmount()}
})

it('reuses installed CUDA for CPU instead of requiring another download', async () => {
  vi.mocked(apiClient.get).mockImplementation(async (url) => url.endsWith('/cuda')
    ? {status:'installed', stage:'组件已安装', supported:true, cuda_available:true}
    : url.endsWith('/cpu') ? {status:'not_installed', stage:'尚未安装', supported:true}
    : {items:[],config:null,runtime_installed:true,last_inference:null})
  const wrapper = mount(LocalModelSettings)
  try {
    await flushPromises()
    expect(wrapper.text()).toContain('已安装的 CUDA 组件可用于 CPU')
    expect(wrapper.findAll('button').some(b => b.text() === '下载并安装 CPU 组件')).toBe(false)
    expect(apiClient.post).not.toHaveBeenCalled()
  } finally {wrapper.unmount()}
})
