<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import type { ProviderConfig, ProviderType } from '@/contracts'
import { useProviderStore } from '@/stores/provider'
import { useSettingsStore } from '@/stores/settings'
import { useThemeStore } from '@/stores/theme'

type Section = 'general' | 'editor' | 'providers' | 'index' | 'permissions' | 'ai-core'
const sections: Array<{ id: Section; label: string }> = [
  { id: 'general', label: '通用' }, { id: 'editor', label: '编辑器' }, { id: 'providers', label: '模型提供商' },
  { id: 'index', label: '索引与模型' }, { id: 'permissions', label: '权限' }, { id: 'ai-core', label: 'AI Core 诊断' },
]
const activeSection = ref<Section>('general')
const settingsStore = useSettingsStore()
const providerStore = useProviderStore()
const themeStore = useThemeStore()
const showProviderForm = ref(false)
const editingProviderId = ref<string | null>(null)
const providerAction = ref('')
const testResults = ref<Record<string, string>>({})
const providerForm = reactive({ preset_id: '', provider_type: 'openai_compatible' as ProviderType, name: '', base_url: '', default_model: '', credential_id: '', enabled: true })
const formModels = computed(() => editingProviderId.value ? providerStore.modelsByProvider[editingProviderId.value] ?? [] : [])

onMounted(async () => {
  await Promise.all([providerStore.loadProviders(), providerStore.loadPresets(), settingsStore.loadDiagnostics()])
  await providerStore.refreshEnabledModels()
})

function presetIdFor(provider?: ProviderConfig) {
  if (!provider) return ''
  return providerStore.presets.find((preset) =>
    preset.provider_type === provider.provider_type && preset.base_url === provider.base_url
  )?.preset_id ?? ''
}

function openProvider(provider?: ProviderConfig) {
  editingProviderId.value = provider?.provider_id ?? null
  Object.assign(providerForm, { preset_id: presetIdFor(provider), provider_type: provider?.provider_type ?? 'openai_compatible', name: provider?.name ?? '', base_url: provider?.base_url ?? '', default_model: provider?.default_model ?? '', credential_id: provider?.credential_id ?? '', enabled: provider?.enabled ?? true })
  showProviderForm.value = true
  if (provider) void providerStore.loadModels(provider.provider_id).catch(() => undefined)
}

function applyProviderPreset() {
  const preset = providerStore.presets.find((item) => item.preset_id === providerForm.preset_id)
  if (!preset) return
  Object.assign(providerForm, {
    provider_type: preset.provider_type,
    name: preset.name,
    base_url: preset.base_url,
  })
}

async function saveProvider() {
  providerAction.value = ''
  const data = { ...providerForm, base_url: providerForm.base_url || undefined, credential_id: providerForm.credential_id || undefined, capabilities: {}, has_credential: Boolean(providerForm.credential_id) }
  try {
    const saved = editingProviderId.value
      ? await providerStore.updateProvider(editingProviderId.value, data)
      : await providerStore.addProvider(data)
    showProviderForm.value = false
    if (saved.enabled) void providerStore.loadModels(saved.provider_id).catch(() => undefined)
  } catch (error) {
    providerAction.value = error instanceof Error ? error.message : 'Provider 保存失败'
  }
}

async function removeProvider(provider: ProviderConfig) { if (!confirm(`确定删除 Provider“${provider.name}”吗？`)) return; try { await providerStore.deleteProvider(provider.provider_id) } catch (error) { providerAction.value = error instanceof Error ? error.message : '删除失败' } }
async function testProvider(provider: ProviderConfig) { testResults.value[provider.provider_id] = '测试中…'; const result = await providerStore.testProvider(provider.provider_id); testResults.value[provider.provider_id] = result.success ? `连接成功${result.latency_ms ? ` · ${result.latency_ms}ms` : ''}` : `连接失败：${result.error}` }
async function refreshModels(provider: ProviderConfig) { await providerStore.loadModels(provider.provider_id).catch(() => undefined) }
async function chooseDefaultModel(provider: ProviderConfig, event: Event) {
  const defaultModel = (event.target as HTMLSelectElement).value
  try { await providerStore.updateProvider(provider.provider_id, { default_model: defaultModel }) }
  catch (error) { providerAction.value = error instanceof Error ? error.message : '默认模型更新失败' }
}
</script>

