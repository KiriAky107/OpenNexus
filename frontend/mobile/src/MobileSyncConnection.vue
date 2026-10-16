<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { t } from '../../src/i18n'
import AppDialog from '../../src/components/common/AppDialog.vue'
import SyncConnectionForm from '../../src/features/settings/SyncConnectionForm.vue'
import { syncError, syncTime } from '../../src/features/settings/syncPresentation'
import { mobileSyncAuth } from './native'
import { createSyncAuthSession } from './syncAuthSession'
import type { AuthProfile, AuthSelection } from './syncAuthTypes'

const session = createSyncAuthSession(mobileSyncAuth), state = session.state
const endpoint = ref('https://'), account = ref(''), password = ref(''), device = ref('OpenNexus Android')
type Confirmation = { kind: 'choose'; profile: AuthProfile; frozen: ReturnType<typeof session.freezeProfile> }
  | { kind: 'logout' | 'disconnect' | 'resume_erasure'; selection: AuthSelection }
const confirmation = ref<Confirmation | null>(null)
let disposed = false
const originalCode = computed(() => state.receipt?.rejected?.code ?? state.current?.rejected?.code ?? state.error)
const mayRefresh = computed(() => state.current?.intent?.phase === 'active' && !state.pending)
const mayErase = computed(() => state.current?.intent?.phase === 'erase_pending'
  && (state.current.intent.kind === 'local_disconnect' || state.current.intent.server_logout_confirmed))
const frozen = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
function feedback(code: string) {
  switch (code) {
    case 'LOGIN_FAILED': return t('账户或密码不正确。', 'The account or password is incorrect.')
    case 'RATE_LIMITED': return t('登录请求过于频繁，请稍后再试。', 'Too many sign-in requests. Try again later.')
    case 'SYNC_AUTH_SELECTION_CHANGED': case 'SYNC_AUTH_IDENTITY_CHANGED': return t('选中的账户已变化，请读取当前连接后重新审核。', 'The selected account changed. Read the current connection and review it again.')
    case 'SYNC_CREDENTIALS_UNAVAILABLE': return t('无法读取或保存本机安全凭据。原操作记录已保留。', 'Device secure credentials could not be read or saved. The original operation record is retained.')
    case 'SYNC_FOREGROUND_CHANGED': case 'SYNC_FOREGROUND_REQUIRED': return t('连接操作已停止。回到应用后读取原操作结果。', 'The connection operation stopped. Read its original result after returning to the app.')
    case 'SYNC_AUTH_INTENT_NOT_FOUND': return t('尚无该原编号的本机记录。不会自动重新发送。', 'No local record exists for that original ID. It will not be sent again automatically.')
    case 'SYNC_ENDPOINT_INVALID': return t('请输入完整的 HTTPS 服务地址。', 'Enter a complete HTTPS service address.')
    case 'SYNC_LOGIN_INVALID': return t('请核对账户、设备名称，以及 12–256 个字符的密码。', 'Check the account, device name and a password containing 12–256 characters.')
    default: return syncError(code)
  }
}
function phase(value: string) {
  switch (value) {
    case 'active': return t('会话已保存', 'Session saved')
    case 'login_pending': return t('登录结果待核对', 'Sign-in result needs checking')
    case 'store_pending': return t('安全存储结果待核对', 'Secure storage needs checking')
    case 'refresh_pending': return t('刷新结果待核对', 'Refresh result needs checking')
    case 'logout_pending': return t('服务退出结果待核对', 'Server sign-out needs checking')
    case 'erase_pending': return t('本机清除待完成', 'Local erasure is pending')
    case 'disconnected': return t('已断开本机连接', 'Locally disconnected')
    default: return t('原结果未确认', 'Original result is unconfirmed')
  }
}
function login() {
  const secret = password.value
  password.value = ''; confirmation.value = null
  return session.login(endpoint.value, account.value, device.value, secret)
}
function reviewProfile(profile: AuthProfile) {
  password.value = ''
  confirmation.value = { kind: 'choose', profile: frozen(profile), frozen: session.freezeProfile(profile) }
}
function review(kind: 'logout' | 'disconnect' | 'resume_erasure') {
  if (!state.selection) return
  password.value = ''; confirmation.value = { kind, selection: frozen(state.selection) }
}
async function confirm() {
  const action = confirmation.value; if (!action || state.busy) return
  confirmation.value = null; password.value = ''
  if (action.kind === 'choose') { if (await session.choose(action.profile, action.frozen)) await session.reload() }
  else if (action.kind === 'resume_erasure') await session.resumeErasure(action.selection)
  else await session.existing(action.kind, action.selection)
}
async function acknowledge() { if (session.acknowledgeOriginal()) await session.reload() }
function handleBack() { if (!confirmation.value) return false; confirmation.value = null; password.value = ''; return true }
function hidden() { if (document.hidden) { password.value = ''; confirmation.value = null; void session.cancel() } }
watch([endpoint, account, device], () => { password.value = ''; confirmation.value = null }, { flush: 'sync' })
onMounted(async () => {
  document.addEventListener('visibilitychange', hidden)
  await session.reload()
  if (!disposed && state.selection) { endpoint.value = state.selection.endpoint; account.value = state.selection.account }
})
onBeforeUnmount(() => { disposed = true; password.value = ''; confirmation.value = null; document.removeEventListener('visibilitychange', hidden); void session.dispose() })
defineExpose({ handleBack })
</script>

