<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import type { ModelBinding, ModelRoutingConfig, ModelRoutingResponse, ProviderConfig, RoutingCapability } from '@/contracts'
import { getModelRouting, saveModelRouting } from '@/services/modelRoutingService'
import { listProviders } from '@/services/providerService'
import { ApiErrorClass } from '@/services/apiClient'
import { t } from '@/i18n'

const capabilities = computed<Array<{ id: RoutingCapability; name: string; endpoint: string; placeholder: string; local: string }>>(() => [
  { id: 'embedding', name: t('向量嵌入 · Embedding', 'Embedding'), endpoint: '/embeddings', placeholder: t('例如 text-embedding-3-small', 'For example, text-embedding-3-small'), local: t('本地支持 Bekko / Granite，安装权重后可离线运行。', 'Local Bekko / Granite can run offline after weights are installed.') },
  { id: 'transcription', name: t('语音转写 · Transcription', 'Transcription'), endpoint: '/audio/transcriptions', placeholder: t('输入转写模型 ID', 'Enter a transcription model ID'), local: t('本地采用 Qwen3-ASR 0.6B，默认 CPU。', 'Local Qwen3-ASR 0.6B uses CPU by default.') },
  { id: 'speaker_matching', name: t('说话人匹配 · Speaker matching', 'Speaker matching'), endpoint: '/audio/speaker-matches', placeholder: t('输入说话人匹配模型 ID', 'Enter a speaker matching model ID'), local: t('本地采用 ERes2NetV2，比对结果是相似度。', 'Local ERes2NetV2 returns a similarity score.') },
])
type Draft = { provider_id: string; model: string; endpoint: string; dimensions: string | number }
const drafts = reactive(Object.fromEntries(capabilities.value.map(item => [item.id, { provider_id: '', model: '', endpoint: item.endpoint, dimensions: '' }])) as Record<RoutingCapability, Draft>)
const providers = ref<ProviderConfig[]>([])
const response = ref<ModelRoutingResponse | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const saved = ref(false)
const conflict = ref(false)
let active = true
const eligible = (provider: ProviderConfig) => provider.enabled && ['openai_chat', 'openai_compatible'].includes(provider.provider_type)
const available = computed(() => providers.value.filter(eligible))
const unavailable = computed(() => providers.value.filter(provider => !eligible(provider)))
const localBackend = (capability: RoutingCapability) => response.value?.local_backends.find(item => item.capability === capability)
const localLabel = (capability: RoutingCapability) => {
  const status = localBackend(capability)?.status
  return status === 'ready' ? t('已安装', 'Installed') : status === 'placeholder' ? t('测试占位实现', 'Test placeholder') : t('未安装', 'Not installed')
}
const protocols = [
  { id: 'openai_chat', label: 'OpenAI Chat' }, { id: 'openai_compatible', label: 'OpenAI Compatible' },
  { id: 'openai_responses', label: 'Responses' }, { id: 'anthropic_messages', label: 'Anthropic' }, { id: 'ollama', label: 'Ollama' },
]

function applyResponse(result: ModelRoutingResponse) {
  response.value = result
  for (const item of capabilities.value) {
    const binding = result.config[item.id]
    Object.assign(drafts[item.id], { provider_id: binding?.provider_id ?? '', model: binding?.model ?? '', endpoint: binding?.endpoint ?? item.endpoint, dimensions: binding?.dimensions?.toString() ?? '' })
  }
}

async function load() {
  if (loading.value || saving.value) return
  loading.value = true
  error.value = ''
  saved.value = false
  try {
    const [routing, items] = await Promise.all([getModelRouting(), listProviders()])
    if (!active) return
    providers.value = items
    applyResponse(routing)
    conflict.value = false
  } catch (reason) {
    if (active) error.value = `${t('加载失败：', 'Load failed: ')}${reason instanceof Error ? reason.message : t('无法读取模型路由或提供商', 'Could not read model routes or providers')}`
  } finally { loading.value = false }
}

onMounted(load)
onBeforeUnmount(() => { active = false })

function changeProvider(capability: RoutingCapability) {
  const draft = drafts[capability]
  draft.model = ''
  draft.dimensions = ''
  draft.endpoint = capabilities.value.find(item => item.id === capability)!.endpoint
  saved.value = false
}

