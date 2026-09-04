<script setup lang="ts">
import { computed, watch, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import type { ModelInfo, ProviderConfig, ProviderPreset, ProviderType, RequestOverride } from '@/contracts'
import * as service from '@/services/providerService'
import ProviderPresetSelector from './ProviderPresetSelector.vue'
import RequestJsonEditor from './RequestJsonEditor.vue'
import { apiClient } from '@/services/apiClient'

const props = defineProps<{ provider?: ProviderConfig; models?: ModelInfo[] }>()
const emit = defineEmits<{ close: []; saved: [provider: ProviderConfig] }>()
const newCredentialId = () => `provider-key-${crypto.randomUUID()}`
const form = reactive({
  preset_id: '', provider_type: props.provider?.provider_type ?? 'openai_compatible' as ProviderType,
  name: props.provider?.name ?? '', base_url: props.provider?.base_url ?? '',
  default_model: props.provider?.default_model ?? '', enabled: props.provider?.enabled ?? true,
})
const credentialId = ref(props.provider?.credential_id || newCredentialId())
const apiKey = ref('')
const configured = ref(false)
const credentialLoading = ref(false)
const credentialError = ref('')
const presets = ref<ProviderPreset[]>([])
const presetsLoading = ref(false)
const presetsError = ref('')
const saving = ref(false)
const error = ref('')
const requestOverrides = ref<RequestOverride[]>(JSON.parse(JSON.stringify(props.provider?.request_overrides || [])))
const requestJsonValid = ref(true)
const requestPreview = ref('')
const probeResult = ref('')
const probing = ref(false)
const previewCapability = ref('chat')
const previewStream = ref(true)
let draftGeneration = 0
watch([form, requestOverrides, requestJsonValid, apiKey, previewStream, previewCapability], () => { draftGeneration++; requestPreview.value = ''; probeResult.value = '' }, {deep:true, flush:'sync'})
async function previewRequest() {
  const generation = draftGeneration
  error.value = ''
  try {
    if (!requestJsonValid.value) throw new Error('请先修正 JSON。')
    const response = await apiClient.post<{body:Record<string,unknown>}>('/api/providers/request-preview', {
      provider: {provider_type:form.provider_type,name:form.name || '预览',base_url:form.base_url || null,
        default_model:form.default_model || null,request_overrides:requestOverrides.value}, stream:previewStream.value, capability:previewCapability.value,
    })
    if (active && generation === draftGeneration) requestPreview.value = JSON.stringify(response.body, null, 2)
  } catch(e) { if (active && generation === draftGeneration) error.value = (e as Error).message }
}
async function probeRequest() {
  if (probing.value) return
  error.value = ''; probeResult.value = ''; probing.value = true
  const generation = draftGeneration
  try {
    if (!requestJsonValid.value) throw new Error('请先修正 JSON。')
    if (apiKey.value.trim()) throw new Error('请先保存新的 API Key，再进行推理验证。')
    const result = await apiClient.post<{message:string}>('/api/providers/request-probe', {
      provider: {provider_type:form.provider_type,name:form.name || '推理验证',base_url:form.base_url || null,
        default_model:form.default_model || null,request_overrides:JSON.parse(JSON.stringify(requestOverrides.value)),
        credential_id:configured.value ? credentialId.value : null}, stream:previewStream.value,
    })
    if (active && generation === draftGeneration) probeResult.value = result.message
  } catch(e) { if (active && generation === draftGeneration) error.value = (e as Error).message }
  finally { probing.value = false }
}
const contextChanged = ref(false)
const dialog = ref<HTMLElement>()
const previousFocus = document.activeElement as HTMLElement | null
let active = true
let credentialGeneration = 0
const selectedPreset = computed(() => presets.value.find(preset => preset.preset_id === form.preset_id))
const modelOptions = computed(() => contextChanged.value ? [] : props.models ?? [])

async function loadPresets() {
  presetsLoading.value = true
  presetsError.value = ''
  try {
    presets.value = await service.listProviderPresets()
    if (!contextChanged.value) form.preset_id = presets.value.find(preset => preset.provider_type === props.provider?.provider_type && preset.base_url === props.provider?.base_url)?.preset_id ?? ''
  } catch { presetsError.value = '预设加载失败，请重试，或填写自定义服务。' }
  finally { presetsLoading.value = false }
}

onMounted(async () => {
  void loadPresets()
  if (props.provider?.credential_id) {
    const generation = credentialGeneration
    credentialLoading.value = true
    try {
      const result = await service.getCredentialStatus(credentialId.value)
      if (active && generation === credentialGeneration) configured.value = result
    } catch {
      if (active && generation === credentialGeneration) credentialError.value = '无法检查已保存的凭据。可输入新密钥，或关闭后重试。'
    } finally {
      if (generation === credentialGeneration) credentialLoading.value = false
    }
  }
  await nextTick()
  if (active) dialog.value?.querySelector<HTMLInputElement>('input')?.focus()
})

function detachCredential() {
  credentialGeneration++
  apiKey.value = ''
  credentialId.value = newCredentialId()
  configured.value = false
  credentialLoading.value = false
  credentialError.value = ''
  form.default_model = ''
  contextChanged.value = true
  error.value = ''
}

function applyPreset(id: string) {
  if (form.preset_id === id) return
  detachCredential()
  form.preset_id = id
  const preset = presets.value.find(item => item.preset_id === id)
  Object.assign(form, { provider_type: preset?.provider_type ?? 'openai_compatible', name: preset?.name ?? '', base_url: preset?.base_url ?? '' })
}

function changeConnection() {
  form.preset_id = ''
  detachCredential()
}

function close() {
  active = false
  apiKey.value = ''
  emit('close')
}

onBeforeUnmount(() => {
  active = false
  apiKey.value = ''
  previousFocus?.focus()
})

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); close() }
  if (event.key !== 'Tab') return
  const elements = Array.from(dialog.value?.querySelectorAll<HTMLElement>('button, input, select, [tabindex="0"]') ?? []).filter(element => !element.matches(':disabled'))
  const first = elements[0], last = elements[elements.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}

