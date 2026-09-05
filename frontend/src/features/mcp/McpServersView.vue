<script setup lang="ts">
import AppDialog from '@/components/common/AppDialog.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { Connection, Delete, EditPen, Plus, Refresh, VideoPlay } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import type { McpServer, McpServerInput, McpServerTransport } from '@/contracts'
import * as service from '@/services/mcpServerService'
import { emptyMcpConfig, mergeImportedSecrets, normalizeMcpConfig, parseMcpJson, type ImportedSecret, type SecretKind } from './configuration'
import { t } from '@/i18n'

const servers = ref<McpServer[]>([])
const busy = ref('')
const error = ref('')
const dialogOpen = ref(false)
const editingId = ref<string | null>(null)
const editingOriginal = ref<McpServer | null>(null)
const editorMode = ref<'form' | 'json'>('form')
const argsText = ref('')
const environmentText = ref('{}')
const headersText = ref('{}')
const secretKeysText = ref('')
const secretHeaderKeysText = ref('')
const permissionsText = ref('')
const rawConfig = ref('')
const secretDrafts = reactive<Record<string, string>>({})
const form = reactive<McpServerInput>(emptyMcpConfig())
const importedSecrets = ref<ImportedSecret[]>([])

const dialogTitle = computed(() => editingId.value ? t('编辑 MCP 服务器', 'Edit MCP Server') : t('新增 MCP 服务器', 'Add MCP Server'))

async function load() {
  error.value = ''
  try { servers.value = await service.listMcpServers() }
  catch (cause) { error.value = message(cause, t('读取 MCP 服务器失败', 'Failed to load MCP servers')) }
}

function resetEditor(input: McpServerInput) {
  Object.assign(form, emptyMcpConfig(), { version: undefined }, input)
  argsText.value = input.args.join('\n')
  environmentText.value = JSON.stringify(input.environment, null, 2)
  headersText.value = JSON.stringify(input.headers, null, 2)
  secretKeysText.value = input.secret_environment_keys.join('\n')
  secretHeaderKeysText.value = input.secret_header_keys.join('\n')
  permissionsText.value = input.permissions.join(', ')
  editorMode.value = 'form'
  rawConfig.value = ''
}

function openCreate() {
  if (busy.value) return
  error.value = ''
  importedSecrets.value = []
  editingId.value = null
  editingOriginal.value = null
  resetEditor(emptyMcpConfig())
  dialogOpen.value = true
}

function openEdit(server: McpServer) {
  if (busy.value) return
  error.value = ''
  importedSecrets.value = []
  editingId.value = server.server_id
  editingOriginal.value = server
  resetEditor({
    version: server.version, name: server.name, transport: server.transport,
    command: server.command, args: [...server.args], url: server.url,
    headers: { ...server.headers }, environment: { ...server.environment },
    secret_environment_keys: Object.keys(server.secret_environment),
    secret_header_keys: Object.keys(server.secret_headers), permissions: [...server.permissions],
    startup_timeout_seconds: server.startup_timeout_seconds,
    tool_timeout_seconds: server.tool_timeout_seconds,
  })
  dialogOpen.value = true
}

function applyTemplate(transport: McpServerTransport) {
  form.transport = transport
  if (transport === 'stdio') {
    form.command = 'uvx'; form.url = null
    argsText.value = '--isolated\n--from\npackage-name==1.0.0\nserver-command'
  } else {
    form.command = null; argsText.value = ''; form.url = transport === 'sse' ? 'http://127.0.0.1:3000/sse' : 'http://127.0.0.1:3000/mcp'
  }
}

function parseObject(value: string, label: string): Record<string, string> {
  let parsed: unknown
  try { parsed = JSON.parse(value || '{}') } catch { throw new Error(`${label}${t('必须是 JSON 对象', ' must be a JSON object')}`) }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.values(parsed).some(item => typeof item !== 'string')) throw new Error(`${label}${t('必须是字符串键值 JSON 对象', ' must be a JSON object with string keys and values')}`)
  return parsed as Record<string, string>
}

