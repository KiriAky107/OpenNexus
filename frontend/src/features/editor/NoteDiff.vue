<script setup lang="ts">
import type { NoteDiff } from '@/services/noteService'
import { t } from '@/i18n'
defineProps<{ diff: NoteDiff }>()
</script>

<template>
  <section class="note-diff" :aria-label="t('笔记内容差异', 'Note content changes')">
    <p>{{ t('预计新增', 'Added') }} {{ diff.added_chars }} {{ t('字／字符', 'characters') }} · {{ t('删除', 'Removed') }} {{ diff.removed_chars }} {{ t('字／字符', 'characters') }} <small>({{ diff.before_chars }} → {{ diff.after_chars }})</small></p>
    <p v-if="!diff.lines.length" class="subtle">{{ t('正文未变化；请核对标题与标签。', 'Content is unchanged; review the title and tags.') }}</p>
    <div v-else class="diff-lines" tabindex="0">
      <div v-for="(line, index) in diff.lines" :key="index" class="diff-line" :class="line.kind === '+' ? 'added' : 'removed'"><span class="line-number">{{ line.kind }} {{ line.line }}</span><pre>{{ line.text }}</pre></div>
    </div>
    <p v-if="diff.truncated" role="status">{{ t('内容较长，差异仅显示部分行；计数涵盖全部变化。请同时核对原始参数和当前笔记。', 'Only part of this large diff is shown; counts cover all changes. Also review the raw arguments and current note.') }}</p>
  </section>
</template>

<style scoped>
.note-diff { min-width: 0; overflow-wrap: anywhere; }
.diff-lines { max-height: 320px; overflow: auto; border: 1px solid var(--color-border-subtle); border-radius: var(--radius-sm); user-select: text; }
.diff-line { display: flex; align-items: start; gap: var(--space-sm); padding: 3px var(--space-sm); }
.added { background: color-mix(in srgb, var(--color-success) 12%, var(--color-surface-primary)); }
.removed { background: color-mix(in srgb, var(--color-error) 12%, var(--color-surface-primary)); }
.line-number { flex: 0 0 4em; font-family: var(--font-ui-mono); }
pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; min-width: 0; font: var(--font-size-xs)/1.6 var(--font-ui-mono); }
</style>
