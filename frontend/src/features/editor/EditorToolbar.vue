<script setup lang="ts">
import { t } from '@/i18n'
withDefaults(defineProps<{ docked?: boolean; touch?: boolean; statusText: string; statusKind?: string; canSave: boolean; mode?: string; modes?: {id:string;label:string}[]; saveLabel?: string }>(), { modes: () => [] })
defineEmits<{ save: []; mode: [value:string] }>()
</script>

<template>
  <header class="editor-header" :class="{ docked, 'touch-toolbar': touch }">
    <slot name="dialogs" />
    <div class="editor-actions">
      <slot name="actions" />
      <span class="save-status" :class="statusKind" role="status">{{ statusText }}</span>
      <slot name="status" />
      <div v-if="modes.length" class="mode-switch" :aria-label="t('编辑模式','Editor mode')"><button v-for="item in modes" :key="item.id" type="button" :class="{active:mode===item.id}" @click="$emit('mode',item.id)">{{ item.label }}</button></div>
      <button type="button" class="save-button" :disabled="!canSave" @click="$emit('save')">{{ saveLabel ?? t('保存','Save') }}</button>
    </div>
  </header>
</template>

<style scoped>

.editor-header {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: var(--space-xs) var(--space-md);
  border-bottom: 1px solid var(--color-border-subtle);
  color: var(--color-text-secondary);
}
.editor-header.docked { padding: 0; border: 0; }
.docked .editor-actions { flex-wrap: nowrap; }
.docked :deep(.save-status.saved), .docked :deep(.save-status.idle) { display: none; }
.docked .editor-actions > :deep(.button-secondary) { padding: 3px 8px; font-size: var(--font-size-xs); }
.docked .mode-switch button, .docked .save-button { padding: 3px 8px; font-size: var(--font-size-xs); }
.editor-actions { flex-wrap: wrap; justify-content: flex-end; }
:deep(.toolbar-toggle) { display: grid; place-items: center; width: 32px; height: 32px; flex-shrink: 0; border-radius: var(--radius-sm); }
:deep(.toolbar-toggle):hover, :deep(.toolbar-toggle)[aria-pressed="true"] { color: var(--color-accent-primary); background: var(--color-accent-soft); }
:deep(.toolbar-toggle):focus-visible { outline: 2px solid var(--color-border-focus); }
.editor-actions, .mode-switch { display: flex; align-items: center; gap: var(--space-sm); }
:deep(.save-status) { color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
:deep(.save-status.dirty), :deep(.save-status.external_changed) { color: var(--color-warning); }
:deep(.save-status.save_failed), :deep(.save-status.conflict) { color: var(--color-error); }
:deep(.save-status.saved) { color: var(--color-success); }
.mode-switch { gap: 2px; padding: 2px; border-radius: var(--radius-md); background: var(--color-background-secondary); }
.mode-switch button, .save-button { padding: 5px 9px; border-radius: var(--radius-sm); }
.mode-switch button.active { background: var(--color-surface-primary); color: var(--color-accent-primary); box-shadow: var(--shadow-sm); }
.save-button { background: var(--color-accent-primary); color: var(--color-text-inverse); }
.save-button:disabled { opacity: .55; }

.touch-toolbar { padding: var(--space-sm); }
.touch-toolbar .editor-actions { width: 100%; }
.touch-toolbar .save-button,.touch-toolbar .mode-switch button,.touch-toolbar :deep(.button-secondary) { min-height: 44px; }
</style>
