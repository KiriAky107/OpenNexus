<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch, computed } from 'vue'
import { preferenceSyncIssues, resolvePreferenceDraft, seedCurrentPreferences } from '@/services/platform/preferenceSync'
import { hostInvoke } from '@/services/platform/desktop'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
import { t } from '@/i18n'
const { actionDialog, resolveAction, askConfirm } = useActionDialog()
interface Binding { id: string; endpoint: string; account: string; remote_vault: string; cursor: number }
interface Conflict { sequence: number; local_path: string; local_hash: string; current_hash?: string; current_path?: string; remote: { path: string; operation: string } }
type OptionalScope = { persona: boolean; layout: boolean; conversations: boolean; agent_history: boolean; provider_settings: boolean; extension_installations: boolean }
interface Status { optional_scope?: OptionalScope; vault_id: string; binding: Binding | null; paused: boolean; pending: number; conflicts: Conflict[]; credential_state: string; running: boolean; error: string | null; retry_in: number | null; failures: number; halted: boolean; attempts?: Array<{ operation_id: string; path: string; attempts: number; outcome: string; error: string | null }> }
interface RemoteVault { id: string; name: string; sequence: number; used: number; quota: number }
const status = ref<Status | null>(null)
const endpoint = ref('https://'), account = ref(''), password = ref(''), device = ref('OpenNexus Desktop'), testHttp = ref(false)
const connected = ref(false), busy = ref(false), message = ref(''), remoteVaults = ref<RemoteVault[]>([]), selected = ref(''), newName = ref('')
const copies = ref<Record<number, string>>({})
interface Preview { fingerprint: string; boundary: number; items: Array<{ path: string; action: string }> }
const preview = ref<Preview | null>(null), previewPage = ref(0)
const previewItems = computed(() => preview.value?.items.slice(previewPage.value * 100, (previewPage.value + 1) * 100) ?? [])
watch([endpoint, account, selected, () => status.value?.vault_id], () => { preview.value = null; previewPage.value = 0 })
function previewMerge() { return act(async () => {
  await seedCurrentPreferences(status.value!.vault_id)
  preview.value = await hostInvoke<Preview>('sync_preview', { request: { vault_id: status.value!.vault_id, endpoint: endpoint.value, account: account.value, remote_vault: selected.value, mode: 'merge' } })
}) }
async function merge() {
  const plan = preview.value, vaultId = status.value?.vault_id, remote = selected.value
  if (!plan || !vaultId || !(await askConfirm(t('按预览合并并绑定？相同路径将使用远端文件身份，内容不同的文件保留为待解决冲突。', 'Merge and bind this preview? Matching paths adopt remote identities; differing contents are preserved as conflicts.')))) return
  await act(async () => { await hostInvoke('sync_bind', { request: { vault_id: vaultId, endpoint: endpoint.value, account: account.value, remote_vault: remote, mode: 'merge', fingerprint: plan.fingerprint } }); preview.value = null })
}
let timer: ReturnType<typeof setInterval> | undefined
let mounted = true
async function refresh() {
  const next = await hostInvoke<Status>('sync_status')
  if (!mounted) return
  status.value = next
  if (next.binding) { endpoint.value = next.binding.endpoint; account.value = next.binding.account; connected.value = next.credential_state === 'ready' }
}
async function act(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; message.value = ''
  try { await action(); await refresh() }
  catch (error) { message.value = error instanceof Error ? error.message : 'SYNC_FAILED' }
  finally { busy.value = false }
}
function setScope(kind: keyof OptionalScope, event: Event) {
  const current = status.value
  if (!current || current.binding) return
  const input = event.target as HTMLInputElement
  const scope: OptionalScope = { persona: false, layout: false, conversations: false, agent_history: false, provider_settings: false, extension_installations: false, ...current.optional_scope, [kind]: input.checked }
  input.checked = current.optional_scope?.[kind] ?? false
  preview.value = null
  return act(async () => { await hostInvoke('sync_set_scope', { vaultId: current.vault_id, scope }) })
}
async function listVaults() {
  const result = await hostInvoke<{ items: RemoteVault[] }>('sync_vaults', { endpoint: endpoint.value, account: account.value })
  remoteVaults.value = result.items
}
function login() {
  const secret = password.value; password.value = ''
  return act(async () => {
    const result = await hostInvoke<{ endpoint: string; account: string }>('sync_login', { request: { endpoint: endpoint.value, account: account.value, password: secret, device_name: device.value, allow_test_http: testHttp.value } })
    endpoint.value = result.endpoint; account.value = result.account; connected.value = true
    await listVaults()
  })
}
function createVault() { return act(async () => {
  const result = await hostInvoke<{ vault_id: string }>('sync_create_vault', { endpoint: endpoint.value, account: account.value, name: newName.value })
  newName.value = ''; await listVaults(); selected.value = result.vault_id
}) }
async function bind(mode: 'upload' | 'download') {
  const vaultId = status.value?.vault_id
  const remote = selected.value
  if (!vaultId || !remote) return
  if (!(await askConfirm(mode === 'upload' ? t('将当前本地笔记上传到所选空远端库？', 'Upload current notes to the selected empty remote vault?') : t('将所选远端库下载到当前空本地库？', 'Download the selected remote vault into this empty local vault?')))) return
  await act(async () => {
    if (mode === 'upload') await seedCurrentPreferences(vaultId)
    await hostInvoke('sync_bind', { request: { vault_id: vaultId, endpoint: endpoint.value, account: account.value, remote_vault: remote, mode } })
  })
}
async function unbind() {
  const binding = status.value?.binding
  if (!binding || !(await askConfirm(t('解除当前绑定并封存待上传任务？本地文件仍保留。', 'Unbind and archive pending uploads? Local files are retained.')))) return
  await act(async () => { await hostInvoke('sync_unbind', { bindingId: binding.id }); remoteVaults.value = []; selected.value = '' })
}
async function resolve(conflict: Conflict, choice: 'local' | 'remote' | 'copy') {
  const binding = status.value?.binding
  const destination = choice === 'copy' ? copies.value[conflict.sequence] ?? '' : ''
  if (!binding || (choice === 'copy' && !destination)) return
  if (!(await askConfirm(t(`确认解决 ${conflict.local_path} 的冲突？`, `Resolve the conflict for ${conflict.local_path}?`)))) return
  await act(async () => { await hostInvoke('sync_resolve', { bindingId: binding.id, sequence: conflict.sequence, choice, destination, expected: conflict.current_hash ?? conflict.local_hash }) })
}
onMounted(() => {
  void refresh().catch(error => { message.value = error instanceof Error ? error.message : 'SYNC_FAILED' })
  timer = setInterval(() => { if (!busy.value) void refresh().catch(() => {}) }, 1500)
})
onUnmounted(() => { mounted = false; clearInterval(timer); password.value = '' })
</script>
<template>
  <section class="panel settings-section sync-settings" aria-labelledby="sync-title">
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
    <h2 id="sync-title">OpenNexus Sync</h2>
    <p>{{ t('同步当前 Vault 的 Markdown 与常用附件。登录前请先解锁设备凭据保险库。', 'Sync Markdown and supported attachments in the current vault. Unlock the device credential vault before signing in.') }}</p>
    <p class="subtle">{{ t('默认同步笔记、附件、任务、主题设置和编辑器偏好。两边都有数据时先预览合并；密钥、权限和本机路径不随设置同步。', 'Notes, attachments, tasks, theme settings and editor preferences sync by default. Preview a merge when both vaults contain data. Secrets, permissions and device paths stay local.') }}</p>
    <p v-if="message" class="error-banner" role="alert">{{ message }}</p>
    <article v-for="issue in preferenceSyncIssues" :key="issue.kind" class="sync-conflict" role="status">
      <h3>{{ issue.label }}</h3><p>{{ issue.error }}</p>
      <p>{{ t('本机待提交设置已保留，请选择使用哪一份。', 'The local preference draft is retained. Choose which version to use.') }}</p>
      <div v-if="issue.hasDraft" class="inline-actions"><button :disabled="busy" @click="act(() => resolvePreferenceDraft(issue.kind, 'local'))">{{ t('保留本机设置', 'Keep local settings') }}</button><button :disabled="busy" @click="act(() => resolvePreferenceDraft(issue.kind, 'remote'))">{{ t('采用工作区设置', 'Use workspace settings') }}</button></div>
    </article>
    <form class="sync-form" @submit.prevent="login">
      <label>{{ t('服务器地址', 'Server URL') }}<input v-model="endpoint" required :disabled="!!status?.binding || busy" type="url" autocomplete="url" /></label>
      <label>{{ t('账户', 'Account') }}<input v-model="account" required :disabled="!!status?.binding || busy" autocomplete="username" /></label>
      <label>{{ t('密码', 'Password') }}<input v-model="password" required type="password" autocomplete="current-password" :disabled="busy" /></label>
      <label>{{ t('设备名称', 'Device name') }}<input v-model="device" required maxlength="100" :disabled="busy" /></label>
      <label class="test-http"><input v-model="testHttp" type="checkbox" :disabled="busy" />{{ t('仅测试：允许 HTTP 明文连接', 'Testing only: allow unencrypted HTTP') }}</label>
      <button class="button-primary" :disabled="busy || !password">{{ t('登录', 'Sign in') }}</button>
    </form>
    <div v-if="connected && !status?.binding" class="sync-connect">
      <button :disabled="busy" @click="act(listVaults)">{{ t('刷新远端库', 'Refresh vaults') }}</button>
      <label>{{ t('远端库', 'Remote vault') }}<select v-model="selected"><option value="">{{ t('请选择', 'Choose a vault') }}</option><option v-for="vault in remoteVaults" :key="vault.id" :value="vault.id">{{ vault.name }} · {{ vault.sequence }}</option></select></label>
      <div class="inline-actions"><input v-model="newName" :placeholder="t('新远端库名称', 'New vault name')" /><button :disabled="busy || !newName.trim()" @click="createVault">{{ t('创建远端库', 'Create vault') }}</button></div>
      <div class="inline-actions"><button :disabled="busy || !selected || !status" @click="bind('upload')">{{ t('上传到空远端', 'Upload to empty remote') }}</button><button :disabled="busy || !selected || !status" @click="bind('download')">{{ t('下载到空本地', 'Download into empty local') }}</button></div>
      <button :disabled="busy || !selected || !status" @click="previewMerge">{{ t('预览合并', 'Preview merge') }}</button>
      <div v-if="preview" class="sync-preview">
        <p>{{ t('预览文件数', 'Files in preview') }} {{ preview.items.length }}</p>
        <ul><li v-for="item in previewItems" :key="item.path">{{ item.path }} · {{ item.action }}</li></ul>
        <div class="inline-actions"><button :disabled="previewPage === 0" @click="previewPage--">{{ t('上一页', 'Previous') }}</button><button :disabled="(previewPage + 1) * 100 >= preview.items.length" @click="previewPage++">{{ t('下一页', 'Next') }}</button><button :disabled="busy" @click="merge">{{ t('确认合并并绑定', 'Confirm merge and bind') }}</button></div>
      </div>
    </div>
    <fieldset v-if="status" :disabled="busy || !!status.binding" class="sync-scope">
      <legend>{{ t('可选同步内容', 'Optional sync content') }}</legend>
      <label><input type="checkbox" :checked="status.optional_scope?.persona ?? false" @change="setScope('persona', $event)" />{{ t('工作区人设', 'Workspace persona') }}</label>
      <label><input type="checkbox" :checked="status.optional_scope?.layout ?? false" @change="setScope('layout', $event)" />{{ t('侧栏布局', 'Sidebar layout') }}</label>
      <label><input type="checkbox" :checked="status.optional_scope?.conversations ?? false" @change="setScope('conversations', $event)" />{{ t('对话记录', 'Conversation history') }}</label>
      <label><input type="checkbox" :checked="status.optional_scope?.agent_history ?? false" @change="setScope('agent_history', $event)" />{{ t('已结束的 Agent 历史', 'Completed agent history') }}</label>
      <label><input type="checkbox" :checked="status.optional_scope?.provider_settings ?? false" @change="setScope('provider_settings', $event)" />{{ t('Provider 通用参数（不含凭据）', 'Provider parameters (credentials excluded)') }}</label>
      <label><input type="checkbox" :checked="status.optional_scope?.extension_installations ?? false" @change="setScope('extension_installations', $event)" />{{ t('扩展安装清单（需重新下载和授权）', 'Extension list (download and authorize again)') }}</label>
      <p>{{ t('默认仅保存在本机。修改已绑定范围时，请先解除绑定，再重新预览合并；关闭选项不会删除远端内容。', 'Kept locally by default. Unbind before changing scope, then preview a new merge. Disabling an option does not delete remote content.') }}</p>
    </fieldset>
    <div v-if="status?.binding" class="sync-bound">
      <p>{{ status.binding.endpoint }} · {{ status.binding.account }} · {{ status.binding.remote_vault }}</p>
      <p aria-live="polite">{{ status.paused ? t('已暂停', 'Paused') : status.running ? t('同步中', 'Syncing') : status.halted ? t('自动同步已停止，请处理错误后重试', 'Automatic sync stopped; resolve the error and retry') : t('等待下一轮同步', 'Waiting for next sync') }} · {{ t('待上传', 'Pending') }} {{ status.pending }} · cursor {{ status.binding.cursor }}</p>
      <p v-if="status.credential_state !== 'ready'" role="status">{{ status.credential_state }}</p>
      <p v-if="status.error" role="alert">{{ status.error }}<span v-if="status.retry_in && !status.halted"> · {{ status.retry_in }}s</span></p>
      <div class="inline-actions">
        <button :disabled="busy || status.running || status.paused" @click="act(async () => { await hostInvoke('sync_run') })">{{ t('立即同步', 'Sync now') }}</button>
        <button :disabled="busy" @click="act(async () => { await hostInvoke('sync_pause', { bindingId: status!.binding!.id, paused: !status!.paused }) })">{{ status.paused ? t('继续同步', 'Resume sync') : t('暂停同步', 'Pause sync') }}</button>
        <button :disabled="busy" @click="unbind">{{ t('解除绑定', 'Unbind') }}</button>
        <button :disabled="busy" @click="act(async () => { await hostInvoke('sync_logout', { endpoint, account }); connected = false })">{{ t('退出登录', 'Sign out') }}</button>
      </div>
      <details v-if="status.attempts?.length">
        <summary>{{ t('上传作业恢复记录（最多 20 项）', 'Upload recovery records (up to 20)') }}</summary>
        <p v-for="job in status.attempts" :key="job.operation_id">{{ job.path }} · {{ t('尝试次数', 'Attempts') }} {{ job.attempts }} · {{ job.outcome === 'interrupted' ? t('上次上传已中断，将从已确认位置恢复', 'Previous upload interrupted; resumes from the confirmed offset') : job.outcome === 'failed' ? t('上次尝试失败', 'Last attempt failed') : t('上传处理中', 'Upload in progress') }}<span v-if="job.error"> · {{ job.error }}</span></p>
      </details>
      <article v-for="conflict in status.conflicts" :key="conflict.sequence" class="sync-conflict">
        <h3>{{ conflict.local_path }}</h3><p>{{ t('远端版本', 'Remote revision') }} {{ conflict.sequence }} · {{ conflict.remote.operation }} · {{ conflict.remote.path }}</p>
        <div class="inline-actions"><button :disabled="busy" @click="resolve(conflict, 'local')">{{ t('保留本地', 'Keep local') }}</button><button :disabled="busy" @click="resolve(conflict, 'remote')">{{ t('采用远端', 'Use remote') }}</button></div>
        <p v-if="conflict.local_path.startsWith('opennexus-records/')">{{ t('可将本地设置保留为 attachments 目录下的文本副本；副本供查看和恢复，不会自动应用。', 'Keep local settings as a text copy under attachments for inspection and recovery; the copy is not applied automatically.') }}</p>
        <label>{{ t('副本相对路径', 'Relative copy path') }}<input v-model="copies[conflict.sequence]" :placeholder="conflict.local_path.startsWith('opennexus-records/') ? 'attachments/settings-copy.txt' : 'conflicts/note-copy.md'" /></label><button :disabled="busy || !copies[conflict.sequence]" @click="resolve(conflict, 'copy')">{{ t('另存本地副本并采用远端', 'Save local copy and use remote') }}</button>
      </article>
    </div>
  </section>
</template>
<style scoped>
.sync-form,.sync-connect,.sync-bound { display:grid;gap:12px;margin-top:16px }
.sync-form label,.sync-connect label,.sync-conflict label { display:grid;gap:5px }
input,select { padding:8px;border:1px solid var(--border-color);border-radius:6px;background:var(--bg-primary);color:inherit;min-width:0 }
.test-http { display:flex!important;align-items:center }
.inline-actions { display:flex;flex-wrap:wrap;gap:8px }
.sync-conflict { border:1px solid var(--border-color);padding:14px;border-radius:8px;display:grid;gap:10px }
button { padding:7px 12px;cursor:pointer } button:disabled { cursor:default;opacity:.5 }
</style>
