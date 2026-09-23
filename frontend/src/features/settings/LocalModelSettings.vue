<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { apiClient } from '@/services/apiClient'
import { t } from '@/i18n'
interface Config {device: 'cpu'|'cuda'; cpu_threads: number; memory_limit_mb: number; gpu_memory_limit_mb: number; timeout_seconds: number; embedding_model: string; version: number}
interface Model {key: string; name: string; capability: string; revision: string; license: string; status: string; disk_bytes: number|null; downloaded_bytes: number; total_bytes: number|null; error_code?: string}
const items = ref<Model[]>([])
const config = ref<Config | null>(null)
const installed = ref(false)
type Device = 'cpu' | 'cuda'
interface RuntimeComponent {status:string;stage:string;cuda_available:boolean|null;supported:boolean;custom_interpreter:boolean;error?:string;torch?:string}
const devices: Device[] = ['cpu', 'cuda']
const components = ref<Partial<Record<Device, RuntimeComponent>>>({})
const componentErrors = ref<Partial<Record<Device, string>>>({})
const installing = computed(() => Object.values(components.value).some(item => item?.status === 'installing'))
async function loadComponents() {
  await Promise.all(devices.map(async device => {
    try { components.value[device] = await apiClient.get<RuntimeComponent>(`/api/local-models/runtime-components/${device}`); componentErrors.value[device] = '' }
    catch(e) { componentErrors.value[device] = (e as Error).message }
  }))
}
async function installComponent(device: Device) {
  await act(async () => { components.value[device] = await apiClient.post<RuntimeComponent>(`/api/local-models/runtime-components/${device}`) })
}
const lastInference = ref<{actual_device:string;requested_device:string;inference_seconds?:number;elapsed_seconds?:number;status?:string;error_code?:string}|null>(null)
const error = ref('')
const dirty = ref(false)
const busy = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let stopped = false
const size = (bytes: number | null) => bytes === null ? t('未知', 'Unknown') : `${(bytes / 1024 / 1024).toFixed(1)} MiB`
const labels = computed<Record<string,string>>(() => ({not_installed:t('未下载','Not downloaded'),downloading:t('下载中','Downloading'),installed:t('已下载并校验','Downloaded and verified'),failed:t('下载失败','Download failed'),interrupted:t('已中断，可续传','Interrupted; resumable')}))
async function load() {
  await loadComponents()
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
    <h3>{{ t('本地模型', 'Local Models') }}</h3><p class="subtle">{{ t('默认 CPU。下载需要联网；推理只读取本地权重。文件校验通过不代表当前设备已完成推理验证。', 'CPU is the default. Downloads require network access; inference reads local weights only. File verification does not mean the current device passed inference validation.') }}</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="lastInference" class="subtle">{{ t('最近实际运行：', 'Last actual run: ') }}{{ lastInference.actual_device || t('未开始推理', 'No inference yet') }} · {{ t('请求设备', 'requested device') }} {{ lastInference.requested_device }} · {{ t('推理', 'inference') }} {{ (lastInference.inference_seconds ?? lastInference.elapsed_seconds ?? 0).toFixed(2) }} {{ t('秒', 'sec') }} · {{ lastInference.status }} {{ lastInference.error_code || '' }}</p>
    <p v-if="!installed" class="subtle">{{ t('请在下方安装 CPU 运行组件，或为 NVIDIA 显卡安装 CUDA 组件。安装包已自带安装器，无需预装 uv 或 Python。安装完成后可重建索引。', 'Install the CPU runtime below, or the CUDA runtime for an NVIDIA GPU. The app includes its installer; no preinstalled uv or Python is required. Rebuild the index after installation.') }}</p>
    <article v-for="device in devices" :key="device" class="item-card runtime-components" :aria-label="device.toUpperCase() + t(' 运行组件', ' runtime components')">
      <h4>{{ device === 'cpu' ? t('CPU 运行组件（推荐）', 'CPU Runtime (Recommended)') : t('CUDA 运行组件（可选）', 'CUDA Runtime (Optional)') }}</h4>
      <p class="subtle">{{ device === 'cpu'
        ? t('适用于无独立显卡的设备。首次安装需要联网下载 Python 和模型依赖；模型权重另行下载。', 'Works without a dedicated GPU. Initial installation downloads Python and model dependencies; model weights are downloaded separately.')
        : t('用于 NVIDIA GPU 加速，约 3 GB，安装需要额外磁盘空间；不包含显卡驱动和模型权重。CUDA 环境也支持 CPU 推理，无需重复安装 CPU 组件。', 'For NVIDIA GPU acceleration, about 3 GB plus installation space. Drivers and weights are not included. This runtime also supports CPU inference; installing both is unnecessary.') }}</p>
      <p v-if="componentErrors[device]" class="error-text" role="alert">{{ componentErrors[device] }} <button class="button-secondary" @click="loadComponents">{{ t('重新检查', 'Check again') }}</button></p>
      <template v-if="components[device]">
        <p role="status">{{ components[device]?.stage }} {{ components[device]?.torch || '' }}</p>
        <progress v-if="['checking','installing'].includes(components[device]!.status)" :aria-label="device.toUpperCase() + t(' 组件安装进度', ' installation progress')" />
        <p v-if="components[device]?.error" class="error-text" role="alert">{{ components[device]?.error }}</p>
        <p v-if="!components[device]?.supported" class="subtle">{{ t('当前平台暂不支持页面安装，请使用对应平台的模型运行环境。', 'This platform does not support in-app installation. Use the model runtime for your platform.') }}</p>
        <p v-else-if="device === 'cpu' && components.cuda?.status === 'installed' && components.cpu?.status !== 'installed'" class="subtle">{{ t('已安装的 CUDA 组件可用于 CPU，无需重复下载。', 'The installed CUDA runtime supports CPU; no additional download is needed.') }}</p>
        <button v-else-if="components[device]?.status !== 'installed'" class="button-primary" :disabled="busy || installing || components[device]?.status === 'checking'" @click="installComponent(device)">{{ components[device]?.status === 'installing' ? t('正在下载并安装…', 'Downloading and installing…') : ['failed','interrupted'].includes(components[device]!.status) ? t('重试安装 ', 'Retry installation: ') + device.toUpperCase() : t('下载并安装 ', 'Download and install ') + device.toUpperCase() + t(' 组件', ' components') }}</button>
        <p v-if="components[device]?.status === 'installed'" class="subtle">{{ device === 'cpu'
          ? t('CPU 环境已就绪，可保存运行设置并重建索引。', 'CPU runtime is ready. Save the runtime settings and rebuild the index.')
          : components[device]?.cuda_available ? t('组件已就绪。在下方选择 CUDA 并保存即可启用。', 'Components are ready. Select CUDA below and save to enable it.') : t('组件已安装，但当前未检测到可用 CUDA 设备，将回退 CPU。', 'Components are installed, but no CUDA device is available; CPU fallback will be used.') }}</p>
        <p v-if="components[device]?.custom_interpreter" class="subtle">{{ t('当前设置了 APP_MODEL_PYTHON，优先使用指定环境；使用页面安装的组件前，请移除该覆盖并重启后端。', 'APP_MODEL_PYTHON takes priority. Remove the override and restart the backend to use the installed components.') }}</p>
      </template>
    </article>
    <form v-if="config" @submit.prevent="save" @input="dirty = true" @change="dirty = true">
      <div class="runtime-grid"><label>{{ t('请求设备', 'Requested device') }}<select v-model="config.device" class="select"><option value="cpu">{{ t('CPU（默认）', 'CPU (default)') }}</option><option value="cuda">{{ t('CUDA（不可用则 CPU）', 'CUDA (CPU fallback)') }}</option></select></label>
      <label>Embedding<select v-model="config.embedding_model" class="select"><option value="bekko">Bekko A8M</option><option value="granite">Granite 97M {{ t('多语言', 'Multilingual') }}</option></select></label>
      <label>{{ t('CPU 线程', 'CPU threads') }}<input v-model.number="config.cpu_threads" class="input" type="number" min="1" max="32" /></label>
      <label>{{ t('内存预算 MiB', 'Memory budget MiB') }}<input v-model.number="config.memory_limit_mb" class="input" type="number" min="1024" max="131072" /></label>
      <label>{{ t('显存预算 MiB', 'GPU memory budget MiB') }}<input v-model.number="config.gpu_memory_limit_mb" class="input" type="number" min="512" max="65536" /></label></div>
      <p class="subtle">{{ t('修改 Embedding 后需要重建索引。任务按预算串行运行，模型在任务结束后释放。', 'Changing the embedding model requires rebuilding the index. Jobs run serially within the resource budget, and models are released when each job finishes.') }}</p><button class="button-primary" :disabled="busy || !dirty">{{ t('保存运行设置', 'Save runtime settings') }}</button>
    </form>
    <div class="model-grid"><article v-for="model in items" :key="model.key" class="item-card"><h4>{{ model.name }}</h4><p>{{ model.license }} · {{ labels[model.status] || model.status }}</p><small :title="model.revision">{{ t('版本', 'Revision') }} {{ model.revision.slice(0,12) }}</small>
      <p>{{ t('实际磁盘占用', 'Disk usage') }} {{ size(model.disk_bytes) }}</p><p>{{ size(model.downloaded_bytes) }} / {{ size(model.total_bytes) }}</p><progress v-if="model.status === 'downloading' && model.total_bytes" :value="model.downloaded_bytes" :max="model.total_bytes" />
      <p v-if="model.error_code" class="error-text">{{ model.error_code }}</p><div class="inline-actions">
      <button v-if="model.status !== 'installed' && model.status !== 'downloading'" class="button-secondary" :disabled="busy" @click="act(() => apiClient.post(`/api/local-models/${model.key}/download`))">{{ model.status === 'not_installed' ? t('下载模型', 'Download model') : t('重试 / 续传', 'Retry / Resume') }}</button>
      <button v-if="model.status === 'downloading'" class="button-secondary" :disabled="busy" @click="act(() => apiClient.post(`/api/local-models/${model.key}/cancel`))">{{ t('暂停', 'Pause') }}</button>
      <button v-if="model.status !== 'not_installed'" class="button-danger" :disabled="busy" @click="act(() => apiClient.delete(`/api/local-models/${model.key}`))">{{ t('删除权重', 'Delete weights') }}</button></div>
    </article></div><button class="button-secondary" @click="diagnostics">{{ t('导出最近运行诊断', 'Export recent runtime diagnostics') }}</button><p class="subtle">{{ t('诊断仅包含模型、设备、耗时和资源信息，不包含正文、音频和密钥。', 'Diagnostics include only model, device, timing, and resource data. Note content, audio, and secrets are excluded.') }}</p>
  </section>
</template>
<style scoped>.local-models{display:grid;gap:16px}.runtime-grid,.model-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}label{display:grid;gap:6px}.item-card{padding:16px}progress{width:100%}</style>