function formPayload(): McpServerInput {
  const stdio = form.transport === 'stdio'
  return {
    version: form.version,
    name: form.name.trim(), transport: form.transport,
    command: stdio ? form.command?.trim() : null,
    args: stdio ? argsText.value.split('\n').map(value => value.trim()).filter(Boolean) : [],
    url: stdio ? null : form.url?.trim(),
    headers: stdio ? {} : parseObject(headersText.value, t('普通 Header', 'Headers')),
    environment: stdio ? parseObject(environmentText.value, t('普通环境变量', 'Environment variables')) : {},
    secret_environment_keys: stdio ? splitKeys(secretKeysText.value) : [],
    secret_header_keys: stdio ? [] : splitKeys(secretHeaderKeysText.value),
    permissions: permissionsText.value.split(',').map(value => value.trim()).filter(Boolean),
    startup_timeout_seconds: form.startup_timeout_seconds,
    tool_timeout_seconds: form.tool_timeout_seconds,
  }
}

function payload(requireConnection = true): McpServerInput {
  const { config, secrets } = editorMode.value === 'form'
    ? normalizeMcpConfig(formPayload(), '', requireConnection) : parseMcpJson(rawConfig.value, form.name, requireConnection)
  // Keep only still-declared drafts. A mode switch must not discard imported keys,
  // and editing the declaration must not later send a removed key to the Secret API.
  importedSecrets.value = mergeImportedSecrets(config, importedSecrets.value, secrets)
  if (editingId.value) config.version = form.version
  if (editorMode.value === 'json') rawConfig.value = JSON.stringify(config, null, 2)
  else {
    environmentText.value = JSON.stringify(config.environment, null, 2)
    headersText.value = JSON.stringify(config.headers, null, 2)
    secretKeysText.value = config.secret_environment_keys.join('\n')
    secretHeaderKeysText.value = config.secret_header_keys.join('\n')
  }
  return config
}

function switchMode(mode: 'form' | 'json') {
  try {
    if (mode === editorMode.value) return
    error.value = ''
    if (mode === 'json') rawConfig.value = JSON.stringify(payload(false), null, 2)
    else resetEditor(payload(false))
    editorMode.value = mode
  } catch (cause) { error.value = message(cause, t('配置转换失败', 'Configuration conversion failed')) }
}

async function save() {
  if (busy.value) return
  let saved: McpServer | undefined
  try {
    error.value = ''
    const input = payload()
    if (!input.name || (input.transport === 'stdio' ? !input.command : !input.url)) throw new Error(t('请填写服务器名称和连接地址', 'Enter a server name and connection address'))
    if (editingOriginal.value && executionChanged(editingOriginal.value, input) && !confirm(t('连接命令、地址或认证配置已变化，保存后旧测试与授权会失效。是否保存？', 'The command, address, or authentication settings changed. Previous tests and authorization will be invalidated. Save?'))) return
    busy.value = 'save'
    saved = editingId.value ? await service.updateMcpServer(editingId.value, input) : await service.createMcpServer(input)
    // Commit the returned ID/version before saving secrets so a partial failure can
    // retry this server instead of creating a duplicate or sending a stale version.
    editingId.value = saved.server_id
    editingOriginal.value = saved
    resetEditor({ ...input, version: saved.version })
    for (const item of [...importedSecrets.value]) {
      await service.putMcpServerSecret(saved.server_id, item.key, item.value, item.kind)
      importedSecrets.value = importedSecrets.value.filter(candidate => candidate !== item)
    }
    closeEditor()
    await load()
  } catch (cause) {
    if (saved) await load()
    error.value = `${saved ? t('服务器配置已保存，但密钥保存失败；可点击保存重试。', 'Server settings were saved, but saving secrets failed. Save again to retry.') : ''}${message(cause, t('保存失败', 'Save failed'))}`
  }
  finally { busy.value = '' }
}

function closeEditor() {
  importedSecrets.value = []
  rawConfig.value = ''
  environmentText.value = '{}'
  headersText.value = '{}'
  dialogOpen.value = false
}

function executionChanged(server: McpServer, input: McpServerInput) {
  const sortedEntries = (value: Record<string, string>) => Object.entries(value).sort(([left], [right]) => left.localeCompare(right))
  const current = [
    server.transport, server.command, server.args, server.url,
    sortedEntries(server.headers), sortedEntries(server.environment),
    Object.keys(server.secret_headers).sort(), Object.keys(server.secret_environment).sort(),
    [...server.permissions].sort(), server.startup_timeout_seconds, server.tool_timeout_seconds,
  ]
  const next = [
    input.transport, input.command, input.args, input.url,
    sortedEntries(input.headers), sortedEntries(input.environment),
    [...input.secret_header_keys].sort(), [...input.secret_environment_keys].sort(),
    [...input.permissions].sort(), input.startup_timeout_seconds, input.tool_timeout_seconds,
  ]
  return JSON.stringify(current) !== JSON.stringify(next)
}