function bindingFor(capability: RoutingCapability): ModelBinding | null {
  const draft = drafts[capability]
  if (!draft.provider_id) return null
  if (!available.value.some(provider => provider.provider_id === draft.provider_id)) throw new Error(t('请选择已启用且协议可用的提供商，或切换到本地。', 'Select an enabled provider with a supported protocol, or switch to local.'))
  if (!draft.model.trim()) throw new Error(t('请填写所选 API 的模型 ID。', 'Enter the model ID for the selected API.'))
  if (!/^\/[A-Za-z0-9_/-]+$/.test(draft.endpoint) || draft.endpoint.startsWith('//')) throw new Error(t('Endpoint 必须是以 / 开头的相对路径，只能包含字母、数字、下划线、连字符和 /。', 'Endpoint must be a relative path beginning with / and containing only letters, numbers, underscores, hyphens, and /.'))
  const binding: ModelBinding = { provider_id: draft.provider_id, model: draft.model.trim(), endpoint: draft.endpoint }
  if (capability === 'embedding') {
    const dimension = String(draft.dimensions).trim()
    if (dimension && (!/^\d+$/.test(dimension) || !Number.isSafeInteger(Number(dimension)) || Number(dimension) < 1 || Number(dimension) > 16384)) throw new Error(t('嵌入维度必须为 1–16384 的整数，或留空使用 API 默认值。', 'Embedding dimensions must be an integer from 1 to 16384, or blank to use the API default.'))
    binding.dimensions = dimension ? Number(dimension) : null
  }
  return binding
}

async function save() {
  if (!response.value || loading.value || saving.value || conflict.value) return
  saving.value = true
  error.value = ''
  saved.value = false
  try {
    const config: ModelRoutingConfig = {
      version: response.value.config.version,
      embedding: bindingFor('embedding'), transcription: bindingFor('transcription'), speaker_matching: bindingFor('speaker_matching'),
    }
    const result = await saveModelRouting(config)
    if (active) { applyResponse(result); saved.value = true }
  } catch (reason) {
    if (!active) return
    conflict.value = reason instanceof ApiErrorClass && /CONFLICT|VERSION|HTTP_409/i.test(reason.code)
    error.value = conflict.value
      ? t('配置版本冲突：其他窗口已修改路由。当前输入尚未保存，请重新加载最新配置后再编辑。', 'Configuration conflict: another window changed these routes. Your input is unsaved; reload the latest settings before editing.')
      : `${t('保存失败：', 'Save failed: ')}${reason instanceof Error ? reason.message : t('请重试', 'please retry')}`
  } finally { saving.value = false }
}
</script>

