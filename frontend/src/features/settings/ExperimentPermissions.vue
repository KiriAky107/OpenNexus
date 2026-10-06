<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { getExperimentPermissions, updateExperimentPermission, type ExperimentPermission, type ExperimentPermissionPolicy } from '@/services/systemService'
import { t } from '@/i18n'
const emit = defineEmits<{ changed: [] }>()
const state = ref<ExperimentPermissionPolicy>(), busy = ref(false), error = ref('')
let generation = 0
async function load() {
  const version = ++generation
  busy.value = true; error.value = ''; state.value = undefined
  try { const value = await getExperimentPermissions(); if (version === generation) state.value = value }
  catch (cause) { if (version === generation) error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { if (version === generation) busy.value = false }
}
async function change(permission: ExperimentPermission, enabled: boolean) {
  if (busy.value || !state.value) return
  const version = generation, revision = state.value.revision
  busy.value = true; error.value = ''
  try {
    const value = await updateExperimentPermission(permission, enabled ? 'confirm' : 'deny', revision)
    if (version === generation) { state.value = value; emit('changed') }
  } catch (cause) {
    if (version === generation) {
      error.value = cause instanceof Error ? cause.message : String(cause)
      state.value = undefined
    }
  } finally { if (version === generation) busy.value = false }
}
function inputChange(permission: ExperimentPermission, event: Event) {
  const input = event.target as HTMLInputElement, enabled = input.checked
  // Keep the visible switch at its last committed value until the save returns.
  // A rejected request must not leave an uncontrolled DOM checkbox enabled.
  input.checked = state.value?.rules[permission] === 'confirm'
  void change(permission, enabled)
}
onMounted(load)
onBeforeUnmount(() => { generation++ })
</script>

<template>
  <section class="experiment-permissions">
    <h3>{{ t('Agent 实验请求', 'Agent experiment requests') }}</h3>
    <p>{{ t('开启后，Agent 可以提出相应请求。每次仍须审核内容并通过桌面原生确认；运行与导入分别批准。设置只保留在本机，不随知识库同步。', 'Enabling a switch lets Agents propose that action. Each action still requires review and native desktop confirmation; running and importing are approved separately. These settings stay on this device and do not sync with the vault.') }}</p>
    <label v-for="permission in ['experiments.run', 'experiments.import'] as const" :key="permission"><input type="checkbox" :checked="state?.rules[permission] === 'confirm'" :disabled="busy || !state" @change="inputChange(permission, $event)" /><span>{{ permission === 'experiments.run' ? t('允许 Agent 提出运行请求', 'Let Agents propose runs') : t('允许 Agent 提出成果导入请求', 'Let Agents propose output imports') }}</span></label>
    <p>{{ t('关闭会阻止新请求，并停止仍在执行该类操作的 Agent。已导入的文件会保留。', 'Disabling blocks new requests and stops Agents still performing this action. Imported files are retained.') }}</p>
    <p v-if="busy" role="status">{{ t('正在保存或读取权限…', 'Saving or reading permissions…') }}</p><p v-if="error" class="error-banner" role="alert">{{ error }}</p><button class="button-secondary" :disabled="busy" @click="load">{{ t('刷新实验权限', 'Refresh experiment permissions') }}</button>
  </section>
</template>

<style scoped>
.experiment-permissions { display: grid; gap: var(--space-sm); padding: var(--space-md); margin-block: var(--space-md); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); }
h3, p { margin: 0; }
label { display: flex; gap: var(--space-sm); align-items: flex-start; }
input { margin-top: 4px; }
</style>
