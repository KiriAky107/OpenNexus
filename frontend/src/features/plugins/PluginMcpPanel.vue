<script setup lang="ts">
import { Key, Refresh } from '@element-plus/icons-vue'
import { computed, ref, watch } from 'vue'
import AppIcon from '@/components/common/AppIcon.vue'
import PluginCommandPanel from './PluginCommandPanel.vue'
import type { Plugin, PluginHostStatus, PluginSettingField, PluginSettingsSchema } from '@/contracts'
import * as pluginService from '@/services/pluginService'
import { usePluginStore } from '@/stores/plugin'
import { useWorkspaceStore } from '@/stores/workspace'
import { t, localeTag } from '@/i18n'

const props = defineProps<{ plugin: Plugin }>()
const pluginStore = usePluginStore()
const activeTab = ref<'host' | 'settings' | 'commands'>('host')
const host = ref<PluginHostStatus | null>(null)
const schema = ref<PluginSettingsSchema | null>(null)
const values = ref<Record<string, unknown>>({})
// 明文只停留在组件内存，提交后立即清空。
const secrets = ref<Record<string, string>>({})
const loading = ref(false)
const busy = ref('')
const error = ref('')
const notice = ref('')
let loadVersion = 0

const hasSettings = computed(() => props.plugin.contributions.some((item) => item.type === 'settings_section'))
const tabs = computed(() => [
  ...(props.plugin.backend_type === 'mcp' ? [{ id: 'host' as const, label: 'MCP Host' }] : []),
  ...(hasSettings.value ? [{ id: 'settings' as const, label: t('设置与密钥', 'Settings and secrets') }] : []),
  { id: 'commands' as const, label: t('插件命令', 'Plugin commands') },
])

watch(() => props.plugin.plugin_id, () => {
  loadVersion++
  activeTab.value = props.plugin.backend_type === 'mcp' ? 'host' : hasSettings.value ? 'settings' : 'commands'
  host.value = null
  schema.value = null
  values.value = {}
  secrets.value = {}
  void loadActive()
}, { immediate: true })

function feedback(message = '') { error.value = message; notice.value = '' }
function message(reason: unknown, fallback: string) { return reason instanceof Error ? reason.message : fallback }
function formatTime(value?: string | null) { return value ? new Date(value).toLocaleString(localeTag()) : '—' }

