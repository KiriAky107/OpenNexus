<script setup lang="ts">
import { Key, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '@/components/common/AppIcon.vue'
import type { Plugin, PluginCommand, PluginHostStatus, PluginSettingField, PluginSettingsSchema } from '@/contracts'
import * as pluginService from '@/services/pluginService'
import { useEditorStore } from '@/stores/editor'
import { usePluginStore } from '@/stores/plugin'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ plugin: Plugin }>()
const pluginStore = usePluginStore()
const editorStore = useEditorStore()
const workspaceStore = useWorkspaceStore()
const router = useRouter()
const activeTab = ref<'host' | 'settings' | 'commands'>('host')
const host = ref<PluginHostStatus | null>(null)
const schema = ref<PluginSettingsSchema | null>(null)
const values = ref<Record<string, unknown>>({})
// 明文只停留在组件内存，提交后立即清空。
const secrets = ref<Record<string, string>>({})
const commands = ref<PluginCommand[]>([])
const argumentsByCommand = ref<Record<string, Record<string, unknown>>>({})
const loading = ref(false)
const busy = ref('')
const error = ref('')
const notice = ref('')
let loadVersion = 0

const hasSettings = computed(() => props.plugin.contributions.some((item) => item.type === 'settings_section'))
const tabs = computed(() => [
  ...(props.plugin.backend_type === 'mcp' ? [{ id: 'host' as const, label: 'MCP Host' }] : []),
  ...(hasSettings.value ? [{ id: 'settings' as const, label: '设置与密钥' }] : []),
  { id: 'commands' as const, label: '插件命令' },
])

watch(() => props.plugin.plugin_id, () => {
  loadVersion++
  activeTab.value = props.plugin.backend_type === 'mcp' ? 'host' : hasSettings.value ? 'settings' : 'commands'
  host.value = null
  schema.value = null
  values.value = {}
  secrets.value = {}
  commands.value = []
  void loadActive()
}, { immediate: true })

function feedback(message = '') { error.value = message; notice.value = '' }
function message(reason: unknown, fallback: string) { return reason instanceof Error ? reason.message : fallback }
function formatTime(value?: string | null) { return value ? new Date(value).toLocaleString() : '—' }

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
    if (tab === 'commands') {
      const loadedCommands = (await pluginService.listPluginCommands()).filter((command) => command.plugin_id === pluginId)
      if (version === loadVersion) {
        commands.value = loadedCommands
        for (const command of loadedCommands) argumentsByCommand.value[command.command_id] = {}
      }
    }
  } catch (reason) {
    if (version === loadVersion) feedback(message(reason, 'MCP 数据加载失败'))
  } finally {
    if (version === loadVersion) loading.value = false
  }
}
async function restartHost() {
  busy.value = 'host'
  feedback()
  try {
    await pluginService.restartPluginHost(props.plugin.plugin_id)
    host.value = await pluginService.getPluginHostStatus(props.plugin.plugin_id)
    await pluginStore.loadPlugins()
    notice.value = 'MCP Host 已重启。'
  } catch (reason) { feedback(message(reason, 'MCP Host 重启失败')) } finally { busy.value = '' }
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
    notice.value = '普通设置已保存。'
  } catch (reason) { feedback(message(reason, '设置保存失败')) } finally { busy.value = '' }
}
async function saveSecret(field: PluginSettingField) {
  const secret = secrets.value[field.key]?.trim()
  if (!secret) { feedback('请输入' + field.label); return }
  busy.value = 'secret:' + field.key
  feedback()
  try {
    const state = await pluginService.putPluginSecret(props.plugin.plugin_id, field.key, secret)
    if (schema.value) schema.value.secrets[field.key] = { configured: state.configured }
    secrets.value[field.key] = ''
    notice.value = field.label + '已加密保存。'
  } catch (reason) { feedback(message(reason, '密钥保存失败')) } finally { busy.value = '' }
}
async function deleteSecret(field: PluginSettingField) {
  if (!confirm('删除已保存的' + field.label + '？')) return
  busy.value = 'secret:' + field.key
  feedback()
  try {
    const state = await pluginService.deletePluginSecret(props.plugin.plugin_id, field.key)
    if (schema.value) schema.value.secrets[field.key] = { configured: state.configured }
    secrets.value[field.key] = ''
    notice.value = field.label + '已删除。'
  } catch (reason) { feedback(message(reason, '密钥删除失败')) } finally { busy.value = '' }
}
function properties(command: PluginCommand): Record<string, Record<string, unknown>> {
  const result = command.parameters.properties
  return result && typeof result === 'object' && !Array.isArray(result) ? result as Record<string, Record<string, unknown>> : {}
}
function required(command: PluginCommand, key: string) {
  return Array.isArray(command.parameters.required) && command.parameters.required.includes(key)
}
function commandAvailable(command: PluginCommand) {
  if (!command.enabled) return false
  return command.when.every((condition) => {
    if (condition === 'workspace.has_vault') return Boolean(workspaceStore.vaultId)
    if (condition === 'editor.has_note') return Boolean(editorStore.currentNoteId)
    // Plugin 详情页不冒充编辑器选区；选区命令应从命令面板或编辑器挂载点执行。
    if (condition === 'editor.has_selection') return false
    return false
  })
}
function updateArgument(commandId: string, key: string, raw: string, definition: Record<string, unknown>) {
  const target = argumentsByCommand.value[commandId] ??= {}
  if (definition.type === 'number' || definition.type === 'integer') target[key] = raw === '' ? undefined : Number(raw)
  else if (definition.type === 'boolean') target[key] = raw === 'true'
  else target[key] = raw
}
async function execute(command: PluginCommand) {
  busy.value = command.command_id
  feedback()
  try {
    const result = await pluginService.executePluginCommand(command.command_id, argumentsByCommand.value[command.command_id] ?? {}, {
      vault_id: workspaceStore.hasVault ? workspaceStore.vaultId : null,
      note_id: editorStore.currentNoteId,
      file_path: editorStore.currentFilePath,
      selection: null,
    })
    if (result.effect.type === 'notification') notice.value = result.effect.payload.message
    else if (result.effect.type === 'job') notice.value = '后台任务已创建：' + result.effect.payload.job_id
    else if (result.effect.type === 'navigate') {
      const routes: Record<string, string> = {
        'vault-entry': '/', workspace: '/workspace', search: '/search', chat: '/chat',
        agent: '/agent/runs', tasks: '/tasks', skills: '/extensions/skills',
        plugins: '/extensions/plugins', themes: '/themes', settings: '/settings',
      }
      await router.push(routes[result.effect.payload.route])
    } else if (result.effect.type === 'refresh') {
      await loadActive()
      notice.value = '相关数据已刷新。'
    } else notice.value = '命令执行完成。'
  } catch (reason) { feedback(message(reason, '命令执行失败')) } finally { busy.value = '' }
}
</script>

