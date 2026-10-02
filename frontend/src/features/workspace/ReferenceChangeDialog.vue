<script setup lang="ts">
import { ref } from 'vue'
import type { ReferenceChangePlan } from '@/services/referenceChangeService'
import AppDialog from '@/components/common/AppDialog.vue'
import ReferenceChangePreview from './ReferenceChangePreview.vue'
import { t } from '@/i18n'
defineProps<{ plan: ReferenceChangePlan }>()
defineEmits<{ resolve: [update: boolean | null] }>()
const expanded = ref(new Set<string>())
function toggle(path: string, event: Event) {
  if ((event.target as HTMLDetailsElement).open) expanded.value.add(path)
  else expanded.value.delete(path)
}
</script>
<template>
  <AppDialog :label="t('引用影响预览', 'Reference impact')" @close="$emit('resolve', null)">
    <section class="reference-review modal-card">
      <h2>{{ t('引用影响预览', 'Reference impact') }}</h2>
      <p>{{ plan.oldPath }} → {{ plan.newPath ?? t('删除', 'Delete') }}</p>
      <p>{{ t('受影响引用', 'Affected references') }}: {{ plan.affected.length }} · {{ t('可安全更新的文件', 'Files ready for safe update') }}: {{ plan.updates.length }}</p>
      <p v-if="!plan.newPath">{{ t('删除后这些引用将保留原文，并列入失效链接。', 'Deleting keeps the references and lists them as broken links.') }}</p>
      <div class="reference-sources"><details v-for="item in plan.updates" :key="item.path" @toggle="toggle(item.path, $event)"><summary>{{ item.path }} · {{ item.count }} {{ t('处引用', 'references') }}</summary><ReferenceChangePreview v-if="expanded.has(item.path)" :item="item" /></details>
      <p v-for="source in [...new Set(plan.affected.map(ref => ref.source))]" :key="source">{{ source }}</p></div>
      <p v-if="plan.pending.length">{{ t('以下引用无法确定，将保留原文：', 'Uncertain references remain unchanged:') }}</p><ul><li v-for="(ref,index) in plan.pending" :key="index">{{ ref.source }}: {{ ref.raw }}</li></ul>
      <div class="inline-actions"><button v-if="plan.newPath && plan.updates.length" class="button-primary" @click="$emit('resolve', true)">{{ t('更新引用并移动', 'Update references and move') }}</button><button class="button-secondary" @click="$emit('resolve', false)">{{ plan.newPath ? t('移动并保留引用原文', 'Move and keep references') : t('确认删除', 'Confirm delete') }}</button><button class="button-secondary" @click="$emit('resolve', null)">{{ t('取消', 'Cancel') }}</button></div>
    </section>
  </AppDialog>
</template>
<style scoped>
.reference-review { width: min(760px, 100%); display: grid; gap: var(--space-md); overflow-wrap: anywhere; padding: var(--space-lg); background: var(--color-surface-primary); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md); }
.reference-sources { max-height: 45vh; overflow: auto; }
.inline-actions { flex-wrap: wrap; }
summary { cursor: pointer; padding-block: var(--space-sm); }
</style>