<template>
  <section class="feature-page settings-page">
    <header class="feature-header"><div><h1>设置</h1><p>管理应用偏好、模型、索引、权限和本地 AI Core。</p></div></header>
    <nav class="settings-nav"><button v-for="section in sections" :key="section.id" :class="{ active: activeSection === section.id }" @click="activeSection = section.id">{{ section.label }}</button></nav>

    <div v-if="activeSection === 'general'" class="panel settings-section"><h2>通用</h2><label class="setting-row"><span><strong>恢复上次 Vault</strong><small>启动后自动打开最近使用的知识库</small></span><input v-model="settingsStore.restoreLastVault" type="checkbox" /></label><div class="setting-row"><span><strong>自动保存间隔</strong><small>编辑停止后等待多久写入文件</small></span><select v-model.number="settingsStore.autoSaveInterval" class="select short"><option :value="500">0.5 秒</option><option :value="1500">1.5 秒</option><option :value="3000">3 秒</option></select></div><div class="setting-row"><span><strong>界面语言</strong><small>当前阶段支持中文和英文入口</small></span><select v-model="settingsStore.language" class="select short"><option value="zh-CN">简体中文</option><option value="en">English</option></select></div><div class="setting-row"><span><strong>版本</strong><small>Desktop / AI Core</small></span><span>{{ settingsStore.appVersion }} / {{ settingsStore.aiCoreVersion }}</span></div></div>

    <div v-else-if="activeSection === 'editor'" class="panel settings-section"><h2>编辑器</h2><div class="setting-row"><span><strong>默认模式</strong><small>新打开文件使用的编辑器模式</small></span><select v-model="settingsStore.defaultEditorMode" class="select short"><option value="wysiwyg">写作与预览</option><option value="source">Markdown 源码</option></select></div><div class="setting-row"><span><strong>字号</strong></span><input v-model.number="themeStore.fontEditorSize" class="input short" type="number" min="12" max="32" /></div><div class="setting-row"><span><strong>行高</strong></span><input v-model.number="themeStore.lineHeight" class="input short" type="number" min="1.2" max="2.4" step="0.1" /></div><div class="setting-row"><span><strong>行宽</strong><small>Markdown 预览最大字符宽度</small></span><input v-model.number="settingsStore.editorLineWidth" class="input short" type="number" min="40" max="140" /></div><label class="setting-row"><span><strong>拼写检查</strong></span><input v-model="settingsStore.spellCheck" type="checkbox" /></label></div>

    <div v-else-if="activeSection === 'providers'" class="settings-section">
      <div class="section-head">
        <div><h2>模型提供商</h2><p class="subtle">支持 OpenAI、DeepSeek、Ollama 和自定义兼容服务。</p></div>
        <button class="button-primary" @click="openProvider()">新增 Provider</button>
      </div>
      <div v-if="providerStore.error || providerAction" class="error-banner">{{ providerStore.error || providerAction }}</div>
      <div class="provider-list">
        <article v-for="provider in providerStore.providers" :key="provider.provider_id" class="item-card provider-card">
          <div class="provider-main">
            <div class="inline-actions"><strong>{{ provider.name }}</strong><span class="badge" :class="{ success: provider.enabled }">{{ provider.provider_type }}</span></div>
            <p class="subtle">{{ provider.base_url || '本地内置' }} · 默认模型 {{ provider.default_model || '未设置' }}</p>
            <div class="tag-list"><span v-for="(_, capability) in provider.capabilities" :key="capability" class="badge">{{ capability }}</span></div>
            <div v-if="providerStore.modelsByProvider[provider.provider_id]?.length" class="model-picker">
              <label :for="`default-model-${provider.provider_id}`">默认模型</label>
              <select :id="`default-model-${provider.provider_id}`" class="select" :value="provider.default_model" @change="chooseDefaultModel(provider, $event)">
                <option value="">未设置</option>
                <option v-for="model in providerStore.modelsByProvider[provider.provider_id]" :key="model.model_id" :value="model.model_id">{{ model.name }}</option>
              </select>
              <span class="subtle">已获取 {{ providerStore.modelsByProvider[provider.provider_id].length }} 个模型</span>
            </div>
            <p v-if="providerStore.modelErrorsByProvider[provider.provider_id]" class="error-text">模型获取失败：{{ providerStore.modelErrorsByProvider[provider.provider_id] }}</p>
            <p v-if="testResults[provider.provider_id]" class="test-result">{{ testResults[provider.provider_id] }}</p>
          </div>
          <div class="inline-actions provider-actions">
            <button class="button-secondary" :disabled="providerStore.modelLoadingByProvider[provider.provider_id]" @click="refreshModels(provider)">{{ providerStore.modelLoadingByProvider[provider.provider_id] ? '获取中…' : '刷新模型' }}</button>
            <button class="button-secondary" @click="testProvider(provider)">测试</button>
            <button class="button-secondary" @click="openProvider(provider)">编辑</button>
            <button class="button-danger" :disabled="provider.provider_id === 'mock'" @click="removeProvider(provider)">删除</button>
          </div>
        </article>
      </div>
    </div>

    <div v-else-if="activeSection === 'index'" class="panel settings-section"><h2>索引与模型</h2><div class="index-summary"><div><span class="badge" :class="{ success: settingsStore.indexStatus.status === 'idle', error: settingsStore.indexStatus.status === 'error' }">{{ settingsStore.indexStatus.status }}</span><p>待处理任务 {{ settingsStore.indexStatus.pending_jobs }}</p></div><div><strong>{{ settingsStore.indexStatus.total_notes }}</strong><small>笔记</small></div><div><strong>{{ settingsStore.indexStatus.total_blocks }}</strong><small>Block</small></div></div><div v-if="settingsStore.indexStatus.error" class="error-banner">{{ settingsStore.indexStatus.error }}</div><div class="inline-actions"><button class="button-primary" @click="settingsStore.rebuildIndex('full')">重建全部</button><button class="button-secondary" @click="settingsStore.rebuildIndex('fts')">重建文本索引</button><button class="button-secondary" @click="settingsStore.rebuildIndex('vector')">重建向量索引</button></div></div>

    <div v-else-if="activeSection === 'permissions'" class="panel settings-section"><h2>权限策略</h2><p class="muted section-description">高影响能力默认需要确认。未知权限由后端拒绝。</p><div class="permission-list"><div v-for="(policy, permission) in settingsStore.permissionPolicy" :key="permission" class="setting-row"><span><strong>{{ permission }}</strong></span><select :value="policy" class="select short" @change="settingsStore.setPermission(String(permission), ($event.target as HTMLSelectElement).value as 'allow' | 'confirm' | 'deny')"><option value="allow">允许</option><option value="confirm">每次确认</option><option value="deny">拒绝</option></select></div></div></div>

    <div v-else class="panel settings-section"><h2>AI Core 诊断</h2><div v-if="settingsStore.diagnosticsError" class="error-banner">{{ settingsStore.diagnosticsError }}</div><div class="diagnostic-grid"><div class="item-card"><span class="badge" :class="{ success: settingsStore.aiCoreStatus === 'running', error: settingsStore.aiCoreStatus === 'error' }">{{ settingsStore.aiCoreStatus }}</span><h3>Sidecar 状态</h3><p class="subtle">AI Core 不可用时，Markdown 编辑仍可继续使用。</p></div><div class="item-card"><strong>{{ settingsStore.aiCoreAddress }}</strong><h3>开发 API 地址</h3><p class="subtle">正式桌面环境由 Sidecar Manager 动态提供。</p></div></div><div class="inline-actions diagnostic-actions"><button class="button-primary" @click="settingsStore.loadDiagnostics">重新检测</button><button class="button-secondary" @click="settingsStore.restartAiCore">重启 AI Core</button></div></div>

    <div v-if="showProviderForm" class="modal-backdrop" @click.self="showProviderForm = false">
      <div class="modal">
        <h2>{{ editingProviderId ? '编辑 Provider' : '新增 Provider' }}</h2>
        <form @submit.prevent="saveProvider">
          <div class="field">
            <label>提供商预设</label>
            <select v-model="providerForm.preset_id" class="select" @change="applyProviderPreset">
              <option value="">自定义</option>
              <option v-for="preset in providerStore.presets" :key="preset.preset_id" :value="preset.preset_id">{{ preset.name }}</option>
            </select>
          </div>
          <div class="field"><label>接入协议</label><select v-model="providerForm.provider_type" class="select"><option value="openai_compatible">OpenAI Compatible</option><option value="openai_chat">OpenAI Chat</option><option value="openai_responses">OpenAI Responses</option><option value="anthropic_messages">Anthropic Messages</option><option value="ollama">Ollama</option></select></div>
          <div class="field"><label>名称</label><input v-model="providerForm.name" class="input" required /></div>
          <div class="field"><label>Base URL</label><input v-model="providerForm.base_url" class="input" placeholder="https://api.example.com/v1" required /></div>
          <div class="field">
            <label>默认模型</label>
            <input v-model="providerForm.default_model" class="input" :list="editingProviderId ? 'provider-model-options' : undefined" placeholder="保存后自动获取，也可以手动输入" />
            <datalist id="provider-model-options"><option v-for="model in formModels" :key="model.model_id" :value="model.model_id">{{ model.name }}</option></datalist>
          </div>
          <div class="field"><label>Credential ID</label><input v-model="providerForm.credential_id" class="input" placeholder="由桌面 Host 注入的凭据标识" /><small class="subtle">此处不输入或回显 API Key，密钥明文由 Stronghold 保存。</small></div>
          <label class="inline-actions"><input v-model="providerForm.enabled" type="checkbox" /> 启用</label>
          <div class="inline-actions"><button class="button-primary">保存并获取模型</button><button type="button" class="button-secondary" @click="showProviderForm = false">取消</button></div>
        </form>
      </div>
    </div>
  </section>
