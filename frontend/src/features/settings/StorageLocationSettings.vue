<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { t } from '@/i18n'
import { DesktopError } from '@/services/platform/desktop'
import { storageLocationService, type StorageLocationInfo } from '@/services/storageLocationService'

const info = ref<StorageLocationInfo>()
const busy = ref(false)
const error = ref('')

async function load() {
  try { info.value = await storageLocationService.info() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
}

async function change(useDefault = false) {
  busy.value = true
  error.value = ''
  try { info.value = useDefault ? await storageLocationService.useDefault() : await storageLocationService.choose() }
  catch (reason) {
    if (!(reason instanceof DesktopError && reason.code === 'USER_CANCELLED')) error.value = reason instanceof Error ? reason.message : String(reason)
  } finally { busy.value = false }
}

onMounted(load)
</script>

<template>
  <section class="panel storage-location">
    <div>
      <h2>{{ t('数据存储位置', 'Data storage location') }}</h2>
      <p class="subtle">{{ t('保存 AI Core 数据、模型配置、扩展和最近知识库记录。Vault 文件仍保存在各自所选目录。', 'Stores AI Core data, model configuration, extensions, and recent-vault records. Vault files remain in their selected folders.') }}</p>
    </div>
    <dl v-if="info">
      <div><dt>{{ t('当前使用', 'Active') }}</dt><dd>{{ info.active_path }}</dd></div>
      <div v-if="info.configured_path"><dt>{{ t('下次启动', 'Next launch') }}</dt><dd>{{ info.configured_path }}</dd></div>
    </dl>
    <p v-if="info?.restart_required" class="restart-note" role="status">{{ t('新位置将在重启 OpenNexus 后生效。现有数据不会自动移动或删除；如需继续使用，请先自行复制到新目录。', 'The new location takes effect after restarting OpenNexus. Existing data is not moved or deleted; copy it to the new folder first if you want to keep using it.') }}</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <div class="inline-actions">
      <button class="button-secondary" :disabled="busy" @click="change(false)">{{ busy ? t('处理中…', 'Working…') : t('选择数据目录', 'Choose data folder') }}</button>
      <button v-if="info?.custom || info?.configured_path" class="button-secondary" :disabled="busy" @click="change(true)">{{ t('恢复默认位置', 'Use default location') }}</button>
    </div>
  </section>
</template>

<style scoped>
.storage-location{display:grid;gap:14px}.storage-location h2,.storage-location p{margin:0}.storage-location dl{display:grid;gap:8px;margin:0}.storage-location dl div{display:grid;grid-template-columns:100px minmax(0,1fr);gap:12px}.storage-location dt{color:var(--color-text-secondary)}.storage-location dd{margin:0;overflow-wrap:anywhere}.restart-note{padding:10px 12px;border:1px solid var(--color-warning);border-radius:var(--radius-md);background:var(--color-warning-soft)}
</style>
