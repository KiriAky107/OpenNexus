<script setup lang="ts">
import { t } from '../../i18n'

defineProps<{ items: Array<{ path: string; action: string }>; total: number; offset: number; limit: number; busy: boolean }>()
const emit = defineEmits<{ page: [offset: number]; confirm: [] }>()
function action(value: string) {
  switch (value) {
    case 'upload': return t('上传', 'Upload')
    case 'download': return t('下载', 'Download')
    case 'identical': return t('内容一致', 'Identical content')
    case 'conflict': return t('保留为待解决冲突', 'Keep as an unresolved conflict')
    default: return value
  }
}
</script>

<template>
  <section class="sync-preview" :aria-label="t('初始合并预览', 'Initial merge preview')" :aria-busy="busy">
    <p>{{ t('预览文件数', 'Files in preview') }} {{ total }}</p>
    <ul><li v-for="item in items" :key="item.path"><span>{{ item.path }}</span> · {{ action(item.action) }}</li></ul>
    <div class="preview-actions">
      <button class="button-secondary" :disabled="busy || offset === 0" @click="emit('page', Math.max(0, offset - limit))">{{ t('上一页', 'Previous') }}</button>
      <button class="button-secondary" :disabled="busy || offset + limit >= total" @click="emit('page', offset + limit)">{{ t('下一页', 'Next') }}</button>
      <button class="button-primary" :disabled="busy" @click="emit('confirm')">{{ t('确认合并并绑定', 'Confirm merge and bind') }}</button>
    </div>
  </section>
</template>

<style scoped>
.sync-preview { display:grid; gap:var(--space-sm); min-width:0; }
p { margin:0; }
ul { margin:0; padding-inline-start:1.4em; }
li { overflow-wrap:anywhere; }
.preview-actions { display:flex; flex-wrap:wrap; gap:8px; }
button { min-height:44px; max-width:100%; white-space:normal; }
</style>