</template>

<style scoped>
.settings-page { max-width: 1120px; margin: 0 auto; }
.settings-section { display: grid; gap: var(--space-md); }
.settings-section h2 { margin-bottom: var(--space-sm); }
.setting-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-xl); min-height: 54px; padding: var(--space-sm) 0; border-bottom: 1px solid var(--color-border-subtle); }
.setting-row small { display: block; color: var(--color-text-tertiary); }.short { width: min(220px, 45%); }
.section-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--space-lg); }
.provider-list { display: grid; gap: var(--space-md); }.provider-card { display: flex; align-items: center; justify-content: space-between; gap: var(--space-xl); }.provider-main { min-width: 0; flex: 1; }.provider-card p, .provider-card .tag-list { margin-top: var(--space-sm); }
.model-picker { display: flex; align-items: center; gap: var(--space-sm); margin-top: var(--space-md); }.model-picker label { white-space: nowrap; font-weight: 600; }.model-picker .select { width: min(360px, 100%); }.provider-actions { flex-wrap: wrap; justify-content: flex-end; }.error-text { color: var(--color-danger, #d33); }
.test-result { color: var(--color-info); }.index-summary, .diagnostic-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-md); }.index-summary > div { padding: var(--space-lg); border-radius: var(--radius-md); background: var(--color-background-secondary); }.index-summary strong, .index-summary small { display: block; }.index-summary strong { font-size: var(--font-size-3xl); }
.section-description { margin-top: calc(-1 * var(--space-md)); }.diagnostic-grid { grid-template-columns: repeat(2, 1fr); }.diagnostic-grid h3 { margin: var(--space-md) 0 var(--space-xs); }.diagnostic-actions { margin-top: var(--space-md); }
@media (max-width: 700px) { .provider-card, .setting-row, .model-picker { align-items: flex-start; flex-direction: column; }.short, .model-picker .select { width: 100%; }.index-summary, .diagnostic-grid { grid-template-columns: 1fr; }.provider-actions { justify-content: flex-start; } }
</style>
