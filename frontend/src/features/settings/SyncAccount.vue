<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { SyncAccount } from '@/contracts/sync'
import { hostInvoke } from '@/services/platform/desktop'
import { t } from '@/i18n'
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
import { syncBytes, syncError, syncTime } from './syncPresentation'
const props = defineProps<{ vaultId: string; bindingId: string; remoteVaultId: string; disabled: boolean }>()
const { actionDialog, resolveAction, askConfirm } = useActionDialog()
const details = ref<SyncAccount>(), busy = ref(false), error = ref('')
const identity = computed(() => JSON.stringify([props.vaultId, props.bindingId, props.remoteVaultId]))
let generation = 0, timer: ReturnType<typeof setInterval> | undefined
async function load() {
  if (busy.value || props.disabled) return
  const version = ++generation
  busy.value = true; error.value = ''
  try {
    const value = await hostInvoke<SyncAccount>('sync_account_status', { request: { vault_id: props.vaultId, binding_id: props.bindingId } })
    if (version !== generation) return
    if (value.vault_id !== props.vaultId || value.binding_id !== props.bindingId || value.vault.id !== props.remoteVaultId) throw new Error('SYNC_BINDING_CHANGED')
    details.value = value
  } catch (cause) { if (version === generation) error.value = syncError(cause) }
  finally { if (version === generation) busy.value = false }
}
watch(identity, () => { generation++; details.value = undefined; busy.value = false; error.value = ''; void load() }, { immediate: true })
watch(() => props.disabled, disabled => { if (!disabled && !details.value) void load() })
onMounted(() => { timer = setInterval(() => { void load() }, 30000) })
onBeforeUnmount(() => { generation++; clearInterval(timer) })
async function revoke(device: SyncAccount['devices'][number]) {
  const snapshot = details.value, version = generation
  if (!snapshot || busy.value || props.disabled || device.revoked || device.id === snapshot.current_device_id) return
  busy.value = true; error.value = ''
  try {
    if (!(await askConfirm(t(`撤销设备“${device.name}”的同步访问？该设备需要重新登录，本地文件不会删除。`, `Revoke sync access for “${device.name}”? That device must sign in again; its local files remain.`)))) return
    if (version !== generation || props.disabled || details.value !== snapshot) return
    await hostInvoke('sync_revoke_device', { request: { vault_id: snapshot.vault_id, binding_id: snapshot.binding_id, device_id: device.id } })
    if (version === generation) { busy.value = false; await load() }
  } catch (cause) { if (version === generation) error.value = syncError(cause) }
  finally { if (version === generation) busy.value = false }
}
</script>
<template>
  <section class="sync-account" :aria-label="t('设备与用量', 'Devices and usage')">
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
    <div class="inline-actions"><h3>{{ t('设备与用量', 'Devices and usage') }}</h3><button :disabled="busy || disabled" @click="load">{{ t('刷新设备与用量', 'Refresh devices and usage') }}</button></div>
    <p v-if="busy" role="status">{{ t('正在读取服务状态…', 'Reading service status…') }}</p><p v-if="error" role="alert">{{ error }}</p>
    <template v-if="details">
      <div class="transport">
        <h4>{{ t('传输与内容保护', 'Transport and content protection') }}</h4>
        <p v-if="details.capabilities?.transport_security === 'tls'">{{ t('此连接使用 HTTPS / TLS 加密传输。', 'This connection uses HTTPS / TLS encryption in transit.') }}</p>
        <p v-else-if="details.capabilities?.transport_security === 'test-http'" role="alert">{{ t('此连接使用测试 HTTP，传输未加密。', 'This connection uses test HTTP without encryption in transit.') }}</p>
        <template v-if="details.capabilities">
          <p>{{ t('服务端可以读取存储的文件内容和路径；当前同步没有端到端加密。', 'The service can read stored file content and paths. Current sync does not use end-to-end encryption.') }}</p>
          <p>{{ t('单对象上限', 'Object size limit') }} {{ syncBytes(details.capabilities.max_object_size) }} · {{ t('上传分块', 'Upload chunk') }} {{ syncBytes(details.capabilities.chunk_size) }}</p>
          <p v-if="details.capabilities.features">{{ t('支持从已确认偏移续传。实验文件仅保存和传输，不会因同步而运行。', 'Uploads resume from confirmed offsets. Experiment files are stored and transferred; sync does not run them.') }}</p>
          <p v-else>{{ t('已核对基本协议；服务未提供详细能力声明。', 'The basic protocol is verified; the service does not provide a detailed capability declaration.') }}</p>
        </template>
        <p v-else>{{ t('尚未读取传输能力，请刷新。', 'Transport capabilities have not been read. Refresh this view.') }}</p>
      </div>
      <p>{{ details.vault.name }} · {{ t('已存储对象', 'Stored objects') }} {{ syncBytes(details.vault.used) }} / {{ syncBytes(details.vault.quota) }} · {{ t('剩余配额', 'Remaining quota') }} {{ syncBytes(Math.max(0, details.vault.quota - details.vault.used)) }}</p>
      <p class="subtle">{{ t('用量由服务返回，包含保留的对象和历史内容；此处不估计尚未确认的上传预留。', 'Usage is reported by the service and includes retained objects and history. This view does not estimate unconfirmed upload reservations.') }}</p>
      <p v-if="details.vault.used >= details.vault.quota" role="alert">{{ t('远端配额已满，请处理后再上传。', 'Remote quota is full. Resolve it before uploading.') }}</p>
      <p>{{ t('读取时间', 'Checked at') }} · {{ syncTime(details.checked_at) }}</p>
      <ul><li v-for="device in details.devices" :key="device.id"><span>{{ device.name }} · <code :title="device.id">{{ device.id.slice(0, 8) }}</code> · {{ device.revoked ? t('已撤销', 'Revoked') : t('可访问', 'Authorized') }}<span v-if="device.id === details.current_device_id"> · {{ t('当前设备', 'This device') }}</span></span><button :disabled="busy || disabled || device.revoked || device.id === details.current_device_id" @click="revoke(device)">{{ t('撤销访问', 'Revoke access') }}</button></li></ul>
    </template>
  </section>
</template>
<style scoped>
.sync-account { min-width: 0; overflow-wrap: anywhere; }
.transport { padding: var(--space-md); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); }
ul { padding: 0; list-style: none; display: grid; gap: var(--space-sm); }
li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); flex-wrap: wrap; }
button { padding: 8px 12px; border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-primary); color: var(--color-text-primary); font: inherit; cursor: pointer; }
button:disabled { cursor: default; opacity: .55; }
</style>
