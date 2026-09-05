<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Link } from '@element-plus/icons-vue'
import { Crepe } from '@milkdown/crepe'
import { codeBlockConfig } from '@milkdown/kit/component/code-block'
import { basicSetup } from 'codemirror'
import { keymap } from '@codemirror/view'
import { indentWithTab } from '@codemirror/commands'
import { shikiEditorTheme, shikiLanguages } from './shikiCodeMirror'
import { installLanguagePickerPopover } from './languagePickerPopover'
import {
  createCodeBlockCommand,
  toggleEmphasisCommand,
  toggleInlineCodeCommand,
  toggleLinkCommand,
  toggleStrongCommand,
  turnIntoTextCommand,
  wrapInBulletListCommand,
  wrapInHeadingCommand,
  wrapInOrderedListCommand,
} from '@milkdown/kit/preset/commonmark'
import { commandsCtx, editorViewCtx } from '@milkdown/kit/core'
import { TextSelection } from '@milkdown/kit/prose/state'
import { callCommand } from '@milkdown/kit/utils'
import AppIcon from '@/components/common/AppIcon.vue'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { useThemeStore } from '@/stores/theme'
import { applyMarkdownFontSize, fontSizeMarkdownPlugin } from './fontSizeMarkdown'
import { t } from '@/i18n'
import '@milkdown/crepe/theme/common/style.css'
import '@milkdown/crepe/theme/frame.css'

const props = defineProps<{ initialContent: string }>()
const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const themeStore = useThemeStore()
const editorRoot = ref<HTMLElement | null>(null)
const loading = ref(true)
const fontSizeInput = ref(16)
let crepe: Crepe | null = null
let disposeLanguagePicker: (() => void) | undefined

function applyProofingPreferences() {
  const editable = editorRoot.value?.querySelector<HTMLElement>('.ProseMirror')
  if (!editable) return
  editable.spellcheck = settingsStore.spellCheck
  editable.setAttribute('spellcheck', String(settingsStore.spellCheck))
  editable.lang = settingsStore.language
}

type ToolbarCommand = 'bold' | 'italic' | 'ordered-list' | 'bullet-list' | 'inline-code' | 'code-block' | 'inline-math' | 'math-block'

function runCommand(command: ToolbarCommand) {
  const editor = crepe?.editor
  if (!editor) return
  // 顶部工具栏复用 Milkdown 命令，因此选区与浮动工具栏共享同一文档事务。
  const actions = {
    bold: callCommand(toggleStrongCommand.key),
    italic: callCommand(toggleEmphasisCommand.key),
    'ordered-list': callCommand(wrapInOrderedListCommand.key),
    'bullet-list': callCommand(wrapInBulletListCommand.key),
    'inline-code': callCommand(toggleInlineCodeCommand.key),
    'code-block': callCommand(createCodeBlockCommand.key, ''),
    'inline-math': callCommand('ToggleLatex'),
    'math-block': callCommand(createCodeBlockCommand.key, 'LaTeX'),
  }
  editor.action(actions[command])
  editorRoot.value?.querySelector<HTMLElement>('.ProseMirror')?.focus()
}

function applyLink() {
  if (!crepe) return
  // TODO(editor): 用受控 Element Plus 对话框替换 prompt，补充 URL 校验和键盘焦点管理。
  const href = window.prompt(t('请输入链接地址', 'Enter link address'), 'https://')?.trim()
  if (!href) return

  crepe.editor.action((ctx) => {
    const view = ctx.get(editorViewCtx)
    const commands = ctx.get(commandsCtx)
    if (view.state.selection.empty) {
      const label = window.prompt(t('请输入链接文字', 'Enter link text'), href)?.trim() || href
      const from = view.state.selection.from
      const transaction = view.state.tr.insertText(label, from)
      transaction.setSelection(TextSelection.create(transaction.doc, from, from + label.length))
      view.dispatch(transaction)
    }
    return commands.call(toggleLinkCommand.key, { href })
  })
  editorRoot.value?.querySelector<HTMLElement>('.ProseMirror')?.focus()
}

function applyHeading(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (!value || !crepe) return
  crepe.editor.action(value === 'paragraph'
    ? callCommand(turnIntoTextCommand.key)
    : callCommand(wrapInHeadingCommand.key, Number(value)))
  editorRoot.value?.querySelector<HTMLElement>('.ProseMirror')?.focus()
  ;(event.target as HTMLSelectElement).value = ''
}

