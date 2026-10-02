<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ReferenceChangePlan } from '@/services/referenceChangeService'
import { referenceChangeHunks } from '@/services/referenceChangePreview'
import { t } from '@/i18n'
const props = defineProps<{ item: ReferenceChangePlan['updates'][number] }>()
const page = ref(0), pageSize = 5
const hunks = computed(() => referenceChangeHunks(props.item.before, props.item.after, props.item.edits))
const pages = computed(() => Math.max(1, Math.ceil(hunks.value.length / pageSize)))
const visible = computed(() => hunks.value.slice(page.value * pageSize, (page.value + 1) * pageSize))
watch(() => props.item, () => { page.value = 0 })
</script>
<template>
  <section class="reference-preview" :aria-label="t('引用修改差异', 'Reference changes')">
    <p>{{ t('全部修改片段', 'All change excerpts') }}: {{ hunks.length }}</p>
    <div v-for="(hunk, index) in visible" :key="page * pageSize + index" class="change-excerpt">
      <p>{{ t('更新前 · 起始行', 'Before · starting line') }} {{ hunk.beforeLine }}</p>
      <pre class="before">{{ hunk.before }}</pre>
      <p>{{ t('更新后 · 起始行', 'After · starting line') }} {{ hunk.afterLine }}</p>
      <pre class="after">{{ hunk.after }}</pre>
    </div>
    <nav v-if="pages > 1" class="inline-actions" :aria-label="t('修改片段分页', 'Change excerpt pages')">
      <button class="button-secondary" :disabled="page === 0" @click="page--">{{ t('上一页', 'Previous') }}</button>
      <span role="status">{{ page + 1 }} / {{ pages }}</span>
      <button class="button-secondary" :disabled="page + 1 === pages" @click="page++">{{ t('下一页', 'Next') }}</button>
    </nav>
  </section>
</template>
<style scoped>
.change-excerpt { padding-block: var(--space-sm); border-bottom: 1px solid var(--color-border-subtle); }
.change-excerpt p { margin-block: var(--space-xs); }
pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; padding: var(--space-sm); font: var(--font-size-xs)/1.6 var(--font-ui-mono); user-select: text; }
.before { background: color-mix(in srgb, var(--color-error) 10%, var(--color-surface-primary)); }
.after { background: color-mix(in srgb, var(--color-success) 10%, var(--color-surface-primary)); }
</style>