async function save() {
  if (saving.value || credentialLoading.value || !active) return
  error.value = ''
  saving.value = true
  try {
    if (!form.name.trim() || !form.base_url.trim()) throw new Error('请填写名称和 Base URL。')
    if (!requestJsonValid.value) throw new Error('请先修正自定义请求 JSON。')
    if (selectedPreset.value?.requires_credential && !apiKey.value.trim() && !configured.value) throw new Error('请输入 API Key。密钥将由后端加密保存。')
    // Snapshot before awaiting: closing/unmounting must never create a provider with a changed draft.
    const data = { provider_type: form.provider_type, name: form.name.trim(), base_url: form.base_url.trim() || undefined, default_model: form.default_model.trim(), enabled: form.enabled, capabilities: {}, has_credential: false, request_overrides: requestOverrides.value }
    if (apiKey.value.trim()) {
      // Rotate even an existing reference: older installations may share preset credential IDs.
      const nextId = newCredentialId()
      const request = service.putCredential(nextId, apiKey.value.trim())
      apiKey.value = ''
      await request
      if (!active) return
      credentialId.value = nextId
      configured.value = true
    }
    const reference = configured.value ? credentialId.value : undefined
    // A failed status check must not silently unlink the provider's existing credential.
    if (credentialError.value && !reference) throw new Error(credentialError.value)
    const saved = props.provider
      ? await service.updateProvider(props.provider.provider_id, { ...data, version: props.provider.version, credential_id: reference ?? null })
      : await service.createProvider({ ...data, credential_id: reference })
    if (active) { emit('saved', saved); close() }
  } catch (reason) {
    if (active) error.value = reason instanceof Error ? reason.message : 'Provider 保存失败，请重试。'
  } finally { apiKey.value = ''; saving.value = false }
}
</script>

