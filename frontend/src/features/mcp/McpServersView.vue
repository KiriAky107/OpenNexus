<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Connection, Delete, EditPen, Plus, Refresh, VideoPlay } from '@element-plus/icons-vue'
import AppIcon from '@/components/common/AppIcon.vue'
import type { McpServer, McpServerInput, McpServerTransport } from '@/contracts'
import * as service from '@/services/mcpServerService'

const servers = ref<McpServer[]>([])
const busy = ref('')
const error = ref('')
const dialogOpen = ref(false)
const editingId = ref<string | null>(null)
const argsText = ref('')
const environmentText = ref('{}')
const secretKeysText = ref('')
const permissionsText = ref('')
const secretDrafts = reactive<Record<string, string>>({})
const form = reactive<McpServerInput>({
  name: '', transport: 'stdio', command: '', args: [], environment: {},
  secret_environment_keys: [], permissions: [], startup_timeout_seconds: 15, tool_timeout_seconds: 30,
})

const dialogTitle = computed(() => editingId.value ? '编辑 MCP 服务器' : '新增 MCP 服务器')

async function load() {
  error.value = ''
  try { servers.value = await service.listMcpServers() }
  catch (cause) { error.value = message(cause, '读取 MCP 服务器失败') }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, { name: '', transport: 'stdio', command: '', args: [], environment: {}, secret_environment_keys: [], permissions: [], startup_timeout_seconds: 15, tool_timeout_seconds: 30 })
  argsText.value = ''; environmentText.value = '{}'; secretKeysText.value = ''; permissionsText.value = ''
  dialogOpen.value = true
}

function openEdit(server: McpServer) {
  editingId.value = server.server_id
  Object.assign(form, {
    name: server.name, transport: server.transport, command: server.command,
    args: [...server.args], environment: { ...server.environment },
    secret_environment_keys: Object.keys(server.secret_environment), permissions: [...server.permissions],
    startup_timeout_seconds: server.startup_timeout_seconds,
    tool_timeout_seconds: server.tool_timeout_seconds,
  })
  argsText.value = server.args.join('\n')
  environmentText.value = JSON.stringify(server.environment, null, 2)
  secretKeysText.value = Object.keys(server.secret_environment).join('\n')
  permissionsText.value = server.permissions.join(', ')
  dialogOpen.value = true
}

function applyTemplate(transport: McpServerTransport) {
  if (transport !== 'stdio') return
  form.transport = 'stdio'; form.command = 'uvx'; argsText.value = 'mcp-server-fetch'
}

function payload(): McpServerInput {
  let environment: Record<string, string>
  try { environment = JSON.parse(environmentText.value || '{}') }
  catch { throw new Error('普通环境变量必须是 JSON 对象') }
  if (!environment || Array.isArray(environment) || typeof environment !== 'object') throw new Error('普通环境变量必须是 JSON 对象')
  return {
    ...form,
    name: form.name.trim(), command: form.command.trim(),
    args: argsText.value.split('\n').map(value => value.trim()).filter(Boolean),
    environment,
    secret_environment_keys: secretKeysText.value.split(/[\n,]/).map(value => value.trim()).filter(Boolean),
    permissions: permissionsText.value.split(',').map(value => value.trim()).filter(Boolean),
  }
}

async function save() {
  try {
    const input = payload()
    if (!input.name || !input.command) throw new Error('请填写服务器名称和可执行命令')
    busy.value = 'save'
    editingId.value ? await service.updateMcpServer(editingId.value, input) : await service.createMcpServer(input)
    dialogOpen.value = false
    await load()
  } catch (cause) { error.value = message(cause, '保存失败') }
  finally { busy.value = '' }
}

async function approve(server: McpServer): Promise<McpServer | null> {
  if (server.trusted) return server
  const accepted = confirm(`即将允许本机启动以下命令：\n\n${server.command_summary}\n\n当前 Python Host 没有系统级沙箱，仅应运行可信服务器。是否继续？`)
  if (!accepted) return null
  return service.trustMcpServer(server)
}

async function test(server: McpServer) { await act(server, 'test', async current => service.testMcpServer(current.server_id)) }
async function toggle(server: McpServer) { await act(server, 'toggle', async current => current.enabled ? service.disableMcpServer(current.server_id) : service.enableMcpServer(current.server_id)) }
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