<template>
  <section class="mobile-sync settings-section" :aria-label="t('同步账户连接', 'Sync account connection')" :aria-busy="state.busy">
    <h3>{{ t('服务器连接与登录', 'Server connection and sign-in') }}</h3>
    <p class="muted">{{ t('登录只建立账户连接。选择并审核知识库绑定后才传输笔记。', 'Signing in establishes an account connection. Notes transfer only after you select and review a vault association.') }}</p>
    <p v-if="originalCode" class="feedback" role="alert">{{ feedback(originalCode) }}</p>
    <article v-if="state.current?.intent" class="sync-overview">
      <h4>{{ t('当前选中的本机连接', 'Currently selected device connection') }}</h4>
      <dl><dt>{{ t('服务', 'Service') }}</dt><dd>{{ state.current.intent.endpoint }}</dd><dt>{{ t('账户', 'Account') }}</dt><dd>{{ state.current.intent.account }}</dd><dt>{{ t('设备名称', 'Device name') }}</dt><dd>{{ state.current.intent.device_name }}</dd></dl>
      <p role="status">{{ phase(state.current.intent.phase) }} · {{ state.current.authenticated ? t('已登录', 'Signed in') : t('未确认可用会话', 'No confirmed usable session') }}</p>
      <p v-if="state.current.expires_at">{{ t('会话到期', 'Session expiry') }} · {{ syncTime(state.current.expires_at) }}</p>
      <p v-if="state.current.credential_change_required" class="feedback">{{ t('请先在 Sync 服务上完成初始密码设置，再重新连接。', 'Complete initial password setup on your Sync service, then reconnect.') }}</p>
      <p v-if="state.current.intent.server_logout_confirmed" role="status">{{ t('服务已确认原设备会话退出。', 'The service confirmed sign-out of the original device session.') }}</p>
      <div class="inline-actions"><button class="button-secondary" :disabled="state.busy || !mayRefresh" @click="session.existing('refresh')">{{ t('刷新会话', 'Refresh session') }}</button><button class="button-secondary" :disabled="state.busy || !mayRefresh" @click="review('logout')">{{ t('从服务退出', 'Sign out on server') }}</button><button class="button-secondary" :disabled="state.busy || !!state.pending || !state.current.intent.device_id" @click="review('disconnect')">{{ t('仅断开本机连接', 'Disconnect this device locally') }}</button><button v-if="mayErase" class="button-secondary" :disabled="state.busy" @click="review('resume_erasure')">{{ t('继续原本机清除', 'Resume original local erasure') }}</button></div>
    </article>
    <button class="button-secondary" :disabled="state.busy" @click="session.reload">{{ t('读取当前连接', 'Read current connection') }}</button>
    <SyncConnectionForm v-model:endpoint="endpoint" v-model:account="account" v-model:password="password" v-model:device="device" :busy="state.busy || !!state.pending" :submit-disabled="!!state.pending" @login="login" />
    <section v-if="state.pending" class="original-result" :aria-label="t('核对原操作', 'Check original operation')">
      <h4>{{ t('核对原操作', 'Check original operation') }}</h4><code>{{ state.pending.operation_id }}</code><p>{{ state.pending.endpoint }} · {{ state.pending.account }}</p>
      <p>{{ t('不会自动重发。先读取原编号记录，再决定是否重新登录。重新登录会创建新的设备会话。', 'Nothing is resent automatically. Read the original ID before deciding to sign in again. A new sign-in creates a new device session.') }}</p>
      <p v-if="state.receipt?.intent">{{ phase(state.receipt.intent.phase) }}</p>
      <p v-if="state.choice">{{ t('原选择回执已找到；它不代表当前仍选中该账户。', 'The original choice receipt was found; it does not mean that account is still selected.') }}</p>
      <p v-if="state.originalMissing">{{ t('未找到原记录。', 'The original record was not found.') }}</p>
      <div class="inline-actions"><button class="button-secondary" :disabled="state.busy" @click="session.checkOriginal">{{ t('只读查询原编号', 'Read original ID') }}</button><button class="button-secondary" :disabled="state.busy || !(state.receipt || state.choice || state.originalMissing)" @click="acknowledge">{{ t('保留原记录并继续', 'Keep original record and continue') }}</button></div>
    </section>
    <section v-if="state.profiles" :aria-label="t('已保存的连接', 'Saved connections')"><h4>{{ t('已保存的连接', 'Saved connections') }}</h4>
      <p v-if="!state.profiles.total">{{ t('尚无本机连接记录。', 'No device connection records yet.') }}</p>
      <article v-for="profile in state.profiles.items" :key="profile.intent.operation_id" class="profile-row"><div><strong>{{ profile.intent.account }}</strong><p>{{ profile.intent.endpoint }}</p><span>{{ phase(profile.intent.phase) }} · {{ profile.intent.device_name }}</span><p v-if="profile.selected">{{ t('当前选中', 'Currently selected') }}</p></div><button class="button-secondary" :disabled="state.busy || !!state.pending || profile.selected" @click="reviewProfile(profile)">{{ t('审核并选择', 'Review and select') }}</button></article>
      <nav v-if="state.profiles.total > 20" class="inline-actions" :aria-label="t('连接分页', 'Connection pages')"><button class="button-secondary" :disabled="state.busy || !state.profiles.offset" @click="session.profiles(state.profiles.offset - 20)">{{ t('上一页', 'Previous') }}</button><span>{{ state.profiles.offset + 1 }}–{{ Math.min(state.profiles.offset + 20,state.profiles.total) }} / {{ state.profiles.total }}</span><button class="button-secondary" :disabled="state.busy || state.profiles.offset + 20 >= state.profiles.total" @click="session.profiles(state.profiles.offset + 20)">{{ t('下一页', 'Next') }}</button></nav>
    </section>
    <AppDialog v-if="confirmation" :label="t('审核连接操作', 'Review connection action')" @close="handleBack"><section class="modal-card connection-confirm">
      <h3>{{ t('审核连接操作', 'Review connection action') }}</h3>
      <template v-if="confirmation.kind === 'choose'"><p>{{ t('选中此本机连接？这不会改变知识库绑定或传输笔记。', 'Select this device connection? This does not change the vault association or transfer notes.') }}</p><p>{{ confirmation.profile.intent.endpoint }} · {{ confirmation.profile.intent.account }} · {{ confirmation.profile.intent.device_name }}</p><code>{{ confirmation.frozen.operation_id }}</code></template>
      <template v-else><p>{{ confirmation.selection.endpoint }} · {{ confirmation.selection.account }}</p><code>{{ confirmation.selection.operation_id }}</code><p v-if="confirmation.kind === 'logout'">{{ t('请求服务退出原设备会话，并清除该连接的本机凭据。未知结果先核对原编号。', 'Ask the service to sign out the original device session and erase its local credentials. Check the original ID if the result is uncertain.') }}</p><p v-else>{{ t('只清除当前选中连接的本机凭据，保留笔记、草稿和同步记录。这不证明服务已撤销会话。', 'Erase only the selected connection’s local credentials, keeping notes, drafts and sync records. This does not prove the service revoked the session.') }}</p></template>
      <div class="inline-actions"><button class="button-secondary" @click="handleBack">{{ t('取消', 'Cancel') }}</button><button class="button-primary" @click="confirm">{{ t('确认此操作', 'Confirm this action') }}</button></div>
    </section></AppDialog>
  </section>
</template>

<style scoped>
.mobile-sync { display:grid; gap:var(--space-md); min-width:0; }
.mobile-sync h3,.mobile-sync h4,.mobile-sync p { margin:0; }
.mobile-sync p,.mobile-sync code,.mobile-sync dd { overflow-wrap:anywhere; }
.muted { color:var(--color-text-secondary); }
.sync-overview,.original-result,.profile-row { padding:var(--space-md); border:1px solid var(--color-border-default); border-radius:var(--radius-md); background:var(--color-background-secondary); }
.sync-overview,.original-result { display:grid;gap:var(--space-sm); }
dl { display:grid;grid-template-columns:max-content minmax(0,1fr);gap:8px;margin:0; } dd { margin:0; }
.inline-actions,.profile-row { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.profile-row { justify-content:space-between; margin-block:8px; }
.profile-row>div { flex:1; min-width:160px; }
button { min-height:44px; }
.feedback { color:var(--color-warning); }
.connection-confirm { display:grid;gap:var(--space-md);padding:var(--space-lg);width:min(560px,100%); }
</style>