async function approve(server: McpServer): Promise<McpServer | null> {
  if (server.trusted) return server
  const localWarning = server.transport === 'stdio' ? t('\n\n本机进程尚无系统级沙箱，仅应运行可信服务器。', '\n\nLocal processes have no system-level sandbox. Run trusted servers only.') : t('\n\n连接可能向该地址发送配置的 Header。', '\n\nThe connection may send configured headers to this address.')
  if (!confirm(`${t('请确认 MCP 连接：', 'Confirm MCP connection:')}\n\n${server.command_summary}${localWarning}\n\n${t('是否继续？', 'Continue?')}`)) return null
  return service.trustMcpServer(server)
}

async function test(server: McpServer) { await act(server, 'test', current => service.testMcpServer(current.server_id)) }
async function toggle(server: McpServer) { await act(server, 'toggle', current => current.enabled ? service.disableMcpServer(current.server_id) : service.enableMcpServer(current.server_id)) }
async function act(server: McpServer, action: string, operation: (server: McpServer) => Promise<McpServer>) {
  busy.value = `${action}:${server.server_id}`; error.value = ''
  try { const current = action === 'toggle' && server.enabled ? server : await approve(server); if (!current) return; await operation(current); await load() }
  catch (cause) { error.value = message(cause, t('操作失败', 'Operation failed')) }
  finally { busy.value = '' }
}

async function remove(server: McpServer) {
  if (!confirm(t(`删除“${server.name}”及其加密凭据？`, `Delete “${server.name}” and its encrypted credentials?`))) return
  try { busy.value = `delete:${server.server_id}`; await service.deleteMcpServer(server.server_id); await load() }
  catch (cause) { error.value = message(cause, t('删除失败', 'Delete failed')) } finally { busy.value = '' }
}

async function saveSecret(server: McpServer, key: string, kind: SecretKind) {
  const draftKey = `${server.server_id}:${kind}:${key}`
  const value = secretDrafts[draftKey]?.trim()
  if (!value) return
  try { busy.value = `secret:${draftKey}`; await service.putMcpServerSecret(server.server_id, key, value, kind); secretDrafts[draftKey] = ''; await load() }
  catch (cause) { error.value = message(cause, t('保存密钥失败', 'Failed to save secret')) } finally { busy.value = '' }
}

function splitKeys(value: string) { return value.split(/[\n,]/).map(item => item.trim()).filter(Boolean) }
function message(cause: unknown, fallback: string) { return cause instanceof Error ? cause.message : fallback }
onMounted(load)
</script>

