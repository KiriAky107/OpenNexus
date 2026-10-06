<script setup lang="ts">
import { computed } from 'vue'
import { t } from '@/i18n'
import { syncDiff } from './syncDiff'
const props = defineProps<{ before: string; after: string; label: string }>()
const diff = computed(() => syncDiff(props.before, props.after))
</script>
<template>
  <section class="sync-text-diff" :aria-label="label"><h4>{{ label }}</h4>
    <p v-if="!diff.rows.length">{{ t('可见预览范围内没有正文差异。', 'No text changes within the visible preview.') }}</p>
    <div class="diff-scroll" tabindex="0">
      <div v-for="(row, index) in diff.rows" :key="index" class="diff-row" :class="row.kind"><span>{{ row.kind === 'added' ? '+' : row.kind === 'removed' ? '−' : ' ' }} {{ row.left ?? '·' }} → {{ row.right ?? '·' }}</span><pre>{{ row.text }}</pre><small v-if="row.text.endsWith('\r')">CRLF</small></div>
    </div>
    <p v-if="diff.truncated" role="status">{{ t('差异只比较前 400 行，并显示最多 240 行变化与上下文；这不是全文比较。', 'The diff compares the first 400 lines and displays at most 240 changed and context lines; it is not a full comparison.') }}</p>
  </section>
</template>
<style scoped>
h4 { margin-block: var(--space-sm); }
.diff-scroll { max-height: 300px; overflow: auto; border: 1px solid var(--color-border-subtle); }
.diff-row { display: flex; align-items: flex-start; gap: var(--space-sm); padding: 2px var(--space-sm); }
.diff-row > span { flex: 0 0 6em; white-space: pre; font: var(--font-size-xs)/1.6 var(--font-ui-mono); }
pre { margin: 0; min-width: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: var(--font-size-xs)/1.6 var(--font-ui-mono); }
.added { background: color-mix(in srgb, var(--color-success) 12%, var(--color-surface-primary)); }
.removed { background: color-mix(in srgb, var(--color-error) 12%, var(--color-surface-primary)); }
</style>
