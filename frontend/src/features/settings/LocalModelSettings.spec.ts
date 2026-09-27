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
    expect(apiClient.post).toHaveBeenCalledWith('/api/local-models/runtime-components/cuda?source=domestic')
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
    expect(apiClient.post).toHaveBeenCalledWith('/api/local-models/runtime-components/cpu?source=domestic')
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

it('can recheck a copied runtime and copied ASR weights', async () => {
  vi.mocked(apiClient.get).mockImplementation(async (url) => url.includes('runtime-components')
    ? {status:'failed', stage:'组件安装或验证失败', supported:true, cuda_available:null,
        custom_interpreter:false, install_path:'C:/data/model-runtime'}
    : {items:[{key:'qwen3-asr',name:'Qwen3 ASR 0.6B',license:'Apache-2.0',
        revision:'5eb144179a02acc5e5ba31e748d22b0cf3e303b0',status:'failed',
        disk_bytes:0,downloaded_bytes:0,total_bytes:null,error_code:'MODEL_DOWNLOAD_FAILED',
        download_sources:['domestic','official'],install_path:'C:/data/models/qwen3-asr'}],
      config:null,runtime_installed:false,last_inference:null})
  vi.mocked(apiClient.post).mockResolvedValue({status:'checking',stage:'重新检测 CUDA 组件'})
  const wrapper = mount(LocalModelSettings)
  try {
    await flushPromises()
    expect(wrapper.text()).toContain('国内镜像（默认）')
    expect(wrapper.get('.download-source.surface-nested select').classes()).toContain('select')
    expect(wrapper.findAll('.path-disclosure.ui-disclosure')).toHaveLength(3)
    expect(wrapper.get('.model-card .badge.error').text()).toContain('下载或校验失败')
    const downloadModel = wrapper.findAll('button').find(b => b.text() === '重试 / 续传')!
    await downloadModel.trigger('click')
    await flushPromises()
    expect(apiClient.post).toHaveBeenCalledWith('/api/local-models/qwen3-asr/download?source=domestic')
    const verifyModel = wrapper.findAll('button').find(b => b.text() === '校验本地文件')!
    await verifyModel.trigger('click')
    await flushPromises()
    expect(apiClient.post).toHaveBeenCalledWith('/api/local-models/qwen3-asr/verify')
    const verifyRuntime = wrapper.findAll('button').filter(b => b.text() === '重新检测本地环境')[1]!
    await verifyRuntime.trigger('click')
    expect(apiClient.post).toHaveBeenCalledWith('/api/local-models/runtime-components/cuda/verify')
  } finally {wrapper.unmount()}
})