<template>
  <section class="mcp-panel">
    <nav class="mcp-tabs" aria-label="MCP 与 Plugin 配置">
      <button v-for="tab in tabs" :key="tab.id" :class="{ active: activeTab === tab.id }" @click="selectTab(tab.id)">{{ tab.label }}</button>
    </nav>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="notice" class="notice-banner">{{ notice }}</div>

    <div v-if="activeTab === 'host'" class="mcp-section">
      <div class="section-head"><div><h3>MCP Host 状态</h3><p>查看协议协商、运行状态与 Host 错误。</p></div><div class="inline-actions"><button class="button-secondary" :disabled="loading" @click="loadActive"><AppIcon :icon="Refresh" :size="15" />刷新</button><button class="button-primary" :disabled="busy === 'host' || !plugin.enabled" @click="restartHost">{{ busy === 'host' ? '重启中…' : '重启 Host' }}</button></div></div>
      <div v-if="host" class="status-grid">
        <div><span>状态</span><strong><i class="status-dot" :class="host.status"></i>{{ host.status }}</strong></div>
        <div><span>服务</span><strong>{{ host.server_name || '—' }} {{ host.server_version || '' }}</strong></div>
        <div><span>协议版本</span><strong>{{ host.protocol_version || '—' }}</strong></div>
        <div><span>工具数量</span><strong>{{ host.tools_count }}</strong></div>
        <div><span>启动时间</span><strong>{{ formatTime(host.started_at) }}</strong></div>
        <div><span>最后心跳</span><strong>{{ formatTime(host.last_seen_at) }}</strong></div>
      </div>
      <div v-else-if="loading" class="empty-state">正在读取 Host 状态…</div>
      <div v-if="host?.error" class="error-banner host-error">{{ host.error }}</div>
      <p class="security-hint">当前仅运行插件清单声明的 stdio MCP Server，不开放任意 Shell 命令和环境变量编辑。</p>
    </div>

    <div v-else-if="activeTab === 'settings'" class="mcp-section">
      <div class="section-head"><div><h3>设置与密钥</h3><p>表单由后端 Schema 生成；密钥不会被读取或回显。</p></div><button class="button-primary" :disabled="!schema || busy === 'settings'" @click="saveSettings">{{ busy === 'settings' ? '保存中…' : '保存普通设置' }}</button></div>
      <div v-if="schema" class="settings-list">
        <div v-for="field in schema.fields" :key="field.key" class="setting-row">
          <div class="field-copy"><label :for="'plugin-setting-' + field.key"><AppIcon v-if="field.type === 'secret'" :icon="Key" :size="15" />{{ field.label }}<em v-if="field.required">必填</em></label><p>{{ field.description || (field.type === 'secret' ? '加密保存，不在页面回显。' : '') }}</p></div>
          <template v-if="field.type === 'secret'">
            <div class="secret-control"><input :id="'plugin-setting-' + field.key" :value="secrets[field.key] || ''" class="input" type="password" autocomplete="new-password" :placeholder="schema.secrets[field.key]?.configured ? '已配置；输入新值可替换' : '输入密钥'" @input="secrets[field.key] = ($event.target as HTMLInputElement).value"><button class="button-secondary" :disabled="!secrets[field.key]?.trim() || busy === 'secret:' + field.key" @click="saveSecret(field)">安全保存</button><button v-if="schema.secrets[field.key]?.configured" class="button-danger" @click="deleteSecret(field)">删除</button></div>
            <span class="secret-state" :class="{ configured: schema.secrets[field.key]?.configured }">{{ schema.secrets[field.key]?.configured ? '已配置' : '未配置' }}</span>
          </template>
          <template v-else-if="field.type === 'boolean'"><label class="check-control"><input :id="'plugin-setting-' + field.key" type="checkbox" :checked="Boolean(values[field.key])" @change="updateValue(field, ($event.target as HTMLInputElement).checked)">{{ values[field.key] ? '开启' : '关闭' }}</label></template>
          <template v-else-if="field.type === 'select'"><select :id="'plugin-setting-' + field.key" class="select" :value="values[field.key]" @change="updateValue(field, ($event.target as HTMLSelectElement).value)"><option v-for="option in field.options" :key="option" :value="option">{{ option }}</option></select></template>
          <template v-else><input :id="'plugin-setting-' + field.key" class="input" :type="field.type === 'number' ? 'number' : 'text'" :min="field.minimum ?? undefined" :max="field.maximum ?? undefined" :required="field.required" :value="values[field.key] ?? ''" @input="updateValue(field, ($event.target as HTMLInputElement).value)"></template>
        </div>
      </div>
      <div v-else-if="loading" class="empty-state">正在读取 Plugin 设置…</div>
    </div>

    <div v-else class="mcp-section">
      <div class="section-head"><div><h3>Plugin 命令</h3><p>执行该 Plugin 注册的受控 Command Contribution。</p></div><button class="button-secondary" :disabled="loading" @click="loadActive"><AppIcon :icon="Refresh" :size="15" />刷新</button></div>
      <div v-if="commands.length" class="command-list">
        <article v-for="command in commands" :key="command.command_id" class="item-card command-card">
          <div class="command-head"><div><strong>{{ command.title }}</strong><p>{{ command.description || command.command_id }}</p></div><span class="badge" :class="{ success: commandAvailable(command), warning: command.enabled && !commandAvailable(command) }">{{ commandAvailable(command) ? '可执行' : command.enabled ? '缺少上下文' : '不可用' }}</span></div>
          <div v-if="Object.keys(properties(command)).length" class="command-fields">
            <label v-for="(definition, key) in properties(command)" :key="key" class="field"><span>{{ String(definition.title || key) }}<em v-if="required(command, key)">必填</em></span><select v-if="Array.isArray(definition.enum)" class="select" @change="updateArgument(command.command_id, key, ($event.target as HTMLSelectElement).value, definition)"><option value="">请选择</option><option v-for="option in definition.enum" :key="String(option)" :value="String(option)">{{ option }}</option></select><select v-else-if="definition.type === 'boolean'" class="select" @change="updateArgument(command.command_id, key, ($event.target as HTMLSelectElement).value, definition)"><option value="false">否</option><option value="true">是</option></select><input v-else class="input" :type="definition.type === 'number' || definition.type === 'integer' ? 'number' : 'text'" @input="updateArgument(command.command_id, key, ($event.target as HTMLInputElement).value, definition)"></label>
          </div>
          <button class="button-primary command-run" :disabled="!commandAvailable(command) || busy === command.command_id" @click="execute(command)"><AppIcon :icon="VideoPlay" :size="15" />{{ busy === command.command_id ? '执行中…' : '执行命令' }}</button>
        </article>
      </div>
      <div v-else-if="!loading" class="empty-state"><div><strong>没有可用命令</strong><p>启用 Plugin 后，已注册的命令会出现在这里。</p></div></div>
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
