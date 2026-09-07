<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { useThemeStore } from '@/stores/theme'
import { executeEditorCommand, getEditorCommandCapabilities, subscribeEditorCommandCapabilities, type EditorCommandId } from '@/services/editorCommandService'
import { calloutMenuCommands, formatMenuSections, paragraphMenuSections, type EditorMenuCommand } from '@/services/editorMenu'
import { t } from '@/i18n'
import { useRouter } from 'vue-router'

type MenuName = 'file' | 'edit' | 'paragraph' | 'format' | 'view' | 'theme' | 'help'
const editor = useEditorStore()
const theme = useThemeStore()
const router = useRouter()
const open = ref<MenuName | null>(null)
const nestedOpen = ref(false)
const bar = ref<HTMLElement | null>(null)
const error = ref('')
const capabilities = ref(new Map<EditorCommandId, boolean>())
const shortcut = computed(() => /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘⌥P' : 'Ctrl+Alt+P')
const menuOrder: readonly MenuName[] = ['file', 'edit', 'paragraph', 'format', 'view', 'theme', 'help']

function enabled(id: EditorCommandId) {
  return capabilities.value.get(id) ?? false
}
function refreshCapabilities() {
  capabilities.value = new Map(getEditorCommandCapabilities().map(item => [item.id, item.enabled]))
}
function toggle(name: MenuName) { open.value = open.value === name ? null : name; nestedOpen.value = false; error.value = '' }
function dismiss(event: PointerEvent) { if (!bar.value?.contains(event.target as Node)) close() }
async function focusFirst(name: MenuName) {
  open.value = name
  nestedOpen.value = false
  await nextTick()
  bar.value?.querySelector<HTMLElement>(`[data-menu="${name}"] [role="menuitem"]:not(:disabled)`)?.focus()
}
function close() { open.value = null; nestedOpen.value = false }
async function command(id: EditorCommandId, params?: unknown) {
  const result = params === undefined ? await executeEditorCommand(id) : await executeEditorCommand(id, params)
  if (result.ok) close()
  else error.value = t('当前编辑器无法执行此命令。', 'The active editor cannot run this command.')
}
function commandItem(item: EditorMenuCommand) {
  const params = item.id === 'editor.callout' && item.params && typeof item.params === 'object'
    ? { ...item.params, body: t('提示内容', 'Callout content') }
    : item.params
  return command(item.id, params)
}
function calloutType(item: EditorMenuCommand) {
  return String((item.params as { type?: string } | undefined)?.type ?? '')
}
async function metadataCommand() {
  const imported = await executeEditorCommand('editor.import-note-properties')
  if (imported.ok) { close(); return }
  await command('editor.metadata.edit')
}
async function save() { await editor.save(); close() }
function setMode(mode: 'source' | 'wysiwyg') { editor.setMode(mode); close() }
function toggleTheme() { theme.toggleTheme(); close() }
function applyTheme(id: string) { theme.applyTheme(id); close() }
function navigate(path: string) { void router.push(path); close() }

async function switchMenu(offset: number, expand = open.value !== null) {
  const current = open.value ? menuOrder.indexOf(open.value) : 0
  const name = menuOrder[(current + offset + menuOrder.length) % menuOrder.length]!
  if (expand) await focusFirst(name)
  else bar.value?.querySelector<HTMLElement>(`[data-menu="${name}"] > .menu-trigger`)?.focus()
}
function menuItems(menu: Element) {
  return [...menu.querySelectorAll<HTMLElement>('[data-menu-item]:not(:disabled)')]
    .filter(item => item.closest('[role="menu"]') === menu)
}
function handleKeys(event: KeyboardEvent) {
  const target = event.target as HTMLElement
  const menu = target.closest('[role="menu"]')
  if (event.key === 'Escape') {
    event.preventDefault()
    if (menu?.classList.contains('submenu-popover')) {
      nestedOpen.value = false
      bar.value?.querySelector<HTMLElement>('.submenu-trigger')?.focus()
    } else {
      const name = open.value
      close()
      if (name) bar.value?.querySelector<HTMLElement>(`[data-menu="${name}"] > .menu-trigger`)?.focus()
    }
    return
  }
  if (!menu && ['ArrowLeft', 'ArrowRight', 'ArrowDown'].includes(event.key)) {
    event.preventDefault()
    if (event.key === 'ArrowDown') void focusFirst((target.closest<HTMLElement>('[data-menu]')?.dataset.menu ?? 'file') as MenuName)
    else void switchMenu(event.key === 'ArrowRight' ? 1 : -1, false)
    return
  }
  if (!menu) return
  if (target.classList.contains('submenu-trigger') && event.key === 'ArrowRight') {
    event.preventDefault(); nestedOpen.value = true
    void nextTick(() => bar.value?.querySelector<HTMLElement>('.submenu-popover [data-menu-item]:not(:disabled)')?.focus())
    return
  }
  if (menu.classList.contains('submenu-popover') && event.key === 'ArrowLeft') {
    event.preventDefault(); nestedOpen.value = false; bar.value?.querySelector<HTMLElement>('.submenu-trigger')?.focus(); return
  }
  if (menu.classList.contains('submenu-popover') && event.key === 'ArrowRight') {
    event.preventDefault(); return
  }
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault(); void switchMenu(event.key === 'ArrowRight' ? 1 : -1); return
  }
  const items = menuItems(menu)
  const index = Math.max(0, items.indexOf(target))
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    items[(index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus()
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault(); items[event.key === 'Home' ? 0 : items.length - 1]?.focus()
  } else if (event.key === 'Tab') close()
}

let unsubscribeCapabilities: (() => void) | undefined
onMounted(() => {
  refreshCapabilities()
  unsubscribeCapabilities = subscribeEditorCommandCapabilities(refreshCapabilities)
  window.addEventListener('pointerdown', dismiss)
})
onBeforeUnmount(() => {
  unsubscribeCapabilities?.()
  window.removeEventListener('pointerdown', dismiss)
})
</script>

<template>
  <nav ref="bar" class="desktop-menu-bar" role="menubar" :aria-label="t('应用菜单', 'Application menu')" @keydown="handleKeys">
    <div class="menu-group" data-menu="file">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'file'" aria-haspopup="menu" @click="toggle('file')" @pointerenter="open && focusFirst('file')">{{ t('文件', 'File') }}</button>
      <div v-if="open === 'file'" class="menu-popover" role="menu" :aria-label="t('文件', 'File')">
        <button data-menu-item role="menuitem" @click="navigate('/')"><span>{{ t('选择知识库…', 'Choose knowledge base…') }}</span></button>
        <span class="menu-separator" role="separator" />
        <button data-menu-item role="menuitem" :disabled="!editor.currentFilePath || ['saving','conflict','external_changed'].includes(editor.saveStatus)" @click="save">
          <span>{{ t('保存', 'Save') }}</span><kbd>Ctrl+S</kbd>
        </button>
      </div>
    </div>
    <div class="menu-group" data-menu="edit">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'edit'" aria-haspopup="menu" @click="toggle('edit')" @pointerenter="open && focusFirst('edit')">{{ t('编辑', 'Edit') }}</button>
      <div v-if="open === 'edit'" class="menu-popover" role="menu" :aria-label="t('编辑', 'Edit')">
        <button data-menu-item role="menuitem" :disabled="!enabled('editor.undo')" @click="command('editor.undo')"><span>{{ t('撤销', 'Undo') }}</span><kbd>Ctrl+Z</kbd></button>
        <button data-menu-item role="menuitem" :disabled="!enabled('editor.redo')" @click="command('editor.redo')"><span>{{ t('重做', 'Redo') }}</span><kbd>Ctrl+Shift+Z</kbd></button>
      </div>
    </div>
    <div class="menu-group" data-menu="paragraph">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'paragraph'" aria-haspopup="menu" @click="toggle('paragraph')" @pointerenter="open && focusFirst('paragraph')">{{ t('段落', 'Paragraph') }}</button>
      <div v-if="open === 'paragraph'" class="menu-popover paragraph-menu" role="menu" :aria-label="t('段落', 'Paragraph')">
        <template v-for="(section, sectionIndex) in paragraphMenuSections" :key="sectionIndex">
          <button v-for="item in section" :key="`${item.id}-${String(item.params ?? '')}`" data-menu-item role="menuitem" :disabled="!enabled(item.id)" @click="commandItem(item)"><span>{{ t(...item.label) }}</span><kbd v-if="item.shortcut">{{ item.shortcut }}</kbd></button>
          <span v-if="sectionIndex < paragraphMenuSections.length - 1" class="menu-separator" role="separator" />
        </template>
        <span class="menu-separator" role="separator" />
        <button class="import-properties" data-menu-item role="menuitem" :disabled="!enabled('editor.import-note-properties')" @click="command('editor.import-note-properties')">
          <span>{{ t('导入为笔记属性…', 'Import as note properties…') }}</span><kbd>{{ shortcut }}</kbd>
        </button>
        <small v-if="!enabled('editor.import-note-properties')">{{ t('请在无冲突的 Markdown 源码笔记中使用', 'Available in a conflict-free Markdown source note') }}</small>
      </div>
    </div>
    <div class="menu-group" data-menu="format">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'format'" aria-haspopup="menu" @click="toggle('format')" @pointerenter="open && focusFirst('format')">{{ t('格式', 'Format') }}</button>
      <div v-if="open === 'format'" class="menu-popover format-menu" role="menu" :aria-label="t('格式', 'Format')">
        <template v-for="item in formatMenuSections[0]" :key="item.id">
          <button data-menu-item role="menuitem" :disabled="!enabled(item.id)" @click="commandItem(item)"><span>{{ t(...item.label) }}</span><kbd v-if="item.shortcut">{{ item.shortcut }}</kbd></button>
        </template>
        <span class="menu-separator" role="separator" />
        <template v-for="item in formatMenuSections[1]" :key="item.id">
          <button data-menu-item role="menuitem" :disabled="!enabled(item.id)" @click="commandItem(item)"><span>{{ t(...item.label) }}</span><kbd v-if="item.shortcut">{{ item.shortcut }}</kbd></button>
        </template>
        <div class="submenu" @pointerleave="nestedOpen = false">
          <button class="submenu-trigger" data-menu-item role="menuitem" aria-haspopup="menu" :aria-expanded="nestedOpen" :disabled="!enabled('editor.callout')" @click="nestedOpen = !nestedOpen" @pointerenter="nestedOpen = true"><span>{{ t('警告框样式', 'Callout styles') }}</span><span><kbd>Ctrl+Alt+C</kbd><span class="submenu-arrow">›</span></span></button>
          <div v-if="nestedOpen" class="submenu-popover" role="menu" :aria-label="t('警告框样式', 'Callout styles')">
            <button v-for="item in calloutMenuCommands" :key="calloutType(item)" data-menu-item role="menuitem" @click="commandItem(item)"><span>{{ t(...item.label) }}</span><code>{{ calloutType(item) }}</code></button>
          </div>
        </div>
        <span class="menu-separator" role="separator" />
        <template v-for="item in formatMenuSections[2]" :key="item.id">
          <button data-menu-item role="menuitem" :disabled="!enabled(item.id)" @click="commandItem(item)"><span>{{ t(...item.label) }}</span><kbd v-if="item.shortcut">{{ item.shortcut }}</kbd></button>
        </template>
        <span class="menu-separator" role="separator" />
        <button class="metadata-command" data-menu-item role="menuitem" :disabled="!enabled('editor.import-note-properties') && !enabled('editor.metadata.edit')" @click="metadataCommand"><span>{{ t('元数据 / YAML Front Matter…', 'Metadata / YAML Front Matter…') }}</span><kbd>{{ shortcut }}</kbd></button>
      </div>
    </div>
    <div class="menu-group" data-menu="view">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'view'" aria-haspopup="menu" @click="toggle('view')" @pointerenter="open && focusFirst('view')">{{ t('视图', 'View') }}</button>
      <div v-if="open === 'view'" class="menu-popover" role="menu" :aria-label="t('视图', 'View')">
        <button data-menu-item role="menuitemradio" :aria-checked="editor.mode === 'wysiwyg'" @click="setMode('wysiwyg')"><span>{{ editor.mode === 'wysiwyg' ? '✓ ' : '' }}{{ t('写作模式', 'Writing mode') }}</span></button>
        <button data-menu-item role="menuitemradio" :aria-checked="editor.mode === 'source'" @click="setMode('source')"><span>{{ editor.mode === 'source' ? '✓ ' : '' }}{{ t('源码模式', 'Source mode') }}</span></button>
        <span class="menu-separator" role="separator" />
        <button data-menu-item role="menuitem" :disabled="!enabled('editor.heading.fold-all')" @click="command('editor.heading.fold-all')"><span>{{ t('全部折叠标题', 'Fold all headings') }}</span></button>
        <button data-menu-item role="menuitem" :disabled="!enabled('editor.heading.unfold-all')" @click="command('editor.heading.unfold-all')"><span>{{ t('全部展开标题', 'Unfold all headings') }}</span></button>
      </div>
    </div>
    <div class="menu-group" data-menu="theme">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'theme'" aria-haspopup="menu" @click="toggle('theme')" @pointerenter="open && focusFirst('theme')">{{ t('主题', 'Theme') }}</button>
      <div v-if="open === 'theme'" class="menu-popover" role="menu" :aria-label="t('主题', 'Theme')">
        <button v-for="item in theme.allThemes" :key="item.theme_id" data-menu-item role="menuitemradio" :aria-checked="theme.currentThemeId === item.theme_id" @click="applyTheme(item.theme_id)"><span>{{ theme.currentThemeId === item.theme_id ? '✓ ' : '' }}{{ item.name }}</span></button>
        <span class="menu-separator" role="separator" />
        <button data-menu-item role="menuitem" @click="toggleTheme"><span>{{ theme.isDark ? t('切换为浅色', 'Switch to light') : t('切换为深色', 'Switch to dark') }}</span></button>
        <button data-menu-item role="menuitem" @click="navigate('/themes')"><span>{{ t('管理主题…', 'Manage themes…') }}</span></button>
      </div>
    </div>
    <div class="menu-group" data-menu="help">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'help'" aria-haspopup="menu" @click="toggle('help')" @pointerenter="open && focusFirst('help')">{{ t('帮助', 'Help') }}</button>
      <div v-if="open === 'help'" class="menu-popover" role="menu" :aria-label="t('帮助', 'Help')">
        <button data-menu-item role="menuitem" @click="navigate('/logs')"><span>{{ t('运行日志', 'Operation logs') }}</span></button>
        <button data-menu-item role="menuitem" @click="navigate('/settings')"><span>{{ t('设置与诊断', 'Settings and diagnostics') }}</span></button>
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
.format-menu { min-width: 330px; }
.menu-popover button { display: flex; align-items: center; justify-content: space-between; gap: var(--space-xl); width: 100%; padding: 7px var(--space-md); border-radius: var(--radius-sm); text-align: left; white-space: nowrap; }
.menu-popover button:hover:not(:disabled), .menu-popover button:focus-visible { background: var(--color-accent-soft); color: var(--color-accent-primary); }
.menu-popover button:disabled { opacity: .45; cursor: not-allowed; }
.menu-popover kbd { color: var(--color-text-tertiary); font: inherit; font-size: var(--font-size-xs); }
.submenu { position: relative; }
.submenu-trigger > span:last-child { display: inline-flex; align-items: center; gap: var(--space-sm); }
.submenu-arrow { color: var(--color-text-tertiary); font-size: 18px; line-height: 1; }
.submenu-popover { position: absolute; bottom: -5px; left: calc(100% + 4px); display: grid; min-width: 210px; max-height: calc(100vh - 96px); overflow: auto; padding: var(--space-xs); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-elevated); box-shadow: var(--shadow-lg); }
.submenu-popover button { padding: 6px var(--space-md); }
.submenu-popover code { color: var(--color-text-tertiary); font-size: 10px; }
.menu-popover { max-height: calc(100vh - 110px); overflow: auto; }
.menu-popover.format-menu { max-height: none; overflow: visible; }
.menu-separator { height: 1px; margin: var(--space-xs); background: var(--color-border-subtle); }
.menu-popover small { padding: var(--space-xs) var(--space-md); color: var(--color-text-tertiary); white-space: normal; }
.menu-error { margin-left: var(--space-md); color: var(--color-error); font-size: var(--font-size-xs); }
</style>
