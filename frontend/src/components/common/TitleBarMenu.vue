<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useEditorStore } from '@/stores/editor'
import { useWorkspaceStore } from '@/stores/workspace'
import { useThemeStore } from '@/stores/theme'
import { executeEditorCommand, getEditorCommandCapabilities, subscribeEditorCommandCapabilities, type EditorCommandId } from '@/services/editorCommandService'
import { calloutMenuCommands, formatMenuSections, paragraphMenuSections, type EditorMenuCommand } from '@/services/editorMenu'
import * as workspaceService from '@/services/workspaceService'
import ExportDialog from '@/features/editor/ExportDialog.vue'
import ActionDialog from './ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
import { t } from '@/i18n'
import { useRoute, useRouter } from 'vue-router'

type MenuName = 'file' | 'edit' | 'paragraph' | 'format' | 'view' | 'theme' | 'help'
const editor = useEditorStore()
const workspace = useWorkspaceStore()
const theme = useThemeStore()
const router = useRouter()
const route = useRoute()
const { actionDialog, resolveAction, askPrompt } = useActionDialog()
const open = ref<MenuName | null>(null)
const nestedOpen = ref(false)
const exportOpen = ref(false)
const fileBusy = ref(false)
const bar = ref<HTMLElement | null>(null)
const error = ref('')
const capabilities = ref(new Map<EditorCommandId, boolean>())
const shortcut = computed(() => /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘⌥P' : 'Ctrl+Alt+P')
const menuOrder: readonly MenuName[] = ['file', 'edit', 'paragraph', 'format', 'view', 'theme', 'help']
const editorBlocked = computed(() => !editor.currentFilePath || ['saving', 'conflict', 'external_changed'].includes(editor.saveStatus))
const viewDestinations = computed(() => [
  { name: 'workspace', label: t('工作区', 'Workspace') },
  { name: 'search', label: t('搜索', 'Search') },
  { name: 'chat', label: t('AI 对话', 'AI Chat') },
  { name: 'agent', label: t('智能体', 'Agent') },
  { name: 'tasks', label: t('任务', 'Tasks') },
  { name: 'media', label: t('音视频', 'Media') },
])
const extensionDestinations = computed(() => [
  { name: 'skills', label: t('Skill 管理', 'Skill management') },
  { name: 'plugins', label: t('Plugin 管理', 'Plugin management') },
  { name: 'mcp-servers', label: t('MCP 服务器', 'MCP servers') },
])

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
async function save() {
  await editor.save()
  if (['saved', 'idle'].includes(editor.saveStatus)) close()
  else error.value = t('当前笔记尚未安全保存。', 'The current note has not been saved safely.')
}
function setMode(mode: 'source' | 'wysiwyg') { editor.setMode(mode); close() }
function toggleTheme() { theme.toggleTheme(); close() }
function applyTheme(id: string) { theme.applyTheme(id); close() }
function navigate(path: string) { void router.push(path); close() }
function navigateNamed(name: string) { void router.push({ name }); close() }

function message(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason)
}
async function ensureCurrentNoteSaved() {
  if (!editor.currentFilePath) return true
  if (['dirty', 'saving', 'save_failed'].includes(editor.saveStatus)) await editor.save()
  if (['saved', 'idle'].includes(editor.saveStatus)) return true
  error.value = t('请先处理当前笔记的保存或外部修改冲突。', 'Resolve the current note save or external-change conflict first.')
  return false
}
async function runFileAction(action: () => Promise<void>) {
  if (fileBusy.value) return
  close()
  error.value = ''
  fileBusy.value = true
  try { await action() }
  catch (reason) { error.value = message(reason) }
  finally { fileBusy.value = false }
}
async function createWorkspaceItem(type: 'file' | 'folder') {
  close()
  const rawName = (await askPrompt(type === 'file' ? t('笔记名称', 'Note name') : t('文件夹名称', 'Folder name')))?.trim()
  if (!rawName) return
  if (/[\\/]/.test(rawName) || ['.', '..'].includes(rawName)) {
    error.value = t('名称不能包含路径分隔符。', 'Names cannot contain path separators.')
    return
  }
  await runFileAction(async () => {
    if (!(await ensureCurrentNoteSaved())) return
    if (type === 'folder') {
      const folder = await workspaceService.createFolder('/', rawName)
      workspace.addFileToTree('/', folder)
      return
    }
    const title = rawName.replace(/\.md$/i, '')
    const file = await workspaceService.createFile('/', rawName, `# ${title}\n\n`)
    workspace.addFileToTree('/', file)
    await editor.loadFile(file.path)
    workspace.openFile(file.path)
    await router.push('/workspace')
  })
}
function openExport() { close(); exportOpen.value = true }
function downloadMarkdown() {
  if (!editor.currentFilePath) return
  const url = URL.createObjectURL(new Blob([editor.content], { type: 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = editor.currentFilePath.split('/').at(-1) ?? 'note.md'
  document.body.append(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  close()
}
async function chooseVault() {
  await runFileAction(async () => {
    if (!(await ensureCurrentNoteSaved())) return
    await workspace.openVault('')
    editor.closeFile()
    await router.push('/workspace')
  })
}
async function refreshWorkspace() {
  await runFileAction(async () => { await workspace.refreshFileTree() })
}
async function closeCurrentNote() {
  await runFileAction(async () => {
    if (!(await ensureCurrentNoteSaved())) return
    const path = editor.currentFilePath
    if (!path) return
    workspace.closeFile(path)
    const next = workspace.activeFilePath
    editor.closeFile()
    if (next) await editor.loadFile(next)
  })
}

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
  <ExportDialog v-if="exportOpen" @close="exportOpen = false" />
  <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
  <nav ref="bar" class="desktop-menu-bar" role="menubar" :aria-label="t('应用菜单', 'Application menu')" @keydown="handleKeys">
    <div class="menu-group" role="none" data-menu="file">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'file'" aria-haspopup="menu" @click="toggle('file')" @pointerenter="open && focusFirst('file')">{{ t('文件', 'File') }}</button>
      <div v-if="open === 'file'" class="menu-popover file-menu" role="menu" :aria-label="t('文件', 'File')">
        <button data-menu-item role="menuitem" :disabled="!workspace.hasVault || fileBusy" @click="createWorkspaceItem('file')"><span>{{ t('新建笔记…', 'New note…') }}</span></button>
        <button data-menu-item role="menuitem" :disabled="!workspace.hasVault || fileBusy" @click="createWorkspaceItem('folder')"><span>{{ t('新建文件夹…', 'New folder…') }}</span></button>
        <span class="menu-separator" role="separator" />
        <button data-menu-item role="menuitem" :disabled="fileBusy" @click="chooseVault"><span>{{ t('打开其他知识库…', 'Open another knowledge base…') }}</span></button>
        <button data-menu-item role="menuitem" :disabled="!workspace.hasVault || fileBusy" @click="navigate('/workspace')"><span>{{ t('返回工作区', 'Return to workspace') }}</span></button>
        <button data-menu-item role="menuitem" :disabled="!workspace.hasVault || fileBusy" @click="refreshWorkspace"><span>{{ t('刷新文件树', 'Refresh file tree') }}</span></button>
        <small v-if="workspace.hasVault" class="menu-context">{{ workspace.vaultName }} · {{ workspace.vaultPath }}</small>
        <span class="menu-separator" role="separator" />
        <button data-menu-item role="menuitem" :disabled="editorBlocked || fileBusy" @click="save">
          <span>{{ t('保存', 'Save') }}</span><kbd>Ctrl+S</kbd>
        </button>
        <button class="export-command" data-menu-item role="menuitem" :disabled="!editor.currentFilePath || !editor.content.trim() || fileBusy" @click="openExport"><span>{{ t('导出…', 'Export…') }}</span></button>
        <button data-menu-item role="menuitem" :disabled="!editor.currentFilePath || fileBusy" @click="downloadMarkdown"><span>{{ t('下载 Markdown 副本', 'Download Markdown copy') }}</span></button>
        <span class="menu-separator" role="separator" />
        <button data-menu-item role="menuitem" :disabled="!editor.currentFilePath || fileBusy" @click="closeCurrentNote"><span>{{ t('关闭当前笔记', 'Close current note') }}</span></button>
      </div>
    </div>
    <div class="menu-group" role="none" data-menu="edit">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'edit'" aria-haspopup="menu" @click="toggle('edit')" @pointerenter="open && focusFirst('edit')">{{ t('编辑', 'Edit') }}</button>
      <div v-if="open === 'edit'" class="menu-popover" role="menu" :aria-label="t('编辑', 'Edit')">
        <button data-menu-item role="menuitem" :disabled="!enabled('editor.undo')" @click="command('editor.undo')"><span>{{ t('撤销', 'Undo') }}</span><kbd>Ctrl+Z</kbd></button>
        <button data-menu-item role="menuitem" :disabled="!enabled('editor.redo')" @click="command('editor.redo')"><span>{{ t('重做', 'Redo') }}</span><kbd>Ctrl+Shift+Z</kbd></button>
        <span class="menu-separator" role="separator" />
        <button data-menu-item role="menuitem" :disabled="!workspace.hasVault" @click="navigate('/search')"><span>{{ t('在知识库中搜索…', 'Search knowledge base…') }}</span></button>
      </div>
    </div>
    <div class="menu-group" role="none" data-menu="paragraph">
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
    <div class="menu-group" role="none" data-menu="format">
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
    <div class="menu-group" role="none" data-menu="view">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'view'" aria-haspopup="menu" @click="toggle('view')" @pointerenter="open && focusFirst('view')">{{ t('视图', 'View') }}</button>
      <div v-if="open === 'view'" class="menu-popover view-menu" role="menu" :aria-label="t('视图', 'View')">
        <button v-for="item in viewDestinations" :key="item.name" data-menu-item role="menuitemradio" :aria-checked="route.name === item.name" :disabled="!workspace.hasVault" @click="navigateNamed(item.name)"><span>{{ route.name === item.name ? '✓ ' : '' }}{{ item.label }}</span></button>
        <span class="menu-separator" role="separator" />
        <button v-for="item in extensionDestinations" :key="item.name" data-menu-item role="menuitemradio" :aria-checked="route.name === item.name" :disabled="!workspace.hasVault" @click="navigateNamed(item.name)"><span>{{ route.name === item.name ? '✓ ' : '' }}{{ item.label }}</span></button>
        <span class="menu-separator" role="separator" />
        <button data-menu-item role="menuitemradio" :aria-checked="editor.mode === 'wysiwyg'" :disabled="!editor.currentFilePath" @click="setMode('wysiwyg')"><span>{{ editor.mode === 'wysiwyg' && editor.currentFilePath ? '✓ ' : '' }}{{ t('写作模式', 'Writing mode') }}</span></button>
        <button data-menu-item role="menuitemradio" :aria-checked="editor.mode === 'source'" :disabled="!editor.currentFilePath" @click="setMode('source')"><span>{{ editor.mode === 'source' && editor.currentFilePath ? '✓ ' : '' }}{{ t('源码模式', 'Source mode') }}</span></button>
        <span class="menu-separator" role="separator" />
        <button data-menu-item role="menuitem" :disabled="!enabled('editor.heading.fold-all')" @click="command('editor.heading.fold-all')"><span>{{ t('全部折叠标题', 'Fold all headings') }}</span></button>
        <button data-menu-item role="menuitem" :disabled="!enabled('editor.heading.unfold-all')" @click="command('editor.heading.unfold-all')"><span>{{ t('全部展开标题', 'Unfold all headings') }}</span></button>
      </div>
    </div>
    <div class="menu-group" role="none" data-menu="theme">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'theme'" aria-haspopup="menu" @click="toggle('theme')" @pointerenter="open && focusFirst('theme')">{{ t('主题', 'Theme') }}</button>
      <div v-if="open === 'theme'" class="menu-popover" role="menu" :aria-label="t('主题', 'Theme')">
        <button v-for="item in theme.allThemes" :key="item.theme_id" data-menu-item role="menuitemradio" :aria-checked="theme.currentThemeId === item.theme_id" @click="applyTheme(item.theme_id)"><span>{{ theme.currentThemeId === item.theme_id ? '✓ ' : '' }}{{ item.name }}</span></button>
        <span class="menu-separator" role="separator" />
        <button data-menu-item role="menuitem" @click="toggleTheme"><span>{{ theme.isDark ? t('切换为浅色', 'Switch to light') : t('切换为深色', 'Switch to dark') }}</span></button>
        <button data-menu-item role="menuitem" @click="navigate('/themes')"><span>{{ t('管理主题…', 'Manage themes…') }}</span></button>
      </div>
    </div>
    <div class="menu-group" role="none" data-menu="help">
      <button class="menu-trigger" role="menuitem" :aria-expanded="open === 'help'" aria-haspopup="menu" @click="toggle('help')" @pointerenter="open && focusFirst('help')">{{ t('帮助', 'Help') }}</button>
      <div v-if="open === 'help'" class="menu-popover" role="menu" :aria-label="t('帮助', 'Help')">
        <button data-menu-item role="menuitem" @click="navigate('/logs')"><span>{{ t('运行日志', 'Operation logs') }}</span></button>
        <button data-menu-item role="menuitem" @click="navigate('/benchmarks')"><span>{{ t('Benchmark 评测', 'Benchmarks') }}</span></button>
        <button data-menu-item role="menuitem" @click="navigate('/community')"><span>{{ t('社区目录', 'Community catalog') }}</span></button>
        <span class="menu-separator" role="separator" />
        <button data-menu-item role="menuitem" :disabled="!workspace.hasVault" @click="navigate('/settings')"><span>{{ t('设置与诊断…', 'Settings and diagnostics…') }}</span></button>
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
.file-menu { min-width: 320px; }
.paragraph-menu { min-width: 270px; }
.format-menu { min-width: 330px; }
.view-menu { min-width: 250px; }
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
.menu-context { display: block; max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap !important; }
.menu-error { margin-left: var(--space-md); color: var(--color-error); font-size: var(--font-size-xs); }
</style>
