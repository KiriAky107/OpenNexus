<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { executeEditorCommand } from '@/services/editorCommandService'
import { t } from '@/i18n'

const editor = useEditorStore()
const open = ref(false)
const menu = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const importItem = ref<HTMLButtonElement | null>(null)
const error = ref('')
const importEnabled = computed(() => editor.mode === 'source' && !!editor.currentFilePath
  && !['conflict', 'external_changed'].includes(editor.saveStatus))

function dismiss(event: PointerEvent) {
  if (!menu.value?.contains(event.target as Node)) open.value = false
}

async function showAndFocus() {
  open.value = true
  await nextTick()
  importItem.value?.focus()
}

function closeAndFocus() {
  open.value = false
  trigger.value?.focus()
}

async function importProperties() {
  if (!importEnabled.value) return
  const result = await executeEditorCommand('editor.import-note-properties')
  if (!result.ok) error.value = t('当前编辑器无法执行属性导入。', 'Property import is unavailable in the current editor.')
  else { error.value = ''; open.value = false }
}

onMounted(() => window.addEventListener('pointerdown', dismiss))
onBeforeUnmount(() => window.removeEventListener('pointerdown', dismiss))
</script>

<template>
  <div ref="menu" class="titlebar-menu">
    <button ref="trigger" type="button" class="menu-trigger" aria-haspopup="menu" :aria-expanded="open" @click="open ? closeAndFocus() : showAndFocus()" @keydown.down.prevent="showAndFocus">
      {{ t('段落', 'Paragraph') }}
    </button>
    <div v-if="open" class="menu-popover" role="menu" @keydown.esc.stop.prevent="closeAndFocus">
      <button ref="importItem" type="button" role="menuitem" :disabled="!importEnabled" @click="importProperties">
        {{ t('导入为笔记属性…', 'Import as note properties…') }}
      </button>
      <small v-if="!importEnabled">{{ t('请在无冲突的 Markdown 源码笔记中使用', 'Available in a conflict-free Markdown source note') }}</small>
      <small v-if="error" role="alert">{{ error }}</small>
    </div>
  </div>
</template>

<style scoped>
.titlebar-menu { position: relative; flex: 0 0 auto; -webkit-app-region: no-drag; }
.menu-trigger { min-height: 28px; padding: 3px 10px; border: 1px solid transparent; border-radius: var(--radius-sm); color: var(--color-text-secondary); }
.menu-trigger:hover, .menu-trigger[aria-expanded='true'] { border-color: var(--color-border-default); background: var(--color-background-hover); color: var(--color-text-primary); }
.menu-popover { position: absolute; top: calc(100% + 5px); left: 0; z-index: calc(var(--z-titlebar) + 2); display: grid; min-width: 220px; padding: var(--space-xs); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-elevated); box-shadow: var(--shadow-lg); }
.menu-popover button { width: 100%; padding: var(--space-sm) var(--space-md); border-radius: var(--radius-sm); text-align: left; white-space: nowrap; }
.menu-popover button:hover:not(:disabled), .menu-popover button:focus-visible { background: var(--color-accent-soft); color: var(--color-accent-primary); }
.menu-popover button:disabled { opacity: .5; cursor: not-allowed; }
.menu-popover small { padding: var(--space-xs) var(--space-md); color: var(--color-text-tertiary); white-space: normal; }
.menu-popover small[role='alert'] { color: var(--color-error); }
</style>