function applyFontSize(event: Event) {
  const size = Number((event.target as HTMLSelectElement).value)
  if (!size || !crepe) return
  fontSizeInput.value = size
  applyFontSizeValue()
  ;(event.target as HTMLSelectElement).value = ''
}

function applyFontSizeValue() {
  if (!crepe) return
  const size = Math.min(96, Math.max(8, Math.round(Number(fontSizeInput.value))))
  if (!Number.isFinite(size)) return
  fontSizeInput.value = size
  applyMarkdownFontSize(crepe.editor, size)
}

onMounted(async () => {
  crepe = new Crepe({
    root: editorRoot.value,
    defaultValue: props.initialContent,
    features: { [Crepe.Feature.TopBar]: false },
    featureConfigs: {
      [Crepe.Feature.Placeholder]: { text: t('开始记录你的想法…', 'Start writing your thoughts…') },
      [Crepe.Feature.CodeMirror]: {
        previewOnlyByDefault: false,
        searchPlaceholder: t('搜索语言', 'Search languages'),
        noResultText: t('没有匹配的语言', 'No matching language'),
        copyText: t('复制', 'Copy'),
      },
      [Crepe.Feature.Latex]: {
        inlineEditConfirm: t('确认', 'Confirm'),
      },
      [Crepe.Feature.LinkTooltip]: {
        editButton: t('编辑', 'Edit'),
        removeButton: t('移除', 'Remove'),
        confirmButton: t('确认', 'Confirm'),
        inputPlaceholder: t('粘贴链接地址…', 'Paste link address…'),
      },
      [Crepe.Feature.Toolbar]: {
        boldLabel: t('加粗', 'Bold'),
        italicLabel: t('斜体', 'Italic'),
        strikethroughLabel: t('删除线', 'Strikethrough'),
        codeLabel: t('行内代码', 'Inline code'),
        latexLabel: t('行内公式', 'Inline formula'),
        linkLabel: t('链接', 'Link'),
      },
      [Crepe.Feature.BlockEdit]: {
        textGroup: {
          label: t('文本', 'Text'),
          text: { label: t('正文', 'Paragraph') },
          h1: { label: t('一级标题', 'Heading 1') },
          h2: { label: t('二级标题', 'Heading 2') },
          h3: { label: t('三级标题', 'Heading 3') },
          h4: { label: t('四级标题', 'Heading 4') },
          h5: { label: t('五级标题', 'Heading 5') },
          h6: { label: t('六级标题', 'Heading 6') },
          quote: { label: t('引用', 'Quote') },
          divider: { label: t('分割线', 'Divider') },
        },
        listGroup: {
          label: t('列表', 'Lists'),
          bulletList: { label: t('无序列表', 'Bullet list') },
          orderedList: { label: t('有序列表', 'Ordered list') },
          taskList: { label: t('任务列表', 'Task list') },
        },
        advancedGroup: {
          label: t('插入', 'Insert'),
          image: { label: t('图片', 'Image') },
          codeBlock: { label: t('代码块', 'Code block') },
          table: { label: t('表格', 'Table') },
          math: { label: t('公式块', 'Formula block') },
        },
      },
    },
  })
  // Crepe's defaultsDeep merges language arrays and theme extension internals.
  // Replace both AFTER feature configuration to avoid default grammar collisions.
  crepe.editor.config(ctx => ctx.update(codeBlockConfig.key, config => ({
    ...config,
    languages: shikiLanguages(themeStore.resolvedCodeBlockTheme),
    extensions: [basicSetup, keymap.of([indentWithTab]), shikiEditorTheme(themeStore.resolvedCodeBlockTheme)],
  })))
  crepe.editor.use(fontSizeMarkdownPlugin)
  crepe.on((listener) => {
    listener.markdownUpdated((_ctx, markdown, previousMarkdown) => {
      // 忽略编辑器初始化/回显事件，防止无内容变化时触发自动保存循环。
      if (markdown === previousMarkdown || markdown === editorStore.content) return
      editorStore.updateContent(markdown)
      editorStore.scheduleAutoSave(settingsStore.autoSaveInterval)
    })
  })
  await crepe.create()
  if (editorRoot.value) disposeLanguagePicker = installLanguagePickerPopover(editorRoot.value)
  applyProofingPreferences()
  loading.value = false
})

watch([() => settingsStore.spellCheck, () => settingsStore.language], applyProofingPreferences)

onBeforeUnmount(() => { disposeLanguagePicker?.(); void crepe?.destroy() })

defineExpose({ getEditor: () => crepe?.editor })
</script>

