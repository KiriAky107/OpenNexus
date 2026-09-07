<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { useThemeStore } from '@/stores/theme'
import { executeEditorCommand, getEditorCommandCapabilities, type EditorCommandId } from '@/services/editorCommandService'
import { t } from '@/i18n'

type MenuName = 'file' | 'edit' | 'paragraph' | 'view'
const editor = useEditorStore()
const theme = useThemeStore()
const open = ref<MenuName | null>(null)
const bar = ref<HTMLElement | null>(null)
const error = ref('')
const shortcut = computed(() => /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘⌥P' : 'Ctrl+Alt+P')

function enabled(id: EditorCommandId) {
  return getEditorCommandCapabilities().find(item => item.id === id)?.enabled ?? false
}
function toggle(name: MenuName) { open.value = open.value === name ? null : name; error.value = '' }
function dismiss(event: PointerEvent) { if (!bar.value?.contains(event.target as Node)) open.value = null }
async function focusFirst(name: MenuName) {
  open.value = name
  await nextTick()
  bar.value?.querySelector<HTMLElement>(`[data-menu="${name}"] [role="menuitem"]:not(:disabled)`)?.focus()
}
function close() { open.value = null }
async function command(id: EditorCommandId, params?: unknown) {
  const result = params === undefined ? await executeEditorCommand(id) : await executeEditorCommand(id, params)
  if (result.ok) close()
  else error.value = t('当前编辑器无法执行此命令。', 'The active editor cannot run this command.')
}
async function save() { await editor.save(); close() }
function setMode(mode: 'source' | 'wysiwyg') { editor.setMode(mode); close() }
function toggleTheme() { theme.toggleTheme(); close() }

onMounted(() => window.addEventListener('pointerdown', dismiss))
onBeforeUnmount(() => window.removeEventListener('pointerdown', dismiss))
</script>

<template>
  <nav ref="bar" class="desktop-menu-bar" :aria-label="t('应用菜单', 'Application menu')" @keydown.esc.stop.prevent="close">
    <div class="menu-group" data-menu="file">
      <button class="menu-trigger" :aria-expanded="open === 'file'" aria-haspopup="menu" @click="toggle('file')" @keydown.down.prevent="focusFirst('file')">{{ t('文件', 'File') }}</button>
      <div v-if="open === 'file'" class="menu-popover" role="menu">
        <button role="menuitem" :disabled="!editor.currentFilePath || ['saving','conflict','external_changed'].includes(editor.saveStatus)" @click="save">
          <span>{{ t('保存', 'Save') }}</span><kbd>Ctrl+S</kbd>
        </button>
      </div>
    </div>
    <div class="menu-group" data-menu="edit">
      <button class="menu-trigger" :aria-expanded="open === 'edit'" aria-haspopup="menu" @click="toggle('edit')" @keydown.down.prevent="focusFirst('edit')">{{ t('编辑', 'Edit') }}</button>
      <div v-if="open === 'edit'" class="menu-popover" role="menu">
        <button role="menuitem" :disabled="!enabled('editor.undo')" @click="command('editor.undo')"><span>{{ t('撤销', 'Undo') }}</span><kbd>Ctrl+Z</kbd></button>
        <button role="menuitem" :disabled="!enabled('editor.redo')" @click="command('editor.redo')"><span>{{ t('重做', 'Redo') }}</span><kbd>Ctrl+Shift+Z</kbd></button>
      </div>
    </div>
    <div class="menu-group" data-menu="paragraph">
      <button class="menu-trigger" :aria-expanded="open === 'paragraph'" aria-haspopup="menu" @click="toggle('paragraph')" @keydown.down.prevent="focusFirst('paragraph')">{{ t('段落', 'Paragraph') }}</button>
      <div v-if="open === 'paragraph'" class="menu-popover paragraph-menu" role="menu">
        <button role="menuitem" :disabled="!enabled('editor.paragraph')" @click="command('editor.paragraph')"><span>{{ t('正文', 'Body text') }}</span></button>
        <button v-for="level in 3" :key="level" role="menuitem" :disabled="!enabled('editor.heading')" @click="command('editor.heading', level)"><span>{{ t(`标题 ${level}`, `Heading ${level}`) }}</span></button>
        <span class="menu-separator" role="separator" />
        <button role="menuitem" :disabled="!enabled('editor.bullet-list')" @click="command('editor.bullet-list')"><span>{{ t('无序列表', 'Bullet list') }}</span></button>
        <button role="menuitem" :disabled="!enabled('editor.ordered-list')" @click="command('editor.ordered-list')"><span>{{ t('有序列表', 'Ordered list') }}</span></button>
        <button role="menuitem" :disabled="!enabled('editor.task-list')" @click="command('editor.task-list')"><span>{{ t('任务列表', 'Task list') }}</span></button>
        <button role="menuitem" :disabled="!enabled('editor.blockquote')" @click="command('editor.blockquote')"><span>{{ t('引用', 'Blockquote') }}</span></button>
        <button role="menuitem" :disabled="!enabled('editor.code-block')" @click="command('editor.code-block')"><span>{{ t('代码块', 'Code block') }}</span></button>
        <span class="menu-separator" role="separator" />
        <button class="import-properties" role="menuitem" :disabled="!enabled('editor.import-note-properties')" @click="command('editor.import-note-properties')">
          <span>{{ t('导入为笔记属性…', 'Import as note properties…') }}</span><kbd>{{ shortcut }}</kbd>
        </button>
        <small v-if="!enabled('editor.import-note-properties')">{{ t('请在无冲突的 Markdown 源码笔记中使用', 'Available in a conflict-free Markdown source note') }}</small>
      </div>
    </div>
    <div class="menu-group" data-menu="view">
      <button class="menu-trigger" :aria-expanded="open === 'view'" aria-haspopup="menu" @click="toggle('view')" @keydown.down.prevent="focusFirst('view')">{{ t('视图', 'View') }}</button>
      <div v-if="open === 'view'" class="menu-popover" role="menu">
        <button role="menuitemradio" :aria-checked="editor.mode === 'wysiwyg'" @click="setMode('wysiwyg')"><span>✓ {{ t('写作模式', 'Writing mode') }}</span></button>
        <button role="menuitemradio" :aria-checked="editor.mode === 'source'" @click="setMode('source')"><span>✓ {{ t('源码模式', 'Source mode') }}</span></button>
        <span class="menu-separator" role="separator" />
        <button role="menuitem" @click="toggleTheme"><span>{{ theme.isDark ? t('浅色主题', 'Light theme') : t('深色主题', 'Dark theme') }}</span></button>
      </div>
    </div>
    <span v-if="error" class="menu-error" role="alert">{{ error }}</span>
  </nav>
</template>

<style scoped>
.desktop-menu-bar { position: relative; z-index: calc(var(--z-titlebar) - 1); display: flex; align-items: center; min-height: 32px; padding: 0 var(--space-md); border-bottom: 1px solid var(--color-border-default); background: var(--color-surface-primary); flex-shrink: 0; user-select: none; -webkit-app-region: no-drag; }
.menu-group { position: relative; height: 100%; display: flex; align-items: center; }
.menu-trigger { min-width: 52px; height: 28px; padding: 2px 12px; border-radius: var(--radius-sm); color: var(--color-text-secondary); }
.menu-trigger:hover, .menu-trigger[aria-expanded='true'] { background: var(--color-background-hover); color: var(--color-text-primary); }
.menu-popover { position: absolute; top: calc(100% + 2px); left: 0; z-index: calc(var(--z-titlebar) + 2); display: grid; min-width: 230px; padding: var(--space-xs); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-elevated); box-shadow: var(--shadow-lg); }
.paragraph-menu { min-width: 270px; }
.menu-popover button { display: flex; align-items: center; justify-content: space-between; gap: var(--space-xl); width: 100%; padding: 7px var(--space-md); border-radius: var(--radius-sm); text-align: left; white-space: nowrap; }
.menu-popover button:hover:not(:disabled), .menu-popover button:focus-visible { background: var(--color-accent-soft); color: var(--color-accent-primary); }
.menu-popover button:disabled { opacity: .45; cursor: not-allowed; }
.menu-popover kbd { color: var(--color-text-tertiary); font: inherit; font-size: var(--font-size-xs); }
.menu-separator { height: 1px; margin: var(--space-xs); background: var(--color-border-subtle); }
.menu-popover small { padding: var(--space-xs) var(--space-md); color: var(--color-text-tertiary); white-space: normal; }
.menu-error { margin-left: var(--space-md); color: var(--color-error); font-size: var(--font-size-xs); }
</style>