async function selectTab(tab: typeof activeTab.value) {
  activeTab.value = tab
  await loadActive()
}
async function loadActive() {
  const version = ++loadVersion
  const pluginId = props.plugin.plugin_id
  const tab = activeTab.value
  feedback()
  loading.value = true
  try {
    if (tab === 'host') {
      const loadedHost = await pluginService.getPluginHostStatus(pluginId)
      if (version === loadVersion) host.value = loadedHost
    }
    if (tab === 'settings') {
      const loadedSchema = await pluginService.getPluginSettings(pluginId)
      if (version === loadVersion) {
        schema.value = loadedSchema
        values.value = { ...loadedSchema.values }
      }
    }
    // commands 由 PluginCommandPanel 自己加载。
  } catch (reason) {
    if (version === loadVersion) feedback(message(reason, t('MCP 数据加载失败', 'Failed to load MCP data')))
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

/** 命令返回 refresh:settings 时重新拉设置。 */
async function reloadSettings() {
  const loadedSchema = await pluginService.getPluginSettings(props.plugin.plugin_id)
  schema.value = loadedSchema
  values.value = { ...loadedSchema.values }
}
async function restartHost() {
  busy.value = 'host'
  feedback()
  try {
    await pluginService.restartPluginHost(props.plugin.plugin_id)
    host.value = await pluginService.getPluginHostStatus(props.plugin.plugin_id)
    await pluginStore.loadPlugins()
    notice.value = t('MCP Host 已重启。', 'MCP Host restarted.')
  } catch (reason) { feedback(message(reason, t('MCP Host 重启失败', 'Failed to restart MCP Host'))) } finally { busy.value = '' }
}
function updateValue(field: PluginSettingField, raw: string | boolean) {
  values.value[field.key] = field.type === 'number' && typeof raw === 'string' ? (raw === '' ? null : Number(raw)) : raw
}
async function saveSettings() {
  if (!schema.value) return
  busy.value = 'settings'
  feedback()
  try {
    schema.value = await pluginService.updatePluginSettings(props.plugin.plugin_id, schema.value.schema_version, values.value)
    values.value = { ...schema.value.values }
    notice.value = t('普通设置已保存。', 'Settings saved.')
  } catch (reason) { feedback(message(reason, t('设置保存失败', 'Failed to save settings'))) } finally { busy.value = '' }
}
async function saveSecret(field: PluginSettingField) {
  const secret = secrets.value[field.key]?.trim()
  if (!secret) { feedback(t('请输入', 'Enter ') + field.label); return }
  busy.value = 'secret:' + field.key
  feedback()
  try {
    const state = await pluginService.putPluginSecret(props.plugin.plugin_id, field.key, secret)
    if (schema.value) schema.value.secrets[field.key] = { configured: state.configured }
    secrets.value[field.key] = ''
    notice.value = field.label + t('已加密保存。', ' encrypted and saved.')
  } catch (reason) { feedback(message(reason, t('密钥保存失败', 'Failed to save secret'))) } finally { busy.value = '' }
}
async function deleteSecret(field: PluginSettingField) {
  if (!confirm(t('删除已保存的', 'Delete saved ') + field.label + '？')) return
  busy.value = 'secret:' + field.key
  feedback()
  try {
    const state = await pluginService.deletePluginSecret(props.plugin.plugin_id, field.key)
    if (schema.value) schema.value.secrets[field.key] = { configured: state.configured }
    secrets.value[field.key] = ''
    notice.value = field.label + t('已删除。', ' deleted.')
  } catch (reason) { feedback(message(reason, t('密钥删除失败', 'Failed to delete secret'))) } finally { busy.value = '' }
}
</script>

<template>
  <section class="mcp-panel">
    <nav class="mcp-tabs" :aria-label="t('MCP 与 Plugin 配置', 'MCP and Plugin settings')">
      <button v-for="tab in tabs" :key="tab.id" :class="{ active: activeTab === tab.id }" @click="selectTab(tab.id)">{{ tab.label }}</button>
    </nav>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="notice" class="notice-banner">{{ notice }}</div>

    <div v-if="activeTab === 'host'" class="mcp-section">
      <div class="section-head"><div><h3>{{ t('MCP Host 状态', 'MCP Host status') }}</h3><p>{{ t('查看协议协商、运行状态与 Host 错误。', 'Inspect protocol negotiation, runtime status, and Host errors.') }}</p></div><div class="inline-actions"><button class="button-secondary" :disabled="loading" @click="loadActive"><AppIcon :icon="Refresh" :size="15" />{{ t('刷新', 'Refresh') }}</button><button class="button-primary" :disabled="busy === 'host' || !plugin.enabled" @click="restartHost">{{ busy === 'host' ? t('重启中…', 'Restarting…') : t('重启 Host', 'Restart Host') }}</button></div></div>
      <div v-if="host" class="status-grid">
        <div><span>{{ t('状态', 'Status') }}</span><strong><i class="status-dot" :class="host.status"></i>{{ host.status }}</strong></div>
        <div><span>{{ t('服务', 'Server') }}</span><strong>{{ host.server_name || '—' }} {{ host.server_version || '' }}</strong></div>
        <div><span>{{ t('协议版本', 'Protocol version') }}</span><strong>{{ host.protocol_version || '—' }}</strong></div>
        <div><span>{{ t('工具数量', 'Tools') }}</span><strong>{{ host.tools_count }}</strong></div>
        <div><span>{{ t('启动时间', 'Started') }}</span><strong>{{ formatTime(host.started_at) }}</strong></div>
        <div><span>{{ t('最后心跳', 'Last heartbeat') }}</span><strong>{{ formatTime(host.last_seen_at) }}</strong></div>
      </div>
      <div v-else-if="loading" class="empty-state">{{ t('正在读取 Host 状态…', 'Loading Host status…') }}</div>
      <div v-if="host?.error" class="error-banner host-error">{{ host.error }}</div>
      <p class="security-hint">{{ t('当前仅运行插件清单声明的 stdio MCP Server，不开放任意 Shell 命令和环境变量编辑。', 'Only stdio MCP servers declared by the plugin manifest can run. Arbitrary shell commands and environment variable editing are unavailable.') }}</p>
    </div>

    <div v-else-if="activeTab === 'settings'" class="mcp-section">
      <div class="section-head"><div><h3>{{ t('设置与密钥', 'Settings and secrets') }}</h3><p>{{ t('表单由后端 Schema 生成；密钥不会被读取或回显。', 'The backend schema generates this form. Secrets are never read back or displayed.') }}</p></div><button class="button-primary" :disabled="!schema || busy === 'settings'" @click="saveSettings">{{ busy === 'settings' ? t('保存中…', 'Saving…') : t('保存普通设置', 'Save settings') }}</button></div>
      <div v-if="schema" class="settings-list">
        <div v-for="field in schema.fields" :key="field.key" class="setting-row">
          <div class="field-copy"><label :for="'plugin-setting-' + field.key"><AppIcon v-if="field.type === 'secret'" :icon="Key" :size="15" />{{ field.label }}<em v-if="field.required">{{ t('必填', 'Required') }}</em></label><p>{{ field.description || (field.type === 'secret' ? t('加密保存，不在页面回显。', 'Encrypted and never displayed.') : '') }}</p></div>
          <template v-if="field.type === 'secret'">
            <div class="secret-control"><input :id="'plugin-setting-' + field.key" :value="secrets[field.key] || ''" class="input" type="password" autocomplete="new-password" :placeholder="schema.secrets[field.key]?.configured ? t('已配置；输入新值可替换', 'Configured; enter a new value to replace') : t('输入密钥', 'Enter secret')" @input="secrets[field.key] = ($event.target as HTMLInputElement).value"><button class="button-secondary" :disabled="!secrets[field.key]?.trim() || busy === 'secret:' + field.key" @click="saveSecret(field)">{{ t('安全保存', 'Save securely') }}</button><button v-if="schema.secrets[field.key]?.configured" class="button-danger" @click="deleteSecret(field)">{{ t('删除', 'Delete') }}</button></div>
            <span class="secret-state" :class="{ configured: schema.secrets[field.key]?.configured }">{{ schema.secrets[field.key]?.configured ? t('已配置', 'Configured') : t('未配置', 'Not configured') }}</span>
          </template>
          <template v-else-if="field.type === 'boolean'"><label class="check-control"><input :id="'plugin-setting-' + field.key" type="checkbox" :checked="Boolean(values[field.key])" @change="updateValue(field, ($event.target as HTMLInputElement).checked)">{{ values[field.key] ? t('开启', 'On') : t('关闭', 'Off') }}</label></template>
          <template v-else-if="field.type === 'select'"><select :id="'plugin-setting-' + field.key" class="select" :value="values[field.key]" @change="updateValue(field, ($event.target as HTMLSelectElement).value)"><option v-for="option in field.options" :key="option" :value="option">{{ option }}</option></select></template>
          <template v-else><input :id="'plugin-setting-' + field.key" class="input" :type="field.type === 'number' ? 'number' : 'text'" :min="field.minimum ?? undefined" :max="field.maximum ?? undefined" :required="field.required" :value="values[field.key] ?? ''" @input="updateValue(field, ($event.target as HTMLInputElement).value)"></template>
        </div>
      </div>
      <div v-else-if="loading" class="empty-state">{{ t('正在读取 Plugin 设置…', 'Loading Plugin settings…') }}</div>
    </div>

    <div v-else class="mcp-section">
      <PluginCommandPanel :plugin="plugin" @refresh-settings="reloadSettings" />
    </div>
  </section>
</template>

<style scoped>
.mcp-panel { margin-top: var(--space-xl); padding-top: var(--space-xl); border-top: 1px solid var(--color-border-default); }
.mcp-tabs { display: flex; gap: var(--space-xs); margin-bottom: var(--space-xl); padding: var(--space-xs); border: 1px solid var(--color-border-default); border-radius: var(--radius-lg); background: var(--color-background-secondary); }
.mcp-tabs button { padding: 9px var(--space-md); border-radius: var(--radius-md); color: var(--color-text-secondary); }
.mcp-tabs button:hover { background: var(--color-background-hover); }
.mcp-tabs button.active { background: var(--color-surface-primary); color: var(--color-accent-primary); box-shadow: var(--shadow-sm); }
.mcp-section { min-height: 220px; }
.section-head, .command-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-md); margin-bottom: var(--space-lg); }
.section-head p, .command-head p { margin-top: var(--space-xs); color: var(--color-text-tertiary); font-size: var(--font-size-sm); }
.section-head button, .command-run { display: inline-flex; align-items: center; gap: var(--space-xs); }
.status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); gap: var(--space-sm); }
.status-grid > div { display: grid; gap: var(--space-xs); padding: var(--space-md); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); background: var(--color-background-secondary); }
.status-grid span { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.status-grid strong { display: flex; align-items: center; gap: var(--space-xs); font-size: var(--font-size-sm); }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-text-tertiary); }
.status-dot.ready { background: var(--color-success); box-shadow: 0 0 0 4px var(--color-success-soft); }
.status-dot.error, .status-dot.unhealthy { background: var(--color-error); box-shadow: 0 0 0 4px var(--color-error-soft); }
.status-dot.starting { background: var(--color-warning); box-shadow: 0 0 0 4px var(--color-warning-soft); }
.security-hint { margin-top: var(--space-lg); padding: var(--space-md); border-left: 3px solid var(--color-info); background: var(--color-info-soft); color: var(--color-text-secondary); font-size: var(--font-size-sm); }
.host-error { margin-top: var(--space-lg); }
.settings-list { display: grid; }
.setting-row { display: grid; grid-template-columns: minmax(180px, .9fr) minmax(260px, 1.1fr) auto; align-items: center; gap: var(--space-lg); padding: var(--space-lg) 0; border-bottom: 1px solid var(--color-border-subtle); }
.field-copy label { display: flex; align-items: center; gap: var(--space-xs); font-weight: 650; }
.field-copy p { margin-top: var(--space-xs); color: var(--color-text-tertiary); font-size: var(--font-size-sm); }
em { margin-left: var(--space-xs); color: var(--color-error); font-size: var(--font-size-xs); font-style: normal; }
.secret-control { display: flex; gap: var(--space-xs); }
.secret-state { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.secret-state.configured { color: var(--color-success); }
.check-control { display: flex; align-items: center; gap: var(--space-sm); color: var(--color-text-secondary); }
.check-control input { width: 18px; height: 18px; accent-color: var(--color-accent-primary); }
.command-list, .command-card { display: grid; gap: var(--space-sm); }
.command-card:hover { transform: none; }
.command-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: var(--space-md); }
.command-run { justify-self: end; }
@media (max-width: 800px) { .mcp-tabs { overflow-x: auto; } .mcp-tabs button { flex: 0 0 auto; } .setting-row { grid-template-columns: 1fr; gap: var(--space-sm); } .secret-control { flex-wrap: wrap; } }
</style>
