<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { hostInvoke } from '@/services/platform/desktop'
import { t } from '@/i18n'

const locked = ref(true)
const busy = ref(false)
const password = ref('')
const confirmation = ref('')
const message = ref('')
function failureMessage(error: unknown, fallback: string) {
  const code = error instanceof Error ? error.message : fallback
  return code === 'CREDENTIALS_BUSY'
    ? t('保险库正在使用中，请等待当前操作完成，或在其他 OpenNexus 实例中锁定后重试。', 'The vault is busy. Wait for the current operation, or lock it in the other OpenNexus instance before retrying.')
    : code
}
async function refresh() {
  const state = await hostInvoke<{ locked: boolean }>('credentials_status')
  locked.value = state.locked
}
async function importLegacy() {
  busy.value = true; message.value = ''
  try {
    const count = await hostInvoke<number | null>('credentials_import')
    if (count !== null) message.value = t(`已迁移并验证 ${count} 条凭据；旧文件仍保留。`, `Imported and verified ${count} credentials. Legacy files are retained.`)
  } catch (error) { message.value = failureMessage(error, 'MIGRATION_FAILED') }
  finally { busy.value = false }
}
async function backup() {
  busy.value = true; message.value = ''
  try {
    if (await hostInvoke<boolean>('credentials_backup')) message.value = t('已导出加密备份，请保留备份时使用的口令。', 'Encrypted backup exported. Keep the password used for this backup.')
  } catch (error) { message.value = failureMessage(error, 'CREDENTIAL_BACKUP_FAILED') }
  finally { busy.value = false }
}
async function restore() {
  if (password.value.length < 12) { message.value = t('请输入备份的口令。', 'Enter the backup password.'); return }
  busy.value = true; message.value = ''
  const value = password.value
  password.value = ''; confirmation.value = ''
  try {
    const count = await hostInvoke<number | null>('credentials_restore', { password: value })
    if (count !== null) message.value = t(`已恢复 ${count} 条凭据，请使用备份口令解锁。`, `Restored ${count} credentials. Unlock with the backup password.`)
    await refresh()
  } catch (error) { message.value = failureMessage(error, 'CREDENTIAL_RESTORE_FAILED') }
  finally { busy.value = false }
}
async function act(action: 'unlock' | 'lock' | 'change_password') {
  if (busy.value) return
  message.value = ''
  if (action === 'change_password' && password.value !== confirmation.value) {
    message.value = t('两次口令不一致。', 'The passwords do not match.'); return
  }
  busy.value = true
  const value = password.value
  password.value = ''; confirmation.value = ''
  try {
    await hostInvoke(`credentials_${action}`, action === 'lock' ? undefined : { password: value })
    await refresh()
    message.value = action === 'change_password' ? t('口令已更新。', 'Password updated.') : ''
  } catch (error) { message.value = failureMessage(error, 'CREDENTIAL_STORE_FAILED') }
  finally { busy.value = false }
}
let statusTimer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  void refresh().catch(error => { message.value = String(error) })
  statusTimer = setInterval(() => { if (!busy.value) void refresh().catch(() => {}) }, 500)
})
onUnmounted(() => clearInterval(statusTimer))
</script>

<template>
  <section class="panel settings-section credential-vault" aria-labelledby="credential-vault-title">
    <h2 id="credential-vault-title">{{ t('设备凭据保险库', 'Device credential vault') }}</h2>
    <p>{{ locked ? t('已锁定：使用模型密钥前请解锁。首次解锁将创建本机保险库。', 'Locked: unlock before using provider credentials. The first unlock creates this device’s vault.') : t('已解锁：密钥仅由本机受控调用使用。', 'Unlocked: credentials are available to authorized local calls.') }}</p>
    <p class="subtle">{{ t('口令至少12个字符。遗失口令后需恢复备份或重新配置密钥；笔记仍可使用。', 'Use at least 12 characters. A lost password requires a backup or re-entering credentials; notes remain available.') }}</p>
    <form @submit.prevent="act(locked ? 'unlock' : 'change_password')">
      <label>{{ locked ? t('解锁口令', 'Vault password') : t('新口令', 'New password') }}
        <input v-model="password" type="password" minlength="12" maxlength="1024" required autocomplete="off" :disabled="busy" />
      </label>
      <label v-if="!locked">{{ t('确认新口令', 'Confirm new password') }}
        <input v-model="confirmation" type="password" minlength="12" maxlength="1024" required autocomplete="off" :disabled="busy" />
      </label>
      <div class="inline-actions">
        <button class="button-primary" type="submit" :disabled="busy">{{ busy ? t('处理中…', 'Working…') : locked ? t('解锁', 'Unlock') : t('更改口令', 'Change password') }}</button>
        <button v-if="!locked" class="button-secondary" type="button" :disabled="busy" @click="act('lock')">{{ t('立即锁定', 'Lock now') }}</button>
        <button v-if="!locked" class="button-secondary" type="button" :disabled="busy" @click="importLegacy">{{ t('迁移旧凭据…', 'Import legacy credentials…') }}</button>
        <button v-if="!locked" class="button-secondary" type="button" :disabled="busy" @click="backup">{{ t('导出加密备份…', 'Export encrypted backup…') }}</button>
        <button v-if="locked" class="button-secondary" type="button" :disabled="busy" @click="restore">{{ t('使用此口令恢复备份…', 'Restore backup with this password…') }}</button>
      </div>
    </form>
    <p v-if="message" role="status">{{ message }}</p>
  </section>
</template>

<style scoped>
.credential-vault form { display: grid; gap: 12px; max-width: 480px; }
.credential-vault label { display: grid; gap: 6px; }
.credential-vault input { color: var(--text-primary); background: var(--bg-primary); border: 1px solid var(--border-color); border-radius: 6px; padding: 8px; }
</style>
