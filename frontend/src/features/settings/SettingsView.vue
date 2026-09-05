<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { ProviderConfig } from '@/contracts'
import ProviderForm from './ProviderForm.vue'
import ProviderLogo from './ProviderLogo.vue'
import ModelRoutingSettings from './ModelRoutingSettings.vue'
import LocalModelSettings from './LocalModelSettings.vue'
import UsageCard from './UsageCard.vue'
import { useProviderStore } from '@/stores/provider'
import { useSettingsStore } from '@/stores/settings'
import { useThemeStore } from '@/stores/theme'
import { t } from '@/i18n'

type Section = 'general' | 'editor' | 'providers' | 'index' | 'permissions' | 'ai-core'
const sections = computed<Array<{ id: Section; label: string }>>(() => [
  { id: 'general', label: t('通用', 'General') }, { id: 'editor', label: t('编辑器', 'Editor') }, { id: 'providers', label: t('模型提供商', 'Model Providers') },
  { id: 'index', label: t('索引与模型', 'Index and Models') }, { id: 'permissions', label: t('权限', 'Permissions') }, { id: 'ai-core', label: t('AI Core 诊断', 'AI Core Diagnostics') },
])
const activeSection = ref<Section>('general')
const settingsStore = useSettingsStore()
const providerStore = useProviderStore()
const themeStore = useThemeStore()
const showProviderForm = ref(false)
const editingProvider = ref<ProviderConfig>()
const providerAction = ref('')
const testResults = ref<Record<string, string>>({})
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
  editingProvider.value = provider
  providerAction.value = ''
  showProviderForm.value = true
  if (provider) void providerStore.loadModels(provider.provider_id).catch(() => undefined)
}

async function providerSaved(provider: ProviderConfig) {
  await providerStore.loadProviders()
  if (provider.enabled) void providerStore.loadModels(provider.provider_id).catch(() => undefined)
}

async function removeProvider(provider: ProviderConfig) { if (!confirm(`${t('确定删除 Provider', 'Delete Provider')} “${provider.name}”?`)) return; try { await providerStore.deleteProvider(provider.provider_id) } catch (error) { providerAction.value = error instanceof Error ? error.message : t('删除失败', 'Delete failed') } }
async function testProvider(provider: ProviderConfig) { testResults.value[provider.provider_id] = t('测试中…', 'Testing…'); const result = await providerStore.testProvider(provider.provider_id); testResults.value[provider.provider_id] = result.success ? `${t('连接成功', 'Connection succeeded')}${result.latency_ms ? ` · ${result.latency_ms}ms` : ''}` : `${t('连接失败：', 'Connection failed: ')}${result.error}` }
async function refreshModels(provider: ProviderConfig) { await providerStore.loadModels(provider.provider_id).catch(() => undefined) }
async function chooseDefaultModel(provider: ProviderConfig, event: Event) {
  const defaultModel = (event.target as HTMLSelectElement).value
  try { await providerStore.updateProvider(provider.provider_id, { default_model: defaultModel }) }
  catch (error) { providerAction.value = error instanceof Error ? error.message : t('默认模型更新失败', 'Failed to update the default model') }
}
</script>

