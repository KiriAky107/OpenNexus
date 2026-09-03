<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Connection, Delete, EditPen, Plus, Refresh, VideoPlay } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import type { McpServer, McpServerInput, McpServerTransport } from '@/contracts'
import * as service from '@/services/mcpServerService'

type SecretKind = 'environment' | 'header'

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
const form = reactive<McpServerInput>(emptyForm())

const dialogTitle = computed(() => editingId.value ? '编辑 MCP 服务器' : '新增 MCP 服务器')

function emptyForm(): McpServerInput {
  return {
    name: '', transport: 'stdio', command: '', args: [], url: null, headers: {},
    environment: {}, secret_environment_keys: [], secret_header_keys: [], permissions: [],
    startup_timeout_seconds: 15, tool_timeout_seconds: 30,
  }
}

async function load() {
  error.value = ''
  try { servers.value = await service.listMcpServers() }
  catch (cause) { error.value = message(cause, '读取 MCP 服务器失败') }
}

function resetEditor(input: McpServerInput) {
  Object.assign(form, input)
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
  editingId.value = null
  editingOriginal.value = null
  resetEditor(emptyForm())
  dialogOpen.value = true
}

function openEdit(server: McpServer) {
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
  try { parsed = JSON.parse(value || '{}') } catch { throw new Error(`${label}必须是 JSON 对象`) }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.values(parsed).some(item => typeof item !== 'string')) throw new Error(`${label}必须是字符串键值 JSON 对象`)
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
    headers: stdio ? {} : parseObject(headersText.value, '普通 Header'),
    environment: stdio ? parseObject(environmentText.value, '普通环境变量') : {},
    secret_environment_keys: stdio ? splitKeys(secretKeysText.value) : [],
    secret_header_keys: stdio ? [] : splitKeys(secretHeaderKeysText.value),
    permissions: permissionsText.value.split(',').map(value => value.trim()).filter(Boolean),
    startup_timeout_seconds: form.startup_timeout_seconds,
    tool_timeout_seconds: form.tool_timeout_seconds,
  }
}

function payload(): McpServerInput {
  if (editorMode.value === 'form') return formPayload()
  let parsed: unknown
  try { parsed = JSON.parse(rawConfig.value) } catch { throw new Error('服务器配置不是有效 JSON') }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('服务器配置必须是 JSON 对象')
  const value = parsed as McpServerInput
  if (editingId.value) value.version = form.version
  return value
}

function switchMode(mode: 'form' | 'json') {
  try {
    if (mode === editorMode.value) return
    if (mode === 'json') rawConfig.value = JSON.stringify(formPayload(), null, 2)
    else resetEditor(payload())
    editorMode.value = mode
  } catch (cause) { error.value = message(cause, '配置转换失败') }
}

async function save() {
  try {
    const input = payload()
    if (!input.name || (input.transport === 'stdio' ? !input.command : !input.url)) throw new Error('请填写服务器名称和连接地址')
    if (editingOriginal.value && executionChanged(editingOriginal.value, input) && !confirm('连接命令、地址或认证配置已变化，保存后旧测试与授权会失效。是否保存？')) return
    busy.value = 'save'
    editingId.value ? await service.updateMcpServer(editingId.value, input) : await service.createMcpServer(input)
    dialogOpen.value = false
    await load()
  } catch (cause) { error.value = message(cause, '保存失败') }
  finally { busy.value = '' }
}

function executionChanged(server: McpServer, input: McpServerInput) {
  return JSON.stringify([server.transport, server.command, server.args, server.url, server.headers, Object.keys(server.secret_headers)]) !== JSON.stringify([input.transport, input.command, input.args, input.url, input.headers, input.secret_header_keys])
}

async function approve(server: McpServer): Promise<McpServer | null> {
  if (server.trusted) return server
  const localWarning = server.transport === 'stdio' ? '\n\n本机进程尚无系统级沙箱，仅应运行可信服务器。' : '\n\n连接可能向该地址发送配置的 Header。'
  if (!confirm(`请确认 MCP 连接：\n\n${server.command_summary}${localWarning}\n\n是否继续？`)) return null
  return service.trustMcpServer(server)
}