<template>
  <section class="feature-page mcp-page">
    <header class="feature-header"><div><h1>{{ t('MCP 服务器', 'MCP Servers') }}</h1><p>{{ t('管理独立 MCP Server 的连接、凭据与工具生命周期。', 'Manage standalone MCP server connections, credentials, and tool lifecycles.') }}</p></div><div class="inline-actions"><button class="button-secondary" :disabled="!!busy" @click="load"><AppIcon :icon="Refresh" /> {{ t('刷新', 'Refresh') }}</button><button class="button-primary" @click="openCreate"><AppIcon :icon="Plus" /> {{ t('新增服务器', 'Add server') }}</button></div></header>
    <div class="notice-banner">{{ t('stdio 本机进程仅在开发环境开放；Streamable HTTP 为首选远程传输，SSE 仅用于兼容旧服务器。uvx 隔离依赖但不是安全沙箱。', 'Local stdio processes are available only in development. Streamable HTTP is the preferred remote transport; SSE supports legacy servers. uvx isolates dependencies but is not a security sandbox.') }}</div>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="!servers.length" class="panel empty"><AppIcon :icon="Connection" :size="34" /><h2>{{ t('尚未配置 MCP 服务器', 'No MCP servers configured') }}</h2><p>{{ t('添加 Server，测试连接成功后才能启用工具。', 'Add a server and test its connection before enabling its tools.') }}</p><button class="button-primary" @click="openCreate">{{ t('新增服务器', 'Add server') }}</button></div>
    <div v-else class="server-list">
      <article v-for="server in servers" :key="server.server_id" class="panel server-card">
        <div class="server-main"><div class="server-title"><AppIcon :icon="Connection" :size="24" /><div><h2>{{ server.name }}</h2><code>{{ server.command_summary }}</code></div></div><span class="badge" :class="{ success: server.status === 'ready', error: ['error','unhealthy'].includes(server.status) }">{{ server.status }}</span></div>
        <div class="metadata"><span>{{ server.transport }}</span><span>v{{ server.version }}</span><span>{{ server.tools_count }} {{ t('个工具', 'tools') }}</span><span>{{ server.trusted ? t('连接已确认', 'Connection confirmed') : t('等待确认连接', 'Awaiting confirmation') }}</span><span v-if="server.last_test_succeeded">{{ t('当前配置测试成功', 'Current configuration passed') }}</span><span v-if="server.remote_server_name">{{ server.remote_server_name }} {{ server.remote_server_version }}</span></div>
        <div v-if="server.error" class="error-banner compact">{{ server.error }}</div>
        <div v-if="Object.keys(server.secret_environment).length || Object.keys(server.secret_headers).length" class="secrets">
          <label v-for="(configured, key) in server.secret_environment" :key="`env:${key}`"><span>{{ t('环境变量', 'Environment variable') }} · {{ key }} <small>{{ configured ? t('已加密保存', 'Encrypted and saved') : t('未配置', 'Not configured') }}</small></span><span class="secret-input"><input v-model="secretDrafts[`${server.server_id}:environment:${key}`]" type="password" autocomplete="new-password" :placeholder="t('输入后保存（不会回显）', 'Enter and save (never displayed)')"><button class="button-secondary" @click="saveSecret(server, key, 'environment')">{{ t('保存', 'Save') }}</button></span></label>
          <label v-for="(configured, key) in server.secret_headers" :key="`header:${key}`"><span>HTTP Header · {{ key }} <small>{{ configured ? t('已加密保存', 'Encrypted and saved') : t('未配置', 'Not configured') }}</small></span><span class="secret-input"><input v-model="secretDrafts[`${server.server_id}:header:${key}`]" type="password" autocomplete="new-password" :placeholder="t('输入后保存（不会回显）', 'Enter and save (never displayed)')"><button class="button-secondary" @click="saveSecret(server, key, 'header')">{{ t('保存', 'Save') }}</button></span></label>
        </div>
        <footer class="card-actions"><button class="button-secondary" :disabled="!!busy || server.enabled" @click="test(server)"><AppIcon :icon="VideoPlay" /> {{ t('测试连接', 'Test connection') }}</button><button class="button-secondary" :disabled="!!busy" @click="openEdit(server)"><AppIcon :icon="EditPen" /> {{ t('编辑', 'Edit') }}</button><button class="button-danger" :disabled="!!busy" @click="remove(server)"><AppIcon :icon="Delete" /> {{ t('删除', 'Delete') }}</button><button class="button-primary" :disabled="!!busy || (!server.enabled && !server.last_test_succeeded)" :title="!server.enabled && !server.last_test_succeeded ? t('请先测试当前配置', 'Test the current configuration first') : ''" @click="toggle(server)">{{ server.enabled ? t('停用', 'Disable') : t('启用', 'Enable') }}</button></footer>
      </article>
    </div>

    <AppDialog v-if="dialogOpen" :label="dialogTitle" :dismissible="!busy" @close="closeEditor">
      <form class="modal-card" @submit.prevent="save">
        <fieldset :disabled="!!busy" class="editor-fields">
        <header><h2><AppIcon :icon="Plus" /> {{ dialogTitle }}</h2><button type="button" class="close" @click="closeEditor">×</button></header>
        <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
        <div v-if="importedSecrets.length" class="notice-banner">{{ t('已识别', 'Detected') }} {{ importedSecrets.length }} {{ t('项密钥，保存时将单独加密，不会写入普通服务器配置；取消将清除未保存密钥。', 'secrets. They will be encrypted separately and excluded from regular server settings. Canceling clears unsaved secrets.') }}</div>
        <div class="mode-tabs"><button type="button" :class="{ active: editorMode === 'form' }" @click="switchMode('form')">{{ t('表单配置', 'Form') }}</button><button type="button" :class="{ active: editorMode === 'json' }" @click="switchMode('json')">{{ t('JSON 配置', 'JSON') }}</button></div>
        <template v-if="editorMode === 'form'">
          <label>{{ t('服务器名称', 'Server name') }}<input v-model="form.name" maxlength="80" :placeholder="t('例如：文件系统工具', 'For example: Filesystem tools')"></label>
          <div class="template-row"><span>{{ t('服务器配置', 'Server configuration') }}</span><button type="button" class="template" :class="{ active: form.transport === 'stdio' }" @click="applyTemplate('stdio')">stdio {{ t('模板', 'template') }}</button><button type="button" class="template" :class="{ active: form.transport === 'streamable_http' }" @click="applyTemplate('streamable_http')">Streamable HTTP</button><button type="button" class="template" :class="{ active: form.transport === 'sse' }" @click="applyTemplate('sse')">SSE {{ t('（兼容）', '(legacy)') }}</button></div>
          <template v-if="form.transport === 'stdio'"><label>{{ t('可执行命令', 'Executable command') }}<input v-model="form.command" :placeholder="t('uvx、npx 或可信可执行文件路径', 'uvx, npx, or a trusted executable path')"></label><label>{{ t('参数（每行一项）', 'Arguments (one per line)') }}<textarea v-model="argsText" rows="5"></textarea></label><div class="two-columns"><label>{{ t('普通环境变量（JSON）', 'Environment variables (JSON)') }}<textarea v-model="environmentText" rows="5"></textarea></label><label>{{ t('敏感环境变量名（每行一项）', 'Secret environment names (one per line)') }}<textarea v-model="secretKeysText" rows="5" placeholder="API_KEY"></textarea></label></div></template>
          <template v-else><label>MCP URL<input v-model="form.url" placeholder="https://example.com/mcp"></label><div class="two-columns"><label>{{ t('普通 Header（JSON）', 'Headers (JSON)') }}<textarea v-model="headersText" rows="5" placeholder='{"X-Client":"NotesAgent"}'></textarea></label><label>{{ t('敏感 Header 名（每行一项）', 'Secret header names (one per line)') }}<textarea v-model="secretHeaderKeysText" rows="5" placeholder="Authorization"></textarea></label></div></template>
          <label>{{ t('声明权限（逗号分隔，可选）', 'Declared permissions (comma-separated, optional)') }}<input v-model="permissionsText" placeholder="network.request, notes.read"></label>
          <div class="two-columns"><label>{{ t('启动超时（秒）', 'Startup timeout (seconds)') }}<input v-model.number="form.startup_timeout_seconds" type="number" min="1" max="120"></label><label>{{ t('工具超时（秒）', 'Tool timeout (seconds)') }}<input v-model.number="form.tool_timeout_seconds" type="number" min="1" max="300"></label></div>
        </template>
        <label v-else>{{ t('服务器 JSON 配置', 'Server JSON configuration') }}<textarea v-model="rawConfig" class="json-editor" rows="22" spellcheck="false"></textarea><small>{{ t('支持 NotesAgent 配置、command/args/env 和单服务器 mcpServers 配置。已声明的 Secret 及常见 API Key、Token、Authorization 会拆分后加密保存。其他敏感值请显式声明；不要把密钥放入命令或参数。', 'Supports NotesAgent, command/args/env, and single-server mcpServers configurations. Declared secrets and common API key, token, and authorization values are separated and encrypted. Declare other sensitive values explicitly; never place secrets in commands or arguments.') }}</small><small>{{ t('兼容导入 timeout 为启动超时，sse_read_timeout 为工具等待上限（不保留 SSE 读取超时语义）。', 'For compatible imports, timeout maps to startup timeout and sse_read_timeout maps to the tool wait limit.') }}</small></label>
        <footer><button type="button" class="button-secondary" @click="closeEditor">{{ t('取消', 'Cancel') }}</button><button class="button-primary" :disabled="busy === 'save'">{{ t('保存', 'Save') }}</button></footer>
        </fieldset>
      </form>
    </AppDialog>
  </section>