async function saveSecret(server: McpServer, key: string) {
  const value = secretDrafts[`${server.server_id}:${key}`]?.trim()
  if (!value) return
  try { busy.value = `secret:${server.server_id}:${key}`; await service.putMcpServerSecret(server.server_id, key, value); secretDrafts[`${server.server_id}:${key}`] = ''; await load() }
  catch (cause) { error.value = message(cause, '保存密钥失败') } finally { busy.value = '' }
}

function message(cause: unknown, fallback: string) { return cause instanceof Error ? cause.message : fallback }
onMounted(load)
</script>

<template>
  <section class="feature-page mcp-page">
    <header class="feature-header">
      <div><h1>MCP 服务器</h1><p>管理独立 MCP Server 的连接、凭据与工具生命周期。</p></div>
      <div class="inline-actions"><button class="button-secondary" :disabled="!!busy" @click="load"><AppIcon :icon="Refresh" /> 刷新</button><button class="button-primary" @click="openCreate"><AppIcon :icon="Plus" /> 新增服务器</button></div>
    </header>
    <div class="notice-banner">开发阶段仅开放 stdio。uvx 负责依赖隔离，但不是安全沙箱；生产环境将在桌面端沙箱接入前禁止启动本机进程。</div>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="!servers.length" class="panel empty"><AppIcon :icon="Connection" :size="34" /><h2>尚未配置 MCP 服务器</h2><p>添加一个 stdio Server，保存后可测试连接并启用工具。</p><button class="button-primary" @click="openCreate">新增服务器</button></div>
    <div v-else class="server-list">
      <article v-for="server in servers" :key="server.server_id" class="panel server-card">
        <div class="server-main"><div class="server-title"><AppIcon :icon="Connection" :size="24" /><div><h2>{{ server.name }}</h2><code>{{ server.command_summary }}</code></div></div><span class="badge" :class="{ success: server.status === 'ready', error: ['error','unhealthy'].includes(server.status) }">{{ server.status }}</span></div>
        <div class="metadata"><span>{{ server.transport }}</span><span>{{ server.tools_count }} 个工具</span><span>{{ server.trusted ? '命令已确认' : '等待确认命令' }}</span><span v-if="server.last_test_succeeded">最近测试成功</span><span v-if="server.remote_server_name">{{ server.remote_server_name }} {{ server.remote_server_version }}</span></div>
        <div v-if="server.error" class="error-banner compact">{{ server.error }}</div>
        <div v-if="Object.keys(server.secret_environment).length" class="secrets"><label v-for="(configured, key) in server.secret_environment" :key="key"><span>{{ key }} <small>{{ configured ? '已加密保存' : '未配置' }}</small></span><span class="secret-input"><input v-model="secretDrafts[`${server.server_id}:${key}`]" type="password" autocomplete="new-password" placeholder="输入后保存（不会回显）"><button class="button-secondary" @click="saveSecret(server, key)">保存</button></span></label></div>
        <footer class="card-actions"><button class="button-secondary" :disabled="!!busy || server.enabled" @click="test(server)"><AppIcon :icon="VideoPlay" /> 测试连接</button><button class="button-secondary" :disabled="!!busy" @click="openEdit(server)"><AppIcon :icon="EditPen" /> 编辑</button><button class="button-danger" :disabled="!!busy" @click="remove(server)"><AppIcon :icon="Delete" /> 删除</button><button class="button-primary" :disabled="!!busy" @click="toggle(server)">{{ server.enabled ? '停用' : '启用' }}</button></footer>
      </article>
    </div>

    <div v-if="dialogOpen" class="modal-backdrop" @click.self="dialogOpen = false">
      <form class="modal-card" @submit.prevent="save">
        <header><h2><AppIcon :icon="Plus" /> {{ dialogTitle }}</h2><button type="button" class="close" @click="dialogOpen = false">×</button></header>
        <label>服务器名称<input v-model="form.name" maxlength="80" placeholder="例如：文件系统工具"></label>
        <div class="template-row"><span>服务器配置</span><button type="button" class="template active" @click="applyTemplate('stdio')">stdio 模板</button><button type="button" class="template" disabled>Streamable HTTP（后续）</button><button type="button" class="template" disabled>SSE（兼容项）</button></div>
        <label>可执行命令<input v-model="form.command" placeholder="uvx、npx 或可信可执行文件路径"></label>
        <label>参数（每行一项）<textarea v-model="argsText" rows="4" placeholder="mcp-server-fetch"></textarea></label>
        <div class="two-columns"><label>普通环境变量（JSON）<textarea v-model="environmentText" rows="5"></textarea></label><label>敏感环境变量名（每行一项）<textarea v-model="secretKeysText" rows="5" placeholder="API_KEY"></textarea></label></div>
        <label>声明权限（逗号分隔，可选）<input v-model="permissionsText" placeholder="network.request, notes.read"></label>
        <div class="two-columns"><label>启动超时（秒）<input v-model.number="form.startup_timeout_seconds" type="number" min="1" max="120"></label><label>工具超时（秒）<input v-model.number="form.tool_timeout_seconds" type="number" min="1" max="300"></label></div>
        <footer><button type="button" class="button-secondary" @click="dialogOpen = false">取消</button><button class="button-primary" :disabled="busy === 'save'">保存</button></footer>
      </form>
    </div>
  </section>
