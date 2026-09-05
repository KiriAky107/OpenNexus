// @vitest-environment happy-dom
import { mount, flushPromises } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import { apiClient } from '@/services/apiClient'
import LocalModelSettings from './LocalModelSettings.vue'

vi.mock('@/services/apiClient', () => ({apiClient:{get:vi.fn(),post:vi.fn()}}))
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
