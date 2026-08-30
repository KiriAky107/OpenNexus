<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Crepe } from '@milkdown/crepe'
import {
  toggleEmphasisCommand,
  toggleStrongCommand,
  wrapInBulletListCommand,
  wrapInHeadingCommand,
  wrapInOrderedListCommand,
} from '@milkdown/kit/preset/commonmark'
import { callCommand } from '@milkdown/kit/utils'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { highlightCode } from '@/utils/markdown'
import { applyMarkdownFontSize, fontSizeMarkdownPlugin } from './fontSizeMarkdown'
import '@milkdown/crepe/theme/common/style.css'
import '@milkdown/crepe/theme/frame.css'

const props = defineProps<{ initialContent: string }>()
const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const editorRoot = ref<HTMLElement | null>(null)
const loading = ref(true)
let crepe: Crepe | null = null

type ToolbarCommand = 'bold' | 'italic' | 'ordered-list' | 'bullet-list'

function runCommand(command: ToolbarCommand) {
  const editor = crepe?.editor
  if (!editor) return
  const actions = {
    bold: callCommand(toggleStrongCommand.key),
    italic: callCommand(toggleEmphasisCommand.key),
    'ordered-list': callCommand(wrapInOrderedListCommand.key),
    'bullet-list': callCommand(wrapInBulletListCommand.key),
  }
  editor.action(actions[command])
  editorRoot.value?.querySelector<HTMLElement>('.ProseMirror')?.focus()
}

function applyHeading(event: Event) {
  const level = Number((event.target as HTMLSelectElement).value)
  if (!level || !crepe) return
  crepe.editor.action(callCommand(wrapInHeadingCommand.key, level))
  editorRoot.value?.querySelector<HTMLElement>('.ProseMirror')?.focus()
  ;(event.target as HTMLSelectElement).value = ''
}

function applyFontSize(event: Event) {
  const size = Number((event.target as HTMLSelectElement).value)
  if (!size || !crepe) return
  applyMarkdownFontSize(crepe.editor, size)
  ;(event.target as HTMLSelectElement).value = ''
}

onMounted(async () => {
  crepe = new Crepe({
    root: editorRoot.value,
    defaultValue: props.initialContent,
    features: { [Crepe.Feature.TopBar]: false },
    featureConfigs: {
      [Crepe.Feature.Placeholder]: { text: '开始记录你的想法…' },
      [Crepe.Feature.CodeMirror]: {
        previewOnlyByDefault: true,
        previewLabel: 'Shiki 高亮预览',
        previewLoading: '正在高亮…',
        previewToggleText: (previewOnlyMode) => previewOnlyMode ? '编辑代码' : '查看高亮',
        renderPreview: (language, content, applyPreview) => {
          void highlightCode(content, language).then((html) => {
            const template = document.createElement('template')
            template.innerHTML = html
            applyPreview(template.content.firstElementChild as HTMLElement | null)
          })
          return null
        },
      },
    },
  })
  crepe.editor.use(fontSizeMarkdownPlugin)
  crepe.on((listener) => {
    listener.markdownUpdated((_ctx, markdown, previousMarkdown) => {
      if (markdown === previousMarkdown || markdown === editorStore.content) return
      editorStore.updateContent(markdown)
      editorStore.scheduleAutoSave(settingsStore.autoSaveInterval)
    })
  })
  await crepe.create()
  loading.value = false
})

onBeforeUnmount(() => { void crepe?.destroy() })
</script>

<template>
  <div class="visual-editor">
    <div class="markdown-toolbar" role="toolbar" aria-label="Markdown 格式工具栏">
      <label class="toolbar-select heading-select" title="设置标题级别">
        <span class="format-glyph heading-glyph">H</span>
        <select aria-label="标题级别" @change="applyHeading">
          <option value="" selected>标题</option>
          <option v-for="level in 6" :key="level" :value="level">H{{ level }}</option>
        </select>
      </label>
      <button type="button" title="加粗 (Ctrl+B)" aria-label="加粗" @mousedown.prevent @click="runCommand('bold')"><strong class="format-glyph">B</strong></button>
      <button type="button" title="斜体 (Ctrl+I)" aria-label="斜体" @mousedown.prevent @click="runCommand('italic')"><em class="format-glyph">I</em></button>
      <span class="toolbar-divider" />
      <button type="button" class="list-glyph" title="有序列表" aria-label="有序列表" @mousedown.prevent @click="runCommand('ordered-list')">1.</button>
      <button type="button" class="list-glyph" title="无序列表" aria-label="无序列表" @mousedown.prevent @click="runCommand('bullet-list')">•</button>
      <span class="toolbar-divider" />
      <label class="toolbar-select font-size-select" title="修改选中文字的字号">
        <span class="format-glyph font-size-glyph">A</span>
        <select aria-label="文字字号" @change="applyFontSize">
          <option value="" selected>字号</option>
          <option v-for="size in [12, 14, 16, 18, 20, 24, 28, 32]" :key="size" :value="size">{{ size }} px</option>
        </select>
      </label>
    </div>
    <div v-if="loading" class="editor-loading">正在加载编辑器…</div>
    <div ref="editorRoot" class="milkdown-host" :class="{ loading }" />
  </div>
</template>

