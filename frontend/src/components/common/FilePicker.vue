<script setup lang="ts">
import { useId } from 'vue'
import { Upload } from '@element-plus/icons-vue'

defineProps<{
  file: File | null
  label: string
  emptyLabel: string
  accept?: string
  disabled?: boolean
}>()

const emit = defineEmits<{ select: [file: File | null] }>()
const inputId = useId()

function selectFile(event: Event) {
  emit('select', (event.target as HTMLInputElement).files?.[0] ?? null)
}

function allowReselect(event: MouseEvent) {
  ;(event.currentTarget as HTMLInputElement).value = ''
}
</script>

<template>
  <div class="file-picker" :class="{ disabled }">
    <input
      :id="inputId"
      class="file-picker-input"
      type="file"
      :accept="accept"
      :disabled="disabled"
      @click="allowReselect"
      @change="selectFile"
    />
    <label class="file-picker-trigger" :for="inputId">
      <Upload aria-hidden="true" />
      <span>{{ label }}</span>
    </label>
    <span class="file-picker-name" :class="{ empty: !file }" :title="file?.name || emptyLabel">
      {{ file?.name || emptyLabel }}
    </span>
  </div>
</template>

<style scoped>
.file-picker { display: flex; min-width: 0; align-items: center; gap: var(--space-sm); }
.file-picker-input { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; }
.file-picker-trigger { display: inline-flex; min-height: 36px; flex: 0 0 auto; align-items: center; gap: var(--space-sm); padding: 0 var(--space-md); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-primary); font-weight: 600; cursor: pointer; transition: border-color var(--motion-fast), background-color var(--motion-fast), color var(--motion-fast), box-shadow var(--motion-fast), transform var(--motion-fast); }
.file-picker-trigger svg { width: 16px; height: 16px; }
.file-picker-trigger:hover { border-color: var(--color-accent-secondary); background: var(--color-background-hover); color: var(--color-accent-primary); transform: translateY(-1px); }
.file-picker-input:focus-visible + .file-picker-trigger { outline: 2px solid var(--color-border-focus); outline-offset: 2px; }
.file-picker-name { min-width: 0; overflow: hidden; color: var(--color-text-secondary); text-overflow: ellipsis; white-space: nowrap; user-select: text; }
.file-picker-name.empty { color: var(--color-text-tertiary); }
.disabled { opacity: .55; }
.disabled .file-picker-trigger { cursor: not-allowed; transform: none; }
@media (max-width: 560px) { .file-picker { align-items: stretch; flex-direction: column; } .file-picker-trigger { justify-content: center; } }
</style>