<template>
  <section class="routing-settings" aria-labelledby="routing-title" :aria-busy="loading || saving">
    <div><h2 id="routing-title">{{ t('能力模型路由', 'Capability model routing') }}</h2><p class="subtle">{{ t('向量嵌入、语音转写和说话人匹配分别选择提供商与模型，独立于默认聊天模型。API Key 在「模型提供商」中管理。', 'Choose providers and models separately for embeddings, transcription, and speaker matching. API keys are managed under Model Providers.') }}</p></div>
    <p class="subtle">{{ t('未选择提供商即使用本地模型。API 请求失败、配置不可用或响应无效时回退到本地；使用前请下载对应权重并安装运行环境。', 'With no provider selected, the local model is used. Failed API requests, invalid settings, or invalid responses fall back to local. Download the required weights and runtime first.') }}</p>
    <p v-if="loading" role="status">{{ t('正在加载模型路由…', 'Loading model routes…') }}</p>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div class="inline-actions"><button type="button" class="button-secondary" :disabled="loading || saving" @click="load">{{ conflict ? t('放弃当前输入并加载最新配置', 'Discard input and load latest settings') : response ? t('重新加载（放弃未保存更改）', 'Reload (discard unsaved changes)') : t('重试加载', 'Retry loading') }}</button><span v-if="response" class="subtle">{{ t('配置版本', 'Configuration version') }} {{ response.config.version }}</span></div>
    <form v-if="response" @submit.prevent="save" @input="saved = false" @change="saved = false">
      <fieldset :disabled="loading || saving || conflict">
        <article v-for="capability in capabilities" :key="capability.id" class="routing-card" :data-capability="capability.id">
          <h3>{{ capability.name }}</h3>
          <p v-if="capability.id === 'embedding'" class="embedding-notice">{{ t('保存配置或更换模型、接口后，请重建全部索引。配置成功不代表已有笔记的向量索引已更新；重建完成前可使用全文检索，混合检索会回退到全文检索。', 'Rebuild all indexes after saving or changing the model or endpoint. Saving settings does not update existing note vectors. Full-text search remains available, and hybrid search falls back to it until rebuilding completes.') }}</p>
          <div class="protocols" :aria-label="t('协议可用性', 'Protocol availability')">
            <span v-for="protocol in protocols" :key="protocol.id" class="badge" :class="{ 'protocol-unavailable': !['openai_chat', 'openai_compatible'].includes(protocol.id) }">{{ protocol.label }}{{ ['openai_chat', 'openai_compatible'].includes(protocol.id) ? t(' · 可用', ' · Available') : t(' · 不可用', ' · Unavailable') }}</span>
          </div>
          <label class="field"><span>{{ t('处理方式 / 提供商', 'Processing / Provider') }}</span><select v-model="drafts[capability.id].provider_id" class="select" data-field="provider" @change="changeProvider(capability.id)">
            <option value="">{{ t('本地', 'Local') }} · {{ localLabel(capability.id) }}</option>
            <option v-for="provider in available" :key="provider.provider_id" :value="provider.provider_id">{{ provider.name }} · {{ provider.provider_type }}</option>
            <option v-for="provider in unavailable" :key="provider.provider_id" :value="provider.provider_id" disabled>{{ provider.name }} · {{ provider.enabled ? t('协议不可用', 'Protocol unavailable') : t('未启用', 'Disabled') }}</option>
            <option v-if="drafts[capability.id].provider_id && !providers.some(provider => provider.provider_id === drafts[capability.id].provider_id)" :value="drafts[capability.id].provider_id" disabled>{{ t('原提供商已不可用', 'Previous provider is unavailable') }} · {{ drafts[capability.id].provider_id }}</option>
          </select></label>
          <div v-if="drafts[capability.id].provider_id" class="routing-fields">
            <label class="field"><span>{{ t('模型 ID', 'Model ID') }}</span><input v-model="drafts[capability.id].model" class="input" data-field="model" :placeholder="capability.placeholder" maxlength="256" required /></label>
            <label class="field"><span>{{ t('Endpoint（相对 Base URL）', 'Endpoint (relative to Base URL)') }}</span><input v-model="drafts[capability.id].endpoint" class="input" data-field="endpoint" :placeholder="capability.endpoint" maxlength="256" required /></label>
            <label v-if="capability.id === 'embedding'" class="field"><span>{{ t('向量维度（可选）', 'Vector dimensions (optional)') }}</span><input v-model="drafts.embedding.dimensions" class="input" data-field="dimensions" type="number" min="1" max="16384" step="1" :placeholder="t('留空使用 API 默认维度', 'Blank uses the API default')" /><small class="subtle">{{ t('填写模型支持的 1–16384 整数维度，或留空使用 API 默认值。', 'Enter an integer from 1 to 16384 supported by the model, or leave blank for the API default.') }}</small></label>
          </div>
          <p v-if="capability.id === 'speaker_matching'" class="subtle">{{ t('说话人匹配使用本应用自定义 HTTP multipart 契约。该端点不是 OpenAI 标准接口；服务需实现对应的说话人匹配请求和响应。', 'Speaker matching uses this app’s custom HTTP multipart contract. It is not an OpenAI-standard endpoint; the service must implement the corresponding request and response.') }}</p>
          <div class="local-status" :class="{ selected: !drafts[capability.id].provider_id }">
            <strong>{{ drafts[capability.id].provider_id ? t('本地回退状态', 'Local fallback status') : t('当前本地状态', 'Current local status') }}</strong>
            <p>{{ localBackend(capability.id)?.status === 'ready' ? t('本地后端已就绪。', 'The local backend is ready.') : capability.local }}</p>
            <p v-for="backend in response.local_backends.filter(item => item.capability === capability.id)" :key="backend.capability" class="subtle"><span class="badge">{{ backend.status === 'ready' ? t('已就绪', 'Ready') : backend.status === 'placeholder' ? t('占位实现', 'Placeholder') : t('未安装 / 未接入', 'Not installed / connected') }}</span> {{ backend.message }}</p>
          </div>
        </article>
      </fieldset>
      <div class="inline-actions"><button type="submit" class="button-primary" :disabled="loading || saving || conflict">{{ saving ? t('保存中…', 'Saving…') : t('保存模型路由', 'Save model routes') }}</button><span v-if="saved" role="status">{{ t('模型路由已保存。', 'Model routes saved.') }}</span></div>
    </form>
  </section>
</template>

<style scoped>
.routing-settings, form, fieldset { display: grid; gap: var(--space-lg); }
.routing-settings { border-top: 1px solid var(--color-border-default); padding-top: var(--space-xl); margin-top: var(--space-md); }
fieldset { min-width: 0; padding: 0; margin: 0; border: 0; }
.routing-card { display: grid; gap: var(--space-md); padding: var(--space-lg); border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-surface-primary); }
.routing-card h3 { margin: 0; }
.protocols { display: flex; flex-wrap: wrap; gap: var(--space-xs); }
.protocol-unavailable { opacity: .65; }
.embedding-notice { padding: var(--space-md); border-radius: var(--radius-md); background: var(--color-accent-soft); color: var(--color-text-primary); }
.routing-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-md); }
.local-status { display: grid; gap: var(--space-xs); padding: var(--space-md); background: var(--color-background-secondary); border-radius: var(--radius-md); }
.local-status.selected { border-left: 3px solid var(--color-accent-primary); }
@media (max-width: 700px) { .routing-fields { grid-template-columns: 1fr; } }
</style>