<style scoped>
.visual-editor { display: flex; flex: 1; min-height: 0; flex-direction: column; background: var(--color-background-primary); }
.markdown-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 2px; min-height: 42px; padding: 5px var(--space-lg); border-bottom: 1px solid var(--color-border-subtle); background: var(--color-surface-primary); }
.markdown-toolbar button { display: inline-grid; place-items: center; min-width: 32px; min-height: 30px; padding: 4px 8px; border-radius: var(--radius-sm); color: var(--color-text-primary); }
.markdown-toolbar button:hover, .toolbar-select:hover { background: var(--color-background-hover); color: var(--color-text-primary); }
.markdown-toolbar button:focus-visible, .toolbar-select:focus-within { outline: 2px solid var(--color-border-focus); outline-offset: 1px; }
.format-glyph { font-family: Georgia, 'Times New Roman', serif; font-size: 17px; line-height: 1; }
.heading-glyph { font-weight: 800; }
.font-size-glyph { font-size: 18px; }
.list-glyph { font-size: 17px; font-weight: 700; }
.toolbar-select { display: inline-flex; align-items: center; gap: 4px; min-height: 30px; padding: 3px 5px 3px 8px; border-radius: var(--radius-sm); color: var(--color-text-primary); }
.toolbar-select select { min-width: 58px; border: 0; outline: 0; background: transparent; color: inherit; cursor: pointer; font-size: var(--font-size-sm); }
.font-size-select select { min-width: 62px; }
.toolbar-divider { width: 1px; height: 20px; margin: 0 var(--space-xs); background: var(--color-border-default); }
.milkdown-host { flex: 1; min-height: 0; overflow: auto; color: var(--color-text-primary); }
.milkdown-host.loading { visibility: hidden; }
.editor-loading { padding: var(--space-xl); color: var(--color-text-tertiary); }
.milkdown-host :deep(.milkdown) {
  min-height: 100%;
  background: transparent;
  color: inherit;
  --crepe-color-background: var(--color-background-primary);
  --crepe-color-on-background: var(--color-text-primary);
  --crepe-color-surface: var(--color-surface-primary);
  --crepe-color-surface-low: var(--color-background-secondary);
  --crepe-color-on-surface: var(--color-text-primary);
  --crepe-color-on-surface-variant: var(--color-text-secondary);
  --crepe-color-outline: var(--color-border-default);
  --crepe-color-primary: var(--color-accent-primary);
  --crepe-color-secondary: var(--color-accent-soft);
  --crepe-color-on-secondary: var(--color-text-primary);
  --crepe-color-inverse: var(--color-text-primary);
  --crepe-color-on-inverse: var(--color-background-primary);
  --crepe-color-inline-code: var(--color-error);
  --crepe-color-error: var(--color-error);
  --crepe-color-hover: var(--color-background-hover);
  --crepe-color-selected: var(--color-accent-soft);
  --crepe-color-inline-area: var(--color-background-tertiary);
  --crepe-font-default: var(--font-editor-sans);
  --crepe-font-code: var(--font-editor-mono);
}
.milkdown-host :deep(.ProseMirror) { box-sizing: border-box; width: min(100%, var(--editor-line-width, 80ch)); min-height: 100%; margin: 0 auto; padding: var(--space-3xl) var(--space-xl); outline: none; font-family: var(--font-editor-sans); font-size: var(--font-editor-size); line-height: var(--font-editor-line-height); caret-color: var(--color-accent-primary); }
.milkdown-host :deep(.ProseMirror-selectednode) { outline-color: var(--color-accent-primary); }
.milkdown-host :deep(.ProseMirror h1), .milkdown-host :deep(.ProseMirror h2), .milkdown-host :deep(.ProseMirror h3), .milkdown-host :deep(.ProseMirror h4), .milkdown-host :deep(.ProseMirror h5), .milkdown-host :deep(.ProseMirror h6) { font-weight: 700; }
.milkdown-host :deep(.font-size-marker) { display: none; }
.milkdown-host :deep(.milkdown-toolbar) { border: 1px solid var(--color-border-default); background: var(--color-surface-elevated); box-shadow: var(--shadow-md); }
.milkdown-host :deep(.milkdown-toolbar .toolbar-item svg), .milkdown-host :deep(.milkdown-toolbar .toolbar-item.active svg) { color: var(--color-text-primary); fill: var(--color-text-primary); stroke: currentColor; opacity: 1; }
.milkdown-host :deep(.milkdown-toolbar .toolbar-item:hover svg) { color: var(--color-accent-primary); fill: var(--color-accent-primary); }
.milkdown-host :deep(code) { font-family: var(--font-editor-mono); }
.milkdown-host :deep(.shiki) { box-sizing: border-box; width: 100%; overflow: auto; padding: var(--space-md); border-radius: var(--radius-md); }
:global([data-theme='dark']) .milkdown-host :deep(.shiki),
:global([data-theme='dark']) .milkdown-host :deep(.shiki span) {
  color: var(--shiki-dark) !important;
  background-color: var(--shiki-dark-bg) !important;
  font-style: var(--shiki-dark-font-style) !important;
  font-weight: var(--shiki-dark-font-weight) !important;
  text-decoration: var(--shiki-dark-text-decoration) !important;
}
:global([data-theme='dark']) .milkdown-host :deep(.milkdown) { color-scheme: dark; }
@media (max-width: 680px) { .toolbar-select select { min-width: 46px; width: 46px; } }
</style>