async function test(server: McpServer) { await act(server, 'test', current => service.testMcpServer(current.server_id)) }
async function toggle(server: McpServer) { await act(server, 'toggle', current => current.enabled ? service.disableMcpServer(current.server_id) : service.enableMcpServer(current.server_id)) }
async function act(server: McpServer, action: string, operation: (server: McpServer) => Promise<McpServer>) {
  busy.value = `${action}:${server.server_id}`; error.value = ''
  try { const current = action === 'toggle' && server.enabled ? server : await approve(server); if (!current) return; await operation(current); await load() }
  catch (cause) { error.value = message(cause, '操作失败') }
  finally { busy.value = '' }
}

async function remove(server: McpServer) {
  if (!confirm(`删除“${server.name}”及其加密凭据？`)) return
  try { busy.value = `delete:${server.server_id}`; await service.deleteMcpServer(server.server_id); await load() }
  catch (cause) { error.value = message(cause, '删除失败') } finally { busy.value = '' }
}

async function saveSecret(server: McpServer, key: string, kind: SecretKind) {
  const draftKey = `${server.server_id}:${kind}:${key}`
  const value = secretDrafts[draftKey]?.trim()
  if (!value) return
  try { busy.value = `secret:${draftKey}`; await service.putMcpServerSecret(server.server_id, key, value, kind); secretDrafts[draftKey] = ''; await load() }
  catch (cause) { error.value = message(cause, '保存密钥失败') } finally { busy.value = '' }
}

function splitKeys(value: string) { return value.split(/[\n,]/).map(item => item.trim()).filter(Boolean) }
function message(cause: unknown, fallback: string) { return cause instanceof Error ? cause.message : fallback }
onMounted(load)
</script>