</template>

<style scoped>
.editor-fields { display: grid; gap: var(--space-lg); border: 0; padding: 0; margin: 0; min-width: 0; }
.mcp-page { overflow: auto; }
.mcp-page > :is(.feature-header, .notice-banner, .error-banner, .server-list, .empty) { box-sizing: border-box; width: 100%; max-width: 1180px; margin-inline: auto; }
.server-list { grid-template-columns: minmax(0, 1fr); }
.server-card, .server-main, .server-title, .server-title > div, .secret-input { min-width: 0; }
.server-title { flex: 1; }
.server-title h2, .metadata span, .secrets label > span { overflow-wrap: anywhere; }
.server-title code { display: block; white-space: pre-wrap; }
.server-main > .badge { flex-shrink: 0; }
.secret-input input { min-width: 0; width: 100%; border: 1px solid var(--color-border-default); border-radius: var(--radius-sm); padding: 8px 10px; background: var(--color-background-secondary); color: var(--color-text-primary); font: inherit; }
.secret-input button { flex-shrink: 0; }
.notice-banner,.error-banner { margin-bottom: var(--space-lg); }.server-list { display: grid; gap: var(--space-lg); }.server-card { display: grid; gap: var(--space-md); }
.server-main,.server-title,.metadata,.card-actions,.inline-actions,.template-row,.modal-card header,.modal-card footer { display: flex; align-items: center; gap: var(--space-sm); }.server-main { justify-content: space-between; }.server-title { align-items: flex-start; }.server-title h2 { margin-bottom: 4px; }.server-title code { color: var(--color-text-secondary); overflow-wrap: anywhere; }.metadata { flex-wrap: wrap; color: var(--color-text-tertiary); font-size: var(--font-size-sm); }.metadata span + span::before { content: '·'; margin-right: var(--space-sm); }.compact { margin: 0; }
.card-actions { flex-wrap: wrap; justify-content: flex-end; border-top: 1px solid var(--color-border-subtle); padding-top: var(--space-md); }.empty { text-align: center; place-items: center; display: grid; gap: var(--space-md); padding: 64px; }.secrets { border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); padding: var(--space-md); display: grid; gap: var(--space-sm); }.secrets label { display: grid; grid-template-columns: minmax(0,.7fr) minmax(0,1fr); align-items: center; gap: var(--space-md); }.secrets small,.modal-card small { color: var(--color-text-tertiary); }.secret-input { display: flex; gap: var(--space-sm); }.secret-input input { flex: 1; }
.modal-card { width: min(800px,100%); max-height: calc(100vh - 48px); overflow: auto; background: var(--color-background-primary); border: 1px solid var(--color-border-default); border-radius: var(--radius-xl); box-shadow: var(--shadow-xl); padding: var(--space-xl); display: grid; gap: var(--space-lg); animation: modal-in var(--motion-normal) ease-out; }.modal-card header,.modal-card footer { justify-content: space-between; }.modal-card footer { justify-content: flex-end; }.modal-card label { display: grid; gap: var(--space-xs); font-weight: 600; }.modal-card input,.modal-card textarea { width: 100%; border: 1px solid var(--color-border-default); border-radius: var(--radius-md); padding: 10px 12px; color: var(--color-text-primary); background: var(--color-background-secondary); font: inherit; }.modal-card textarea { resize: vertical; font-family: var(--font-ui-mono); font-size: var(--font-size-sm); }.json-editor { line-height: 1.55; }.close { border: 0; background: transparent; color: var(--color-text-secondary); font-size: 28px; cursor: pointer; }
.template-row { flex-wrap: wrap; }.template-row > span { margin-right: auto; font-weight: 600; }.template,.mode-tabs button { border: 1px solid var(--color-border-default); background: var(--color-background-secondary); color: var(--color-text-secondary); padding: 7px 10px; border-radius: var(--radius-md); cursor: pointer; }.template.active,.mode-tabs button.active { color: var(--color-accent-primary); border-color: var(--color-accent-primary); background: var(--color-accent-soft); }.mode-tabs { display: inline-flex; justify-self: start; gap: 2px; padding: 3px; border-radius: var(--radius-md); background: var(--color-background-secondary); }.two-columns { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-md); }
@keyframes modal-in { from { opacity: 0; transform: translateY(8px) scale(.99); } } @media (max-width:720px) { .two-columns,.secrets label { grid-template-columns:1fr; }.card-actions { flex-wrap:wrap; } }
</style>