<template>
  <div class="visual-editor">
    <div class="markdown-toolbar" role="toolbar" :aria-label="t('Markdown 格式工具栏', 'Markdown formatting toolbar')">
      <label class="toolbar-select heading-select" :title="t('设置标题级别', 'Set heading level')">
        <span class="format-glyph heading-glyph">H</span>
        <select :aria-label="t('标题级别', 'Heading level')" @change="applyHeading">
          <option value="" selected>{{ t('标题', 'Heading') }}</option>
          <option value="paragraph">{{ t('正文', 'Paragraph') }}</option>
          <option v-for="level in 6" :key="level" :value="level">H{{ level }}</option>
        </select>
      </label>
      <button type="button" :title="t('加粗 (Ctrl+B)', 'Bold (Ctrl+B)')" :aria-label="t('加粗', 'Bold')" @pointerdown.prevent="runCommand('bold')"><strong class="format-glyph">B</strong></button>
      <button type="button" :title="t('斜体 (Ctrl+I)', 'Italic (Ctrl+I)')" :aria-label="t('斜体', 'Italic')" @pointerdown.prevent="runCommand('italic')"><em class="format-glyph">I</em></button>
      <span class="toolbar-divider" />
      <button type="button" class="list-glyph" :title="t('有序列表', 'Ordered list')" :aria-label="t('有序列表', 'Ordered list')" @pointerdown.prevent="runCommand('ordered-list')"><span class="list-marker">1</span><span class="list-lines">☰</span></button>
      <button type="button" class="list-glyph" :title="t('无序列表', 'Bullet list')" :aria-label="t('无序列表', 'Bullet list')" @pointerdown.prevent="runCommand('bullet-list')"><span class="list-marker">•</span><span class="list-lines">☰</span></button>
      <span class="toolbar-divider" />
      <label class="toolbar-select font-size-select" :title="t('选择预设字号', 'Choose a preset font size')">
        <span class="format-glyph font-size-glyph">A</span>
        <select :aria-label="t('文字字号', 'Font size')" @change="applyFontSize">
          <option value="" selected>{{ t('字号', 'Size') }}</option>
          <option v-for="size in [12, 14, 16, 18, 20, 24, 28, 32]" :key="size" :value="size">{{ size }} px</option>
        </select>
      </label>
      <div class="font-size-input" :title="t('输入字号后按 Enter 或点击应用', 'Enter a font size, then press Enter or Apply')">
        <input v-model.number="fontSizeInput" type="number" min="8" max="96" step="1" :aria-label="t('自定义字号', 'Custom font size')"
          @keydown.enter.prevent="applyFontSizeValue" />
        <span>px</span>
        <button type="button" :aria-label="t('应用自定义字号', 'Apply custom font size')" @pointerdown.prevent="applyFontSizeValue">{{ t('应用', 'Apply') }}</button>
      </div>
      <span class="toolbar-divider" />
      <button type="button" :title="t('行内代码', 'Inline code')" :aria-label="t('行内代码', 'Inline code')" @pointerdown.prevent="runCommand('inline-code')"><code class="code-glyph">&lt;/&gt;</code></button>
      <button type="button" :title="t('代码块', 'Code block')" :aria-label="t('代码块', 'Code block')" @pointerdown.prevent="runCommand('code-block')"><span class="block-glyph">{ }</span></button>
      <button type="button" :title="t('行内公式', 'Inline formula')" :aria-label="t('行内公式', 'Inline formula')" @pointerdown.prevent="runCommand('inline-math')"><span class="math-glyph">ƒx</span></button>
      <button type="button" :title="t('公式块', 'Formula block')" :aria-label="t('公式块', 'Formula block')" @pointerdown.prevent="runCommand('math-block')"><span class="math-glyph">∑</span></button>
      <button type="button" :title="t('插入链接', 'Insert link')" :aria-label="t('插入链接', 'Insert link')" @pointerdown.prevent="applyLink"><AppIcon :icon="Link" :size="17" /></button>
    </div>
    <div v-if="loading" class="editor-loading">{{ t('正在加载编辑器…', 'Loading editor…') }}</div>
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
.list-glyph { grid-template-columns: 8px 14px; column-gap: 2px; font-weight: 700; }
.list-marker { font: 700 12px/1 var(--font-ui-sans); }
.list-lines { overflow: hidden; width: 14px; font-size: 15px; line-height: 1; transform: scaleX(1.2); }
.code-glyph, .block-glyph { padding: 0; background: transparent; color: inherit; font: 700 13px/1 var(--font-editor-mono); }
.math-glyph { font: italic 700 16px/1 Georgia, 'Times New Roman', serif; }
.toolbar-select { display: inline-flex; align-items: center; gap: 4px; min-height: 30px; padding: 3px 5px 3px 8px; border-radius: var(--radius-sm); color: var(--color-text-primary); }
.toolbar-select select { min-width: 58px; border: 0; outline: 0; background: transparent; color: inherit; cursor: pointer; font-size: var(--font-size-sm); }
.font-size-select select { min-width: 62px; }
.font-size-input { display: inline-flex; align-items: center; height: 30px; margin-left: 2px; overflow: hidden; border: 1px solid var(--color-border-default); border-radius: var(--radius-sm); color: var(--color-text-secondary); background: var(--color-background-primary); }
.font-size-input:focus-within { border-color: var(--color-border-focus); box-shadow: 0 0 0 1px var(--color-border-focus); }
.font-size-input input { width: 42px; height: 100%; padding-left: 7px; border: 0; outline: 0; background: transparent; color: var(--color-text-primary); }
.font-size-input span { font-size: var(--font-size-xs); }
.font-size-input button { min-width: auto; min-height: 100%; margin-left: 4px; padding: 3px 7px; border-left: 1px solid var(--color-border-default); border-radius: 0; font-size: var(--font-size-xs); }
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
  --crepe-color-outline: var(--color-markdown-grid);
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
.milkdown-host :deep(.ProseMirror p) { font-weight: 400; }
.milkdown-host :deep(.ProseMirror h1), .milkdown-host :deep(.ProseMirror h2), .milkdown-host :deep(.ProseMirror h3), .milkdown-host :deep(.ProseMirror h4), .milkdown-host :deep(.ProseMirror h5), .milkdown-host :deep(.ProseMirror h6) { font-weight: 700; }
.milkdown-host :deep(.font-size-marker) { display: none; }
.milkdown-host :deep(.milkdown-code-block) { overflow: visible; border: 1px solid var(--color-code-border); border-radius: 6px; background: var(--color-code-background); color: var(--color-code-text); }
.milkdown-host :deep(.language-picker[popover]) { position: fixed !important; inset: auto; left: var(--picker-left) !important; top: var(--picker-top) !important; margin: 0; padding: 0; border: 0; overflow: visible; background: transparent; color: var(--color-text-primary); }
.milkdown-host :deep(.language-picker .language-list) { height: auto; max-height: var(--picker-list-height, 280px); }
.milkdown-host :deep(.language-picker .list-wrapper) { width: min(260px, calc(100vw - 24px)); border: 1px solid var(--color-border-default); background: var(--color-surface-elevated); box-shadow: var(--shadow-md); }
.milkdown-host :deep(.milkdown-code-block .cm-editor),
.milkdown-host :deep(.milkdown-code-block .cm-gutters),
.milkdown-host :deep(.milkdown-code-block .cm-panel) { background: var(--color-code-background); }
.milkdown-host :deep(.milkdown-code-block .cm-content) { caret-color: var(--color-code-text); font-family: var(--font-editor-mono); }
.milkdown-host :deep(.milkdown-code-block .language-button) { color: var(--color-code-muted); }
:global(.milkdown-toolbar) { border: 1px solid var(--color-border-default) !important; background: var(--color-surface-elevated) !important; box-shadow: var(--shadow-md) !important; }
:global(.milkdown-toolbar .toolbar-item svg), :global(.milkdown-toolbar .toolbar-item.active svg) { color: var(--color-text-primary) !important; fill: var(--color-text-primary) !important; opacity: 1 !important; }
:global(.milkdown-toolbar .toolbar-item:hover svg), :global(.milkdown-toolbar .toolbar-item.active svg) { color: var(--color-accent-primary) !important; fill: var(--color-accent-primary) !important; }
.milkdown-host :deep(.milkdown-table-block th),
.milkdown-host :deep(.milkdown-table-block td) { border-color: var(--color-markdown-grid); }
.milkdown-host :deep(.milkdown-table-block th) { background: var(--color-markdown-table-header); font-weight: 700; }
.milkdown-host :deep(.milkdown-list-item-block li .label-wrapper) { color: var(--color-markdown-marker); font-weight: 700; }
.milkdown-host :deep(.milkdown-list-item-block li .label-wrapper svg) { fill: var(--color-markdown-marker); }
.milkdown-host :deep(code) { font-family: var(--font-editor-mono); }
:global([data-theme='dark'] .milkdown-host .milkdown) { color-scheme: dark; }
@media (max-width: 680px) { .toolbar-select select { min-width: 46px; width: 46px; } }
</style>