<template>
  <div class="modal-backdrop provider-backdrop" @click.self="close" @keydown="handleKeydown">
    <div ref="dialog" class="modal provider-modal" role="dialog" aria-modal="true" aria-labelledby="provider-form-title" :aria-busy="saving">
      <div class="form-heading"><h2 id="provider-form-title">{{ provider ? '编辑 Provider' : '新增 Provider' }}</h2><button type="button" class="button-secondary" aria-label="关闭提供商表单" @click="close">关闭</button></div>
      <p v-if="presetsLoading" class="subtle" role="status">正在加载提供商预设…</p>
      <div v-if="presetsError" class="error-banner" role="alert">{{ presetsError }} <button type="button" class="button-secondary" :disabled="presetsLoading || saving" @click="loadPresets">重试</button></div>
      <form @submit.prevent="save" @input="requestPreview = ''" @change="requestPreview = ''">
        <fieldset :disabled="saving">
          <ProviderPresetSelector :presets="presets" :model-value="form.preset_id" @update:model-value="applyPreset" />
          <p v-if="selectedPreset?.description" class="subtle">{{ selectedPreset.description }}</p>
          <div class="form-grid">
            <label class="field"><span>接入协议</span><select v-model="form.provider_type" class="select" data-field="protocol" @change="changeConnection"><option value="openai_compatible">OpenAI Compatible</option><option value="openai_chat">OpenAI Chat</option><option value="openai_responses">OpenAI Responses</option><option value="anthropic_messages">Anthropic Messages</option><option value="ollama">Ollama</option></select></label>
            <label class="field"><span>名称</span><input v-model="form.name" class="input" data-field="name" required /></label>
            <label class="field wide"><span>Base URL</span><input v-model="form.base_url" class="input" data-field="base-url" placeholder="https://api.example.com/v1" required @change="changeConnection" /></label>
            <label class="field wide"><span>API Key</span><input v-model="apiKey" class="input" type="password" autocomplete="new-password" spellcheck="false" :placeholder="configured ? '已配置，留空表示不修改' : '请输入 API Key（无鉴权服务可留空）'" /><small class="subtle">密钥由本地 AI Core 加密保存；提供商配置仅保存独立的凭据引用。</small></label>
            <p v-if="credentialLoading" class="subtle wide" role="status">正在检查凭据状态…</p>
            <p v-if="credentialError" class="error-text wide" role="alert">{{ credentialError }}</p>
            <label class="field wide"><span>默认聊天模型</span><input v-model="form.default_model" class="input" data-field="model" list="provider-model-options" placeholder="输入模型 ID，或保存后获取模型列表" /><datalist id="provider-model-options"><option v-for="model in modelOptions" :key="model.model_id" :value="model.model_id">{{ model.name }}</option></datalist></label>
          </div>
          <label class="inline-actions"><input v-model="form.enabled" type="checkbox" /> 启用</label>
          <RequestJsonEditor v-model="requestOverrides" @valid="requestJsonValid = $event" />
          <div class="inline-actions"><label>预览能力<select v-model="previewCapability" class="select"><option value="chat">聊天</option><option value="embedding">Embedding</option><option value="transcription">转写</option><option value="speaker_matching">声纹</option></select></label><label><input v-model="previewStream" type="checkbox" />流式聊天</label></div>
          <button type="button" class="button-secondary" @click="previewRequest">预览最终请求（隐藏正文）</button>
          <button v-if="previewCapability === 'chat'" type="button" class="button-secondary" :disabled="probing || credentialLoading || !requestJsonValid" @click="probeRequest">{{ probing ? '推理验证中…' : '发送测试推理请求' }}</button>
          <p class="subtle">推理验证会向当前模型发送固定短消息，并计入实际用量。媒体参数请通过真实转写或声纹操作验证。</p><p v-if="probeResult" role="status">{{ probeResult }}</p>
          <pre v-if="requestPreview" class="request-preview">{{ requestPreview }}</pre>
        </fieldset>
        <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
        <div class="inline-actions form-footer"><button class="button-primary" type="submit" :disabled="saving || credentialLoading">{{ saving ? '保存中…' : '保存提供商' }}</button><button type="button" class="button-secondary" @click="close">取消</button></div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.provider-modal { width: min(820px, 100%); max-height: 90dvh; }
.form-heading { display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); margin-bottom: var(--space-md); }
.form-heading h2 { margin: 0; }
fieldset { display: grid; gap: var(--space-md); border: 0; padding: 0; margin: 0; min-width: 0; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-md); }
.wide { grid-column: 1 / -1; }
.error-text { color: var(--color-error); }
.request-preview { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 300px; overflow: auto; }
.form-footer { padding-top: var(--space-sm); }
@media (max-width: 600px) { .provider-backdrop { padding: 12px; }.provider-modal { padding: var(--space-lg); max-height: 94dvh; }.form-grid { grid-template-columns: 1fr; } }
</style>
