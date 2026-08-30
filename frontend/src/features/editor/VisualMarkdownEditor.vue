<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { CollectionTag, Edit, EditPen, List, Memo } from '@element-plus/icons-vue'
import { Crepe } from '@milkdown/crepe'
import {
  toggleEmphasisCommand,
  toggleStrongCommand,
  wrapInBulletListCommand,
  wrapInHeadingCommand,
  wrapInOrderedListCommand,
} from '@milkdown/kit/preset/commonmark'
import { callCommand } from '@milkdown/kit/utils'
import AppIcon from '@/components/common/AppIcon.vue'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { highlightCode } from '@/utils/markdown'
import '@milkdown/crepe/theme/common/style.css'
import '@milkdown/crepe/theme/frame.css'

const props = defineProps<{ initialContent: string }>()
const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const editorRoot = ref<HTMLElement | null>(null)
const loading = ref(true)
let crepe: Crepe | null = null

type ToolbarCommand = 'heading' | 'bold' | 'italic' | 'ordered-list' | 'bullet-list'

function runCommand(command: ToolbarCommand) {
  const editor = crepe?.editor
  if (!editor) return
  const actions = {
    heading: callCommand(wrapInHeadingCommand.key, 2),
    bold: callCommand(toggleStrongCommand.key),
    italic: callCommand(toggleEmphasisCommand.key),
    'ordered-list': callCommand(wrapInOrderedListCommand.key),
    'bullet-list': callCommand(wrapInBulletListCommand.key),
  }
  editor.action(actions[command])
  editorRoot.value?.querySelector<HTMLElement>('.ProseMirror')?.focus()
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
      <button type="button" title="二级标题" @click="runCommand('heading')"><AppIcon :icon="CollectionTag" /><span>标题</span></button>
      <button type="button" title="加粗 (Ctrl+B)" @click="runCommand('bold')"><AppIcon :icon="Edit" /><span>加粗</span></button>
      <button type="button" title="斜体 (Ctrl+I)" @click="runCommand('italic')"><AppIcon :icon="EditPen" /><span>斜体</span></button>
      <span class="toolbar-divider" />
      <button type="button" title="有序列表" @click="runCommand('ordered-list')"><AppIcon :icon="Memo" /><span>有序列表</span></button>
      <button type="button" title="无序列表" @click="runCommand('bullet-list')"><AppIcon :icon="List" /><span>无序列表</span></button>
    </div>
    <div v-if="loading" class="editor-loading">正在加载编辑器…</div>
    <div ref="editorRoot" class="milkdown-host" :class="{ loading }" />
  </div>
</template>

<style scoped>
.visual-editor { display: flex; flex: 1; min-height: 0; flex-direction: column; background: var(--color-background-primary); }
.markdown-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 2px; min-height: 42px; padding: 5px var(--space-lg); border-bottom: 1px solid var(--color-border-subtle); background: var(--color-surface-primary); }
.markdown-toolbar button { display: inline-flex; align-items: center; gap: 5px; min-height: 30px; padding: 4px 8px; border-radius: var(--radius-sm); color: var(--color-text-secondary); }
.markdown-toolbar button:hover { background: var(--color-background-hover); color: var(--color-text-primary); }
.markdown-toolbar button:focus-visible { outline: 2px solid var(--color-border-focus); outline-offset: 1px; }
.markdown-toolbar button span { font-size: var(--font-size-xs); }
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
@media (max-width: 680px) { .markdown-toolbar button span { display: none; } }
</style>
