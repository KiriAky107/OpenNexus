<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { ProviderConfig } from '@/contracts'
import ProviderForm from './ProviderForm.vue'
import ProviderLogo from './ProviderLogo.vue'
import ModelRoutingSettings from './ModelRoutingSettings.vue'
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
        <div><h2>模型提供商</h2><p class="subtle">选择国内外提供商预设，或配置自定义 API 与独立密钥。</p></div>
        <button class="button-primary" @click="openProvider()">新增 Provider</button>
      </div>
      <div v-if="providerStore.error || providerAction" class="error-banner">{{ providerStore.error || providerAction }}</div>
      <div class="provider-list">
        <article v-for="provider in providerStore.providers" :key="provider.provider_id" class="item-card provider-card">
          <div class="provider-main">
            <div class="inline-actions"><ProviderLogo :logo-id="providerStore.presets.find(preset => preset.preset_id === presetIdFor(provider))?.logo_id || presetIdFor(provider)" /><strong>{{ provider.name }}</strong><span class="badge" :class="{ success: provider.enabled }">{{ provider.provider_type }}</span></div>
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

    <div v-else-if="activeSection === 'index'" class="panel settings-section"><h2>索引与模型</h2><div class="index-summary"><div><span class="badge" :class="{ success: settingsStore.indexStatus.status === 'idle', error: settingsStore.indexStatus.status === 'error' }">{{ settingsStore.indexStatus.status }}</span><p>待处理任务 {{ settingsStore.indexStatus.pending_jobs }}</p></div><div><strong>{{ settingsStore.indexStatus.total_notes }}</strong><small>笔记</small></div><div><strong>{{ settingsStore.indexStatus.total_blocks }}</strong><small>Block</small></div></div><div v-if="settingsStore.indexStatus.error" class="error-banner">{{ settingsStore.indexStatus.error }}</div><div class="inline-actions"><button class="button-primary" @click="settingsStore.rebuildIndex('full')">重建全部</button><button class="button-secondary" @click="settingsStore.rebuildIndex('fts')">重建文本索引</button><button class="button-secondary" @click="settingsStore.rebuildIndex('vector')">重建向量索引</button></div><ModelRoutingSettings /></div>

    <div v-else-if="activeSection === 'permissions'" class="panel settings-section"><h2>权限策略</h2><p class="muted section-description">高影响能力默认需要确认。未知权限由后端拒绝。</p><div class="permission-list"><div v-for="(policy, permission) in settingsStore.permissionPolicy" :key="permission" class="setting-row"><span><strong>{{ permission }}</strong></span><select :value="policy" class="select short" @change="settingsStore.setPermission(String(permission), ($event.target as HTMLSelectElement).value as 'allow' | 'confirm' | 'deny')"><option value="allow">允许</option><option value="confirm">每次确认</option><option value="deny">拒绝</option></select></div></div></div>

    <div v-else class="panel settings-section"><h2>AI Core 诊断</h2><div v-if="settingsStore.diagnosticsError" class="error-banner">{{ settingsStore.diagnosticsError }}</div><div class="diagnostic-grid"><div class="item-card"><span class="badge" :class="{ success: settingsStore.aiCoreStatus === 'running', error: settingsStore.aiCoreStatus === 'error' }">{{ settingsStore.aiCoreStatus }}</span><h3>Sidecar 状态</h3><p class="subtle">AI Core 不可用时，Markdown 编辑仍可继续使用。</p></div><div class="item-card"><strong>{{ settingsStore.aiCoreAddress }}</strong><h3>开发 API 地址</h3><p class="subtle">正式桌面环境由 Sidecar Manager 动态提供。</p></div></div><div class="inline-actions diagnostic-actions"><button class="button-primary" @click="settingsStore.loadDiagnostics">重新检测</button><button class="button-secondary" @click="settingsStore.restartAiCore">重启 AI Core</button></div></div>

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