<template>
  <section class="feature-page settings-page">
    <header class="feature-header"><div><h1>{{ t('设置', 'Settings') }}</h1><p>{{ t('管理应用偏好、模型、索引、权限和本地 AI Core。', 'Manage application preferences, models, indexing, permissions, and the local AI Core.') }}</p></div></header>
    <nav class="settings-nav"><button v-for="section in sections" :key="section.id" :class="{ active: activeSection === section.id }" @click="activeSection = section.id">{{ section.label }}</button></nav>

    <div v-if="activeSection === 'general'" class="panel settings-section"><h2>{{ t('通用', 'General') }}</h2><label class="setting-row"><span><strong>{{ t('恢复上次 Vault', 'Restore last Vault') }}</strong><small>{{ t('启动后自动打开最近使用的知识库', 'Open the most recently used knowledge base at startup') }}</small></span><input v-model="settingsStore.restoreLastVault" type="checkbox" /></label><div class="setting-row"><span><strong>{{ t('自动保存间隔', 'Autosave interval') }}</strong><small>{{ t('编辑停止后等待多久写入文件', 'How long to wait after editing before saving') }}</small></span><select v-model.number="settingsStore.autoSaveInterval" class="select short"><option :value="500">0.5 {{ t('秒', 'sec') }}</option><option :value="1500">1.5 {{ t('秒', 'sec') }}</option><option :value="3000">3 {{ t('秒', 'sec') }}</option></select></div><div class="setting-row"><span><strong>{{ t('界面语言', 'Interface language') }}</strong><small>{{ t('切换后立即应用到界面', 'Applied to the interface immediately') }}</small></span><select v-model="settingsStore.language" class="select short"><option value="zh-CN">简体中文</option><option value="en">English</option></select></div><div class="setting-row"><span><strong>{{ t('版本', 'Version') }}</strong><small>Desktop / AI Core</small></span><span>{{ settingsStore.appVersion }} / {{ settingsStore.aiCoreVersion }}</span></div></div>

    <div v-else-if="activeSection === 'editor'" class="panel settings-section"><h2>{{ t('编辑器', 'Editor') }}</h2><div class="setting-row"><span><strong>{{ t('默认模式', 'Default mode') }}</strong><small>{{ t('新打开文件使用的编辑器模式', 'Editor mode used for newly opened files') }}</small></span><select v-model="settingsStore.defaultEditorMode" class="select short"><option value="wysiwyg">{{ t('写作与预览', 'Writing and preview') }}</option><option value="source">{{ t('Markdown 源码', 'Markdown source') }}</option></select></div><div class="setting-row"><span><strong>{{ t('字号', 'Font size') }}</strong></span><input v-model.number="themeStore.fontEditorSize" class="input short" type="number" min="12" max="32" /></div><div class="setting-row"><span><strong>{{ t('行高', 'Line height') }}</strong></span><input v-model.number="themeStore.lineHeight" class="input short" type="number" min="1.2" max="2.4" step="0.1" /></div><div class="setting-row"><span><strong>{{ t('行宽', 'Line width') }}</strong><small>{{ t('Markdown 预览最大字符宽度', 'Maximum character width for Markdown preview') }}</small></span><input v-model.number="settingsStore.editorLineWidth" class="input short" type="number" min="40" max="140" /></div><label class="setting-row"><span><strong>{{ t('拼写检查', 'Spell check') }}</strong><small>{{ t('在写作与源码编辑器中使用系统拼写检查', 'Use system spell checking in visual and source editors') }}</small></span><input v-model="settingsStore.spellCheck" type="checkbox" /></label></div>

    <div v-else-if="activeSection === 'providers'" class="settings-section">
      <div class="section-head">
        <div><h2>{{ t('模型提供商', 'Model Providers') }}</h2><p class="subtle">{{ t('选择国内外提供商预设，或配置自定义 API 与独立密钥。', 'Choose a provider preset or configure a custom API with separate credentials.') }}</p></div>
        <button class="button-primary" @click="openProvider()">{{ t('新增 Provider', 'Add Provider') }}</button>
      </div>
      <div v-if="providerStore.error || providerAction" class="error-banner">{{ providerStore.error || providerAction }}</div>
      <LocalModelSettings />
      <UsageCard />
      <p v-if="!providerStore.providers.length" class="subtle">{{ providerStore.isLoading ? t('正在加载提供商…', 'Loading providers…') : t('尚无可用提供商，请添加真实 API 或本地 Ollama 配置。', 'No providers are available. Add a real API or local Ollama configuration.') }}</p>
      <div class="provider-list">
        <article v-for="provider in providerStore.providers" :key="provider.provider_id" class="item-card provider-card">
          <div class="provider-main">
            <div class="inline-actions"><ProviderLogo :logo-id="providerStore.presets.find(preset => preset.preset_id === presetIdFor(provider))?.logo_id || presetIdFor(provider)" /><strong>{{ provider.name }}</strong><span class="badge" :class="{ success: provider.enabled }">{{ provider.provider_type }}</span></div>
            <p class="subtle">{{ provider.base_url || t('本地内置', 'Built in locally') }} · {{ t('默认模型', 'Default model') }} {{ provider.default_model || t('未设置', 'Not set') }}</p>
            <div class="tag-list"><span v-for="(_, capability) in provider.capabilities" :key="capability" class="badge">{{ capability }}</span></div>
            <div v-if="providerStore.modelsByProvider[provider.provider_id]?.length" class="model-picker">
              <label :for="`default-model-${provider.provider_id}`">{{ t('默认模型', 'Default model') }}</label>
              <select :id="`default-model-${provider.provider_id}`" class="select" :value="provider.default_model" @change="chooseDefaultModel(provider, $event)">
                <option value="">{{ t('未设置', 'Not set') }}</option>
                <option v-for="model in providerStore.modelsByProvider[provider.provider_id]" :key="model.model_id" :value="model.model_id">{{ model.name }}</option>
              </select>
              <span class="subtle">{{ t('已获取', 'Loaded') }} {{ providerStore.modelsByProvider[provider.provider_id].length }} {{ t('个模型', 'models') }}</span>
            </div>
            <p v-if="providerStore.modelErrorsByProvider[provider.provider_id]" class="error-text">{{ t('模型获取失败：', 'Failed to load models: ') }}{{ providerStore.modelErrorsByProvider[provider.provider_id] }}</p>
            <p v-if="testResults[provider.provider_id]" class="test-result">{{ testResults[provider.provider_id] }}</p>
          </div>
          <div class="inline-actions provider-actions">
            <button class="button-secondary" :disabled="providerStore.modelLoadingByProvider[provider.provider_id]" @click="refreshModels(provider)">{{ providerStore.modelLoadingByProvider[provider.provider_id] ? t('获取中…', 'Loading…') : t('刷新模型', 'Refresh models') }}</button>
            <button class="button-secondary" @click="testProvider(provider)">{{ t('测试', 'Test') }}</button>
            <button class="button-secondary" @click="openProvider(provider)">{{ t('编辑', 'Edit') }}</button>
            <button class="button-danger" @click="removeProvider(provider)">{{ t('删除', 'Delete') }}</button>
          </div>
        </article>
      </div>
    </div>

    <div v-else-if="activeSection === 'index'" class="panel settings-section"><h2>{{ t('索引与模型', 'Index and Models') }}</h2><div class="index-summary"><div><span class="badge" :class="{ success: settingsStore.indexStatus.status === 'idle', error: settingsStore.indexStatus.status === 'error' }">{{ settingsStore.indexStatus.status }}</span><p>{{ t('待处理任务', 'Pending jobs') }} {{ settingsStore.indexStatus.pending_jobs }}</p></div><div><strong>{{ settingsStore.indexStatus.total_notes ?? t('未获取', 'Unavailable') }}</strong><small>{{ t('笔记', 'Notes') }}</small></div><div><strong>{{ settingsStore.indexStatus.total_blocks ?? t('未获取', 'Unavailable') }}</strong><small>Block</small></div></div><div v-if="settingsStore.indexStatus.error" class="error-banner">{{ settingsStore.indexStatus.error }}</div><div class="inline-actions"><button class="button-primary" @click="settingsStore.rebuildIndex('full')">{{ t('重建全部', 'Rebuild all') }}</button><span class="subtle">{{ t('当前后端支持全量重建。', 'The current backend supports a full rebuild.') }}</span></div><ModelRoutingSettings /></div>

    <div v-else-if="activeSection === 'permissions'" class="panel settings-section"><h2>{{ t('权限策略', 'Permission Policy') }}</h2><p class="muted section-description">{{ t('以下为后端当前生效的权限策略；全局策略编辑尚未开放，运行时按实际权限请求确认。', 'These policies are active in the backend. Global policy editing is not yet available; runtime requests are confirmed as needed.') }}</p><p v-if="!Object.keys(settingsStore.permissionPolicy).length" class="subtle">{{ t('尚未获取权限策略，请检查后端连接并重新检测。', 'Permission policy is unavailable. Check the backend connection and try again.') }}</p><div class="permission-list"><div v-for="(policy, permission) in settingsStore.permissionPolicy" :key="permission" class="setting-row"><span><strong>{{ permission }}</strong></span><span>{{ policy === 'allow' ? t('允许', 'Allow') : policy === 'confirm' ? t('每次确认', 'Confirm each time') : t('拒绝', 'Deny') }}</span></div></div></div>

    <div v-else class="panel settings-section"><h2>{{ t('AI Core 诊断', 'AI Core Diagnostics') }}</h2><div v-if="settingsStore.diagnosticsError" class="error-banner">{{ settingsStore.diagnosticsError }}</div><div class="diagnostic-grid"><div class="item-card"><span class="badge" :class="{ success: settingsStore.aiCoreStatus === 'running', error: settingsStore.aiCoreStatus === 'error' }">{{ settingsStore.aiCoreStatus }}</span><h3>{{ t('AI Core 连接状态', 'AI Core connection') }}</h3><p class="subtle">{{ t('AI Core 不可用时，Markdown 编辑仍可继续使用。', 'Markdown editing remains available when AI Core is offline.') }}</p></div><div class="item-card"><strong>{{ settingsStore.aiCoreAddress }}</strong><h3>{{ t('开发 API 地址', 'Development API address') }}</h3><p class="subtle">{{ t('正式桌面环境由 Sidecar Manager 动态提供。', 'The desktop build will provide this through Sidecar Manager.') }}</p></div></div><div class="inline-actions diagnostic-actions"><button class="button-primary" @click="settingsStore.loadDiagnostics">{{ t('重新检测', 'Check again') }}</button><span class="subtle">{{ t('当前 Web 端不支持重启后端进程，请在运行后端的终端中操作。', 'The web build cannot restart the backend. Use the terminal running it.') }}</span></div></div>

    <ProviderForm v-if="showProviderForm" :provider="editingProvider" :models="editingProvider ? providerStore.modelsByProvider[editingProvider.provider_id] : []" @close="showProviderForm = false" @saved="providerSaved" />
  </section>
