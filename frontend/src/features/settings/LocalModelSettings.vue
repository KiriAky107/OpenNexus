<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { apiClient } from '@/services/apiClient'
interface Config {device: 'cpu'|'cuda'; cpu_threads: number; memory_limit_mb: number; gpu_memory_limit_mb: number; timeout_seconds: number; embedding_model: string; version: number}
interface Model {key: string; name: string; capability: string; revision: string; license: string; status: string; disk_bytes: number|null; downloaded_bytes: number; total_bytes: number|null; error_code?: string}
const items = ref<Model[]>([])
const config = ref<Config | null>(null)
const installed = ref(false)
interface CudaComponent {status:string;stage:string;cuda_available:boolean|null;supported:boolean;custom_interpreter:boolean;error?:string;torch?:string}
const cuda = ref<CudaComponent|null>(null)
const cudaError = ref('')
async function loadCuda() {
  try { cuda.value = await apiClient.get<CudaComponent>('/api/local-models/runtime-components/cuda'); cudaError.value = '' }
  catch(e) { cudaError.value = (e as Error).message }
}
async function installCuda() {
  await act(async () => { cuda.value = await apiClient.post<CudaComponent>('/api/local-models/runtime-components/cuda') })
}
const lastInference = ref<{actual_device:string;requested_device:string;inference_seconds?:number;elapsed_seconds?:number;status?:string;error_code?:string}|null>(null)
const error = ref('')
const dirty = ref(false)
const busy = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let stopped = false
const size = (bytes: number | null) => bytes === null ? '未知' : `${(bytes / 1024 / 1024).toFixed(1)} MiB`
const labels: Record<string,string> = {not_installed:'未下载',downloading:'下载中',installed:'已下载并校验',failed:'下载失败',interrupted:'已中断，可续传'}
async function load() {
  await loadCuda()
  try {
    const data = await apiClient.get<{items:Model[];config:Config;runtime_installed:boolean;last_inference:typeof lastInference.value}>('/api/local-models')
    items.value = data.items; installed.value = data.runtime_installed
    lastInference.value = data.last_inference
    if (!dirty.value) config.value = data.config
  } catch (e) { error.value = (e as Error).message }
  if (!stopped) timer = setTimeout(load, 2000)
}
async function act(work: () => Promise<unknown>) {
  error.value = ''; busy.value = true
  try { await work() } catch(e) { error.value = (e as Error).message } finally { busy.value = false }
}
async function save() { await act(async () => { config.value = await apiClient.put<Config>('/api/local-models/config', config.value); dirty.value = false }) }
async function diagnostics() {
  await act(async () => {
    const data = await apiClient.get('/api/local-models/diagnostics')
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type:'application/json'}))
    const link = document.createElement('a'); link.href = url; link.download = 'local-model-diagnostics.json'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  })
}
onMounted(load)
onUnmounted(() => { stopped = true; clearTimeout(timer) })
</script>
<template>
  <section class="local-models">
    <h3>本地模型</h3><p class="subtle">默认 CPU。下载需要联网；推理只读取本地权重。文件校验通过不代表当前设备已完成推理验证。</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="lastInference" class="subtle">最近实际运行：{{ lastInference.actual_device || '未开始推理' }} · 请求设备 {{ lastInference.requested_device }} · 推理 {{ (lastInference.inference_seconds ?? lastInference.elapsed_seconds ?? 0).toFixed(2) }} 秒 · {{ lastInference.status }} {{ lastInference.error_code || '' }}</p>
    <p v-if="!installed" class="subtle">尚未安装模型运行环境。在项目根目录执行 <code>./backend/scripts/install-model-runtime.ps1</code>；CUDA 选装追加 <code>-Device cuda</code>。</p>
    <article class="item-card cuda-components" aria-label="CUDA 运行组件">
      <h4>CUDA 运行组件（可选）</h4>
      <p class="subtle">默认使用 CPU。需要 NVIDIA GPU 加速时下载此组件，约 3 GB，安装时还需要额外磁盘空间；不包含显卡驱动和模型权重。</p>
      <p v-if="cudaError" class="error-text" role="alert">{{ cudaError }} <button class="button-secondary" @click="loadCuda">重新检查</button></p>
      <template v-if="cuda">
        <p role="status">{{ cuda.stage }} {{ cuda.torch || '' }}</p>
        <progress v-if="['checking','installing'].includes(cuda.status)" aria-label="CUDA 组件安装进度" />
        <p v-if="cuda.error" class="error-text">{{ cuda.error }}</p>
        <p v-if="!cuda.supported" class="subtle">当前平台暂不支持页面安装，请使用对应平台的模型运行环境。</p>
        <button v-else-if="cuda.status !== 'installed'" class="button-primary" :disabled="busy || ['checking','installing'].includes(cuda.status)" @click="installCuda">{{ cuda.status === 'installing' ? '正在下载并安装…' : ['failed','interrupted'].includes(cuda.status) ? '重试安装 CUDA 组件' : '下载并安装 CUDA 组件' }}</button>
        <p v-if="cuda.status === 'installed'" class="subtle">{{ cuda.cuda_available ? '组件已就绪。在下方选择 CUDA 并保存即可启用。' : '组件已安装，但当前未检测到可用 CUDA 设备，将回退 CPU。' }}</p>
        <p v-if="cuda.custom_interpreter" class="subtle">当前后端设置了 APP_MODEL_PYTHON，优先使用指定环境；要使用页面安装的组件，请移除该覆盖并重启后端。</p>
      </template>
    </article>
    <form v-if="config" @submit.prevent="save" @input="dirty = true" @change="dirty = true">
      <div class="runtime-grid"><label>请求设备<select v-model="config.device" class="select"><option value="cpu">CPU（默认）</option><option value="cuda">CUDA（不可用则 CPU）</option></select></label>
      <label>Embedding<select v-model="config.embedding_model" class="select"><option value="bekko">Bekko A8M</option><option value="granite">Granite 97M 多语言</option></select></label>
      <label>CPU 线程<input v-model.number="config.cpu_threads" class="input" type="number" min="1" max="32" /></label>
      <label>内存预算 MiB<input v-model.number="config.memory_limit_mb" class="input" type="number" min="1024" max="131072" /></label>
      <label>显存预算 MiB<input v-model.number="config.gpu_memory_limit_mb" class="input" type="number" min="512" max="65536" /></label></div>
      <p class="subtle">修改 Embedding 后需要重建索引。任务按预算串行运行，模型在任务结束后释放。</p><button class="button-primary" :disabled="busy || !dirty">保存运行设置</button>
    </form>
    <div class="model-grid"><article v-for="model in items" :key="model.key" class="item-card"><h4>{{ model.name }}</h4><p>{{ model.license }} · {{ labels[model.status] || model.status }}</p><small :title="model.revision">版本 {{ model.revision.slice(0,12) }}</small>
      <p>实际磁盘占用 {{ size(model.disk_bytes) }}</p><p>{{ size(model.downloaded_bytes) }} / {{ size(model.total_bytes) }}</p><progress v-if="model.status === 'downloading' && model.total_bytes" :value="model.downloaded_bytes" :max="model.total_bytes" />
      <p v-if="model.error_code" class="error-text">{{ model.error_code }}</p><div class="inline-actions">
      <button v-if="model.status !== 'installed' && model.status !== 'downloading'" class="button-secondary" :disabled="busy" @click="act(() => apiClient.post(`/api/local-models/${model.key}/download`))">{{ model.status === 'not_installed' ? '下载模型' : '重试 / 续传' }}</button>
      <button v-if="model.status === 'downloading'" class="button-secondary" :disabled="busy" @click="act(() => apiClient.post(`/api/local-models/${model.key}/cancel`))">暂停</button>
      <button v-if="model.status !== 'not_installed'" class="button-danger" :disabled="busy" @click="act(() => apiClient.delete(`/api/local-models/${model.key}`))">删除权重</button></div>
    </article></div><button class="button-secondary" @click="diagnostics">导出最近运行诊断</button><p class="subtle">诊断仅包含模型、设备、耗时和资源信息，不包含正文、音频和密钥。</p>
  </section>
</template>
<style scoped>.local-models{display:grid;gap:16px}.runtime-grid,.model-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}label{display:grid;gap:6px}.item-card{padding:16px}progress{width:100%}</style>
