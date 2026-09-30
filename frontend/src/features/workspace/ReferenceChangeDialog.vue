<script setup lang="ts">
import type { ReferenceChangePlan } from '@/services/referenceChangeService'
import AppDialog from '@/components/common/AppDialog.vue'
import { t } from '@/i18n'
defineProps<{ plan: ReferenceChangePlan }>()
defineEmits<{ resolve: [update: boolean | null] }>()
</script>
<template>
  <AppDialog :label="t('引用影响预览', 'Reference impact')" @close="$emit('resolve', null)">
    <section class="reference-review">
      <h2>{{ t('引用影响预览', 'Reference impact') }}</h2>
      <p>{{ plan.oldPath }} → {{ plan.newPath ?? t('删除', 'Delete') }}</p>
      <p>{{ t('受影响引用', 'Affected references') }}: {{ plan.affected.length }} · {{ t('可安全更新的文件', 'Files ready for safe update') }}: {{ plan.updates.length }}</p>
      <p v-if="!plan.newPath">{{ t('删除后这些引用将保留原文，并列入失效链接。', 'Deleting keeps the references and lists them as broken links.') }}</p>
      <div class="reference-sources"><details v-for="item in plan.updates" :key="item.path"><summary>{{ item.path }} · {{ item.count }} {{ t('处引用', 'references') }}</summary><p>{{ t('更新前', 'Before') }}</p><pre>{{ item.before.slice(0, 4000) }}</pre><p>{{ t('更新后', 'After') }}</p><pre>{{ item.after.slice(0, 4000) }}</pre><p v-if="item.before.length > 4000 || item.after.length > 4000">{{ t('长文仅展示前 4000 字符；只更新已解析的路径引用。', 'Preview shows the first 4000 characters; only resolved path references are changed.') }}</p></details>
      <p v-for="source in [...new Set(plan.affected.map(ref => ref.source))]" :key="source">{{ source }}</p></div>
      <p v-if="plan.pending.length">{{ t('以下引用无法确定，将保留原文：', 'Uncertain references remain unchanged:') }}</p><ul><li v-for="(ref,index) in plan.pending" :key="index">{{ ref.source }}: {{ ref.raw }}</li></ul>
      <div class="inline-actions"><button v-if="plan.newPath && plan.updates.length" class="button-primary" @click="$emit('resolve', true)">{{ t('更新引用并移动', 'Update references and move') }}</button><button class="button-secondary" @click="$emit('resolve', false)">{{ plan.newPath ? t('移动并保留引用原文', 'Move and keep references') : t('确认删除', 'Confirm delete') }}</button><button class="button-secondary" @click="$emit('resolve', null)">{{ t('取消', 'Cancel') }}</button></div>
    </section>
  </AppDialog>
</template>
<style scoped>
.reference-review { width: min(760px, 100%); display: grid; gap: var(--space-md); overflow-wrap: anywhere; }
.reference-sources { max-height: 45vh; overflow: auto; } pre { white-space: pre-wrap; background: var(--color-background-secondary); padding: var(--space-sm); }
</style>