</template>

<style scoped>
.mcp-page { overflow: auto; }
.notice-banner, .error-banner { margin-bottom: var(--space-lg); }
.server-list { display: grid; gap: var(--space-lg); }
.server-card { display: grid; gap: var(--space-md); }
.server-main, .server-title, .metadata, .card-actions, .inline-actions, .template-row, .modal-card header, .modal-card footer { display: flex; align-items: center; gap: var(--space-sm); }
.server-main { justify-content: space-between; }.server-title { align-items: flex-start; }.server-title h2 { margin-bottom: 4px; }.server-title code { color: var(--color-text-secondary); overflow-wrap: anywhere; }
.metadata { flex-wrap: wrap; color: var(--color-text-tertiary); font-size: var(--font-size-sm); }.metadata span + span::before { content: '·'; margin-right: var(--space-sm); }.compact { margin: 0; }
.card-actions { justify-content: flex-end; border-top: 1px solid var(--color-border-subtle); padding-top: var(--space-md); }.empty { text-align: center; place-items: center; display: grid; gap: var(--space-md); padding: 64px; }
.secrets { border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); padding: var(--space-md); display: grid; gap: var(--space-sm); }.secrets label { display: grid; grid-template-columns: minmax(160px,.6fr) 1fr; align-items: center; gap: var(--space-md); }.secrets small { color: var(--color-text-tertiary); }.secret-input { display: flex; gap: var(--space-sm); }.secret-input input { flex: 1; }
.modal-backdrop { position: fixed; inset: 0; z-index: 1000; background: rgb(0 0 0 / .48); display: grid; place-items: center; padding: var(--space-xl); }
.modal-card { width: min(760px, 100%); max-height: calc(100vh - 48px); overflow: auto; background: var(--color-background-primary); border: 1px solid var(--color-border-default); border-radius: var(--radius-xl); box-shadow: var(--shadow-xl); padding: var(--space-xl); display: grid; gap: var(--space-lg); animation: modal-in var(--motion-normal) ease-out; }
.modal-card header, .modal-card footer { justify-content: space-between; }.modal-card footer { justify-content: flex-end; }.modal-card label { display: grid; gap: var(--space-xs); font-weight: 600; }.modal-card input, .modal-card textarea { width: 100%; border: 1px solid var(--color-border-default); border-radius: var(--radius-md); padding: 10px 12px; color: var(--color-text-primary); background: var(--color-background-secondary); font: inherit; }.modal-card textarea { resize: vertical; font-family: var(--font-family-mono); font-size: var(--font-size-sm); }.close { border: 0; background: transparent; color: var(--color-text-secondary); font-size: 28px; cursor: pointer; }.template-row { flex-wrap: wrap; }.template-row > span { margin-right: auto; font-weight: 600; }.template { border: 1px solid var(--color-border-default); background: var(--color-background-secondary); color: var(--color-text-secondary); padding: 7px 10px; border-radius: var(--radius-md); }.template.active { color: var(--color-accent-primary); border-color: var(--color-accent-primary); }.two-columns { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-md); }
@keyframes modal-in { from { opacity: 0; transform: translateY(8px) scale(.99); } }
@media (max-width: 720px) { .two-columns, .secrets label { grid-template-columns: 1fr; }.card-actions { flex-wrap: wrap; } }
</style>