<template>
  <section class="feature-page mcp-page">
    <header class="feature-header"><div><h1>MCP 服务器</h1><p>管理独立 MCP Server 的连接、凭据与工具生命周期。</p></div><div class="inline-actions"><button class="button-secondary" :disabled="!!busy" @click="load"><AppIcon :icon="Refresh" /> 刷新</button><button class="button-primary" @click="openCreate"><AppIcon :icon="Plus" /> 新增服务器</button></div></header>
    <div class="notice-banner">stdio 本机进程仅在开发环境开放；Streamable HTTP 为首选远程传输，SSE 仅用于兼容旧服务器。uvx 隔离依赖但不是安全沙箱。</div>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="!servers.length" class="panel empty"><AppIcon :icon="Connection" :size="34" /><h2>尚未配置 MCP 服务器</h2><p>添加 Server，测试连接成功后才能启用工具。</p><button class="button-primary" @click="openCreate">新增服务器</button></div>
    <div v-else class="server-list">
      <article v-for="server in servers" :key="server.server_id" class="panel server-card">
        <div class="server-main"><div class="server-title"><AppIcon :icon="Connection" :size="24" /><div><h2>{{ server.name }}</h2><code>{{ server.command_summary }}</code></div></div><span class="badge" :class="{ success: server.status === 'ready', error: ['error','unhealthy'].includes(server.status) }">{{ server.status }}</span></div>
        <div class="metadata"><span>{{ server.transport }}</span><span>v{{ server.version }}</span><span>{{ server.tools_count }} 个工具</span><span>{{ server.trusted ? '连接已确认' : '等待确认连接' }}</span><span v-if="server.last_test_succeeded">当前配置测试成功</span><span v-if="server.remote_server_name">{{ server.remote_server_name }} {{ server.remote_server_version }}</span></div>
        <div v-if="server.error" class="error-banner compact">{{ server.error }}</div>
        <div v-if="Object.keys(server.secret_environment).length || Object.keys(server.secret_headers).length" class="secrets">
          <label v-for="(configured, key) in server.secret_environment" :key="`env:${key}`"><span>环境变量 · {{ key }} <small>{{ configured ? '已加密保存' : '未配置' }}</small></span><span class="secret-input"><input v-model="secretDrafts[`${server.server_id}:environment:${key}`]" type="password" autocomplete="new-password" placeholder="输入后保存（不会回显）"><button class="button-secondary" @click="saveSecret(server, key, 'environment')">保存</button></span></label>
          <label v-for="(configured, key) in server.secret_headers" :key="`header:${key}`"><span>HTTP Header · {{ key }} <small>{{ configured ? '已加密保存' : '未配置' }}</small></span><span class="secret-input"><input v-model="secretDrafts[`${server.server_id}:header:${key}`]" type="password" autocomplete="new-password" placeholder="输入后保存（不会回显）"><button class="button-secondary" @click="saveSecret(server, key, 'header')">保存</button></span></label>
        </div>
        <footer class="card-actions"><button class="button-secondary" :disabled="!!busy || server.enabled" @click="test(server)"><AppIcon :icon="VideoPlay" /> 测试连接</button><button class="button-secondary" :disabled="!!busy" @click="openEdit(server)"><AppIcon :icon="EditPen" /> 编辑</button><button class="button-danger" :disabled="!!busy" @click="remove(server)"><AppIcon :icon="Delete" /> 删除</button><button class="button-primary" :disabled="!!busy || (!server.enabled && !server.last_test_succeeded)" :title="!server.enabled && !server.last_test_succeeded ? '请先测试当前配置' : ''" @click="toggle(server)">{{ server.enabled ? '停用' : '启用' }}</button></footer>
      </article>
    </div>

    <div v-if="dialogOpen" class="modal-backdrop" @click.self="dialogOpen = false">
      <form class="modal-card" @submit.prevent="save">
        <header><h2><AppIcon :icon="Plus" /> {{ dialogTitle }}</h2><button type="button" class="close" @click="dialogOpen = false">×</button></header>
        <div class="mode-tabs"><button type="button" :class="{ active: editorMode === 'form' }" @click="switchMode('form')">表单配置</button><button type="button" :class="{ active: editorMode === 'json' }" @click="switchMode('json')">JSON 配置</button></div>
        <template v-if="editorMode === 'form'">
          <label>服务器名称<input v-model="form.name" maxlength="80" placeholder="例如：文件系统工具"></label>
          <div class="template-row"><span>服务器配置</span><button type="button" class="template" :class="{ active: form.transport === 'stdio' }" @click="applyTemplate('stdio')">stdio 模板</button><button type="button" class="template" :class="{ active: form.transport === 'streamable_http' }" @click="applyTemplate('streamable_http')">Streamable HTTP</button><button type="button" class="template" :class="{ active: form.transport === 'sse' }" @click="applyTemplate('sse')">SSE（兼容）</button></div>
          <template v-if="form.transport === 'stdio'"><label>可执行命令<input v-model="form.command" placeholder="uvx、npx 或可信可执行文件路径"></label><label>参数（每行一项）<textarea v-model="argsText" rows="5"></textarea></label><div class="two-columns"><label>普通环境变量（JSON）<textarea v-model="environmentText" rows="5"></textarea></label><label>敏感环境变量名（每行一项）<textarea v-model="secretKeysText" rows="5" placeholder="API_KEY"></textarea></label></div></template>
          <template v-else><label>MCP URL<input v-model="form.url" placeholder="https://example.com/mcp"></label><div class="two-columns"><label>普通 Header（JSON）<textarea v-model="headersText" rows="5" placeholder='{"X-Client":"NotesAgent"}'></textarea></label><label>敏感 Header 名（每行一项）<textarea v-model="secretHeaderKeysText" rows="5" placeholder="Authorization"></textarea></label></div></template>
          <label>声明权限（逗号分隔，可选）<input v-model="permissionsText" placeholder="network.request, notes.read"></label>
          <div class="two-columns"><label>启动超时（秒）<input v-model.number="form.startup_timeout_seconds" type="number" min="1" max="120"></label><label>工具超时（秒）<input v-model.number="form.tool_timeout_seconds" type="number" min="1" max="300"></label></div>
        </template>
        <label v-else>服务器 JSON 配置<textarea v-model="rawConfig" class="json-editor" rows="22" spellcheck="false"></textarea><small>Secret 只填写键名，明文请在保存后的服务器卡片中单独录入。</small></label>
        <footer><button type="button" class="button-secondary" @click="dialogOpen = false">取消</button><button class="button-primary" :disabled="busy === 'save'">保存</button></footer>
      </form>
    </div>
  </section>