</template>

<style scoped>
.settings-page { max-width: 1120px; margin: 0 auto; }
.settings-section { display: grid; gap: var(--space-md); }
.settings-section h2 { margin-bottom: var(--space-sm); }
.setting-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-xl); min-height: 58px; padding: var(--space-sm) var(--space-md); border-bottom: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); transition: background-color var(--motion-fast); }
.setting-row:hover { background: var(--color-background-secondary); }
.setting-row small { display: block; color: var(--color-text-tertiary); }.short { width: min(220px, 45%); }
.section-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--space-lg); }
.provider-list { display: grid; gap: var(--space-md); }.provider-card { display: flex; align-items: center; justify-content: space-between; gap: var(--space-xl); }.provider-main { min-width: 0; flex: 1; }.provider-card p, .provider-card .tag-list { margin-top: var(--space-sm); }
.model-picker { display: flex; align-items: center; gap: var(--space-sm); margin-top: var(--space-md); }.model-picker label { white-space: nowrap; font-weight: 600; }.model-picker .select { width: min(360px, 100%); }.provider-actions { flex-wrap: wrap; justify-content: flex-end; }.error-text { color: var(--color-error); }
.test-result { color: var(--color-info); }.index-summary, .diagnostic-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-md); }.index-summary > div { padding: var(--space-lg); border-radius: var(--radius-md); background: var(--color-background-secondary); }.index-summary strong, .index-summary small { display: block; }.index-summary strong { font-size: var(--font-size-3xl); }
.section-description { margin-top: calc(-1 * var(--space-md)); }.diagnostic-grid { grid-template-columns: repeat(2, 1fr); }.diagnostic-grid h3 { margin: var(--space-md) 0 var(--space-xs); }.diagnostic-actions { margin-top: var(--space-md); }
@media (max-width: 700px) { .provider-card, .setting-row, .model-picker { align-items: flex-start; flex-direction: column; }.short, .model-picker .select { width: 100%; }.index-summary, .diagnostic-grid { grid-template-columns: 1fr; }.provider-actions { justify-content: flex-start; } }
</style>