</template>

<style scoped>
.mcp-page { overflow: auto; }.notice-banner,.error-banner { margin-bottom: var(--space-lg); }.server-list { display: grid; gap: var(--space-lg); }.server-card { display: grid; gap: var(--space-md); }
.server-main,.server-title,.metadata,.card-actions,.inline-actions,.template-row,.modal-card header,.modal-card footer { display: flex; align-items: center; gap: var(--space-sm); }.server-main { justify-content: space-between; }.server-title { align-items: flex-start; }.server-title h2 { margin-bottom: 4px; }.server-title code { color: var(--color-text-secondary); overflow-wrap: anywhere; }.metadata { flex-wrap: wrap; color: var(--color-text-tertiary); font-size: var(--font-size-sm); }.metadata span + span::before { content: '·'; margin-right: var(--space-sm); }.compact { margin: 0; }
.card-actions { justify-content: flex-end; border-top: 1px solid var(--color-border-subtle); padding-top: var(--space-md); }.empty { text-align: center; place-items: center; display: grid; gap: var(--space-md); padding: 64px; }.secrets { border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); padding: var(--space-md); display: grid; gap: var(--space-sm); }.secrets label { display: grid; grid-template-columns: minmax(220px,.7fr) 1fr; align-items: center; gap: var(--space-md); }.secrets small,.modal-card small { color: var(--color-text-tertiary); }.secret-input { display: flex; gap: var(--space-sm); }.secret-input input { flex: 1; }
.modal-backdrop { position: fixed; inset: 0; z-index: 1000; background: rgb(0 0 0 / .48); display: grid; place-items: center; padding: var(--space-xl); }.modal-card { width: min(800px,100%); max-height: calc(100vh - 48px); overflow: auto; background: var(--color-background-primary); border: 1px solid var(--color-border-default); border-radius: var(--radius-xl); box-shadow: var(--shadow-xl); padding: var(--space-xl); display: grid; gap: var(--space-lg); animation: modal-in var(--motion-normal) ease-out; }.modal-card header,.modal-card footer { justify-content: space-between; }.modal-card footer { justify-content: flex-end; }.modal-card label { display: grid; gap: var(--space-xs); font-weight: 600; }.modal-card input,.modal-card textarea { width: 100%; border: 1px solid var(--color-border-default); border-radius: var(--radius-md); padding: 10px 12px; color: var(--color-text-primary); background: var(--color-background-secondary); font: inherit; }.modal-card textarea { resize: vertical; font-family: var(--font-family-mono); font-size: var(--font-size-sm); }.json-editor { line-height: 1.55; }.close { border: 0; background: transparent; color: var(--color-text-secondary); font-size: 28px; cursor: pointer; }
.template-row { flex-wrap: wrap; }.template-row > span { margin-right: auto; font-weight: 600; }.template,.mode-tabs button { border: 1px solid var(--color-border-default); background: var(--color-background-secondary); color: var(--color-text-secondary); padding: 7px 10px; border-radius: var(--radius-md); cursor: pointer; }.template.active,.mode-tabs button.active { color: var(--color-accent-primary); border-color: var(--color-accent-primary); background: var(--color-accent-soft); }.mode-tabs { display: inline-flex; justify-self: start; gap: 2px; padding: 3px; border-radius: var(--radius-md); background: var(--color-background-secondary); }.two-columns { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-md); }
@keyframes modal-in { from { opacity: 0; transform: translateY(8px) scale(.99); } } @media (max-width:720px) { .two-columns,.secrets label { grid-template-columns:1fr; }.card-actions { flex-wrap:wrap; } }
</style>
