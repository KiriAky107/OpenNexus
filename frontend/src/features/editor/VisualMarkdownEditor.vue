<script setup lang="ts">
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
const { actionDialog, resolveAction, askPrompt } = useActionDialog()
import DiagramInteractions from '@/components/common/DiagramInteractions.vue'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Crepe } from '@milkdown/crepe'
import { codeBlockConfig } from '@milkdown/kit/component/code-block'
import { basicSetup } from 'codemirror'
import { keymap, EditorView as CodeEditorView } from '@codemirror/view'
import { indentUnit } from '@codemirror/language'
import { EditorState as CodeEditorState } from '@codemirror/state'
import { indentWithTab } from '@codemirror/commands'
import { shikiEditorTheme, shikiLanguages, renderCodeLanguage } from './shikiCodeMirror'
import './language-icons.css'
import { installLanguagePickerPopover } from './languagePickerPopover'
import { installCodeBlockLabels } from './codeBlockLabels'
import { installLinkNavigation } from './linkNavigation'
import { navigateMarkdownHref } from '@/services/markdownLinkService'
import { createMermaidPreview } from './mermaidPreview'
import { splitNoteMetadata, updateMetadataTags } from './noteMetadata'
import { getMarkdown, $remark, $prose } from '@milkdown/kit/utils'
import {
  createCodeBlockCommand,
  toggleEmphasisCommand,
  toggleInlineCodeCommand,
  toggleLinkCommand,
  toggleStrongCommand,
  turnIntoTextCommand,
  wrapInBulletListCommand,
  wrapInBlockquoteCommand,
  wrapInHeadingCommand,
  wrapInOrderedListCommand,
} from '@milkdown/kit/preset/commonmark'
import { toggleStrikethroughCommand } from '@milkdown/kit/preset/gfm'
import { commandsCtx, editorViewCtx, parserCtx, remarkStringifyOptionsCtx } from '@milkdown/kit/core'
import { Slice } from '@milkdown/kit/prose/model'
import { executeEditorCommand, registerEditorCommands, type CommandHandler, type EditorCommandId } from '@/services/editorCommandService'
import { undo, redo, undoDepth, redoDepth } from '@milkdown/kit/prose/history'
import { TextSelection, Plugin } from '@milkdown/kit/prose/state'
import { callCommand } from '@milkdown/kit/utils'
import ControlIcon from '@/components/common/ControlIcon.vue'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { useThemeStore } from '@/stores/theme'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import { applyMarkdownFontSize, fontSizeMarkdownPlugin } from './fontSizeMarkdown'
import { inlineCodeInputPlugin } from './inlineCodeInput'
import { calloutPlugin, configureCalloutSerialization } from './calloutPlugin'
import { calloutTypes } from '@/utils/callouts'
import { headingFoldingPlugin, headingFoldTransaction, headingFoldKey, headingSections } from './headingFolding'
import { useHeadingAppearanceStore } from '@/stores/headingAppearance'
import { useMarkdownPreferencesStore } from '@/stores/markdownPreferences'
import { t } from '@/i18n'
import { loadWorkspaceImage, resolveWorkspaceAssetPath, storeWorkspaceImage, type WorkspaceAssetSource } from '@/services/workspaceService'
import '@milkdown/crepe/theme/common/style.css'
import '@milkdown/crepe/theme/frame.css'

const props = defineProps<{ initialContent: string }>()
const headingAppearance = useHeadingAppearanceStore()
const markdownPreferences = { ...useMarkdownPreferencesStore().normalized }
const metadata = ref(splitNoteMetadata(props.initialContent))
const tagDraft = ref('')
function setTags(tags: string[]) {
  if (!metadata.value || !crepe) return
  const prefix = updateMetadataTags(metadata.value, tags)
  const body = crepe.editor.action(getMarkdown())
  metadata.value = splitNoteMetadata(prefix + body)
  editorStore.updateContent(prefix + body)
  editorStore.scheduleAutoSave(settingsStore.autoSaveInterval)
}
function addTags() {
  const tags = tagDraft.value.split(/[,，]/).map(tag => tag.trim()).filter(tag => tag && !/[\r\n"\\]/.test(tag))
  if (!tags.length || !metadata.value) return
  setTags([...metadata.value.tags, ...tags])
  tagDraft.value = ''
}
const editorStore = useEditorStore()
const settingsStore = useSettingsStore()
const themeStore = useThemeStore()
const layout = useLayoutPreferencesStore()
const canUndo = ref(false)
const canRedo = ref(false)
const commandError = ref('')
async function toolbarCommand(id: EditorCommandId) {
  const result = await executeEditorCommand(id)
  commandError.value = result.ok ? '' : t('当前无法执行此操作。', 'This action is not available right now.')
  if (result.ok) editorRoot.value?.querySelector<HTMLElement>('.ProseMirror')?.focus()
}
function hideToolbar() {
  layout.editorToolbarVisible = false
  void nextTick(() => editorRoot.value?.closest('.workspace-view')?.querySelector<HTMLButtonElement>('.toolbar-toggle')?.focus())
}
const editorRoot = ref<HTMLElement | null>(null)
const loading = ref(true)
const allHeadingsFolded = ref(false)
const hasFoldableHeadings = ref(false)
const fontSizeInput = ref(16)
const imageInput = ref<HTMLInputElement | null>(null)
const imageError = ref('')
let crepe: Crepe | null = null
let disposeLanguagePicker: (() => void) | undefined
let disposeCodeLabels: (() => void) | undefined
let disposeLinkNavigation: (() => void) | undefined
let disposeCommands: (() => void) | undefined
let disposed = false
const imageUrls = new Set<string>()

function insertMarkdown(source: string, position?: number) {
  crepe?.editor.action(ctx => {
    const doc = ctx.get(parserCtx)(source)
    if (!doc) throw new Error('Invalid Markdown')
    const view = ctx.get(editorViewCtx)
    if (position !== undefined) view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, Math.min(position, view.state.doc.content.size))))
    view.dispatch(view.state.tr.replaceSelection(new Slice(doc.content, 0, 0)).scrollIntoView())
    view.focus()
  })
}

function imageFiles(list: FileList | null): File[] {
  return [...(list ?? [])].filter(file => file.type.startsWith('image/'))
}

async function insertImages(files: File[], source: WorkspaceAssetSource, position?: number) {
  if (!crepe || !editorStore.currentFilePath || !files.length) return
  const target = crepe, targetPath = editorStore.currentFilePath
  const document = target.editor.action(ctx => ctx.get(editorViewCtx).state.doc)
  imageError.value = ''
  try {
    const assets = []
    for (const file of files) assets.push(await storeWorkspaceImage(file, source, targetPath, editorStore.currentNoteId))
    if (crepe !== target || editorStore.currentFilePath !== targetPath) return
    const current = target.editor.action(ctx => ctx.get(editorViewCtx).state.doc)
    insertMarkdown(assets.map(asset => `![${asset.original_name.replace(/[\]\\]/g, '\\$&')}](${asset.reference})`).join('\n\n'), current.eq(document) ? position : undefined)
  } catch (reason) {
    imageError.value = reason instanceof Error ? reason.message : String(reason)
  }
}

function chooseImages() { imageInput.value?.click() }
function selectedImages(event: Event) {
  const input = event.target as HTMLInputElement
  void insertImages(imageFiles(input.files), 'upload')
  input.value = ''
}

function workspaceImageNodeView(node: { type: unknown; attrs: Record<string, unknown> }) {
  const notePath = editorStore.currentFilePath
  const dom = document.createElement('img')
  let source = '', objectUrl = '', generation = 0
  const apply = (next: typeof node) => {
    const nextSource = String(next.attrs.src ?? '')
    dom.alt = String(next.attrs.alt ?? '')
    if (next.attrs.title) dom.title = String(next.attrs.title)
    else dom.removeAttribute('title')
    if (nextSource === source) return
    source = nextSource
    const current = ++generation
    if (objectUrl) { URL.revokeObjectURL(objectUrl); imageUrls.delete(objectUrl); objectUrl = '' }
    const path = notePath && resolveWorkspaceAssetPath(notePath, nextSource)
    if (!path) { dom.src = nextSource; return }
    dom.dataset.workspaceAsset = path
    void loadWorkspaceImage(path, notePath, editorStore.currentNoteId).then(blob => {
      const url = URL.createObjectURL(blob)
      if (disposed || current !== generation) { URL.revokeObjectURL(url); return }
      objectUrl = url; imageUrls.add(url); dom.src = url
    }).catch(() => {
      if (current === generation) dom.dataset.workspaceAssetError = 'true'
    })
  }
  apply(node)
  return {
    dom,
    update(next: typeof node) {
      if (next.type !== node.type) return false
      node = next; apply(next); return true
    },
    destroy() {
      generation++
      if (objectUrl) { URL.revokeObjectURL(objectUrl); imageUrls.delete(objectUrl) }
    }
  }
}

function insertCallout(event: Event) {
  const select = event.target as HTMLSelectElement
  if (select.value) insertMarkdown(`> [!${select.value.toUpperCase()}]\n> ${t('提示内容', 'Callout content')}`)
  select.value = ''
}

function installCommands() {
  const targetPath = editorStore.currentFilePath
  const handlers: Partial<Record<EditorCommandId, CommandHandler>> = {}
  for (const [id, action] of [['editor.undo', undo], ['editor.redo', redo]] as const) {
    handlers[id] = () => crepe!.editor.action(ctx => {
      const view = ctx.get(editorViewCtx)
      return action(view.state, view.dispatch) ? { ok: true } : { ok: false, reason: 'unavailable' }
    })
  }
  if (markdownPreferences.diagrams) handlers['editor.mermaid'] = () => {
    insertMarkdown('```mermaid\nflowchart LR\n  A[Start] --> B[End]\n```')
    return { ok: true }
  }
  for (const [id, action] of [['editor.heading.toggle-fold', 'toggle'], ['editor.heading.fold-all', 'all'], ['editor.heading.unfold-all', 'none']] as const) {
    handlers[id] = () => { foldHeadings(action); return { ok: true } }
  }
  const toolbar: ToolbarCommand[] = ['bold', 'italic', 'ordered-list', 'bullet-list', 'inline-code', 'code-block', 'inline-math', 'math-block']
  for (const command of toolbar) {
    if (!markdownPreferences.math && command.includes('math')) continue
    handlers[`editor.${command}`] = () => { runCommand(command); return { ok: true } }
  }
  handlers['editor.paragraph'] = () => { crepe!.editor.action(callCommand(turnIntoTextCommand.key)); return { ok: true } }
  handlers['editor.strikethrough'] = () => { crepe!.editor.action(callCommand(toggleStrikethroughCommand.key)); return { ok: true } }
  handlers['editor.blockquote'] = () => { crepe!.editor.action(callCommand(wrapInBlockquoteCommand.key)); return { ok: true } }
  handlers['editor.task-list'] = () => { insertMarkdown('- [ ] '); return { ok: true } }
  handlers['editor.horizontal-rule'] = () => { insertMarkdown('\n---\n'); return { ok: true } }
  handlers['editor.hard-break'] = () => { insertMarkdown('  \n'); return { ok: true } }
  handlers['editor.table'] = () => { insertMarkdown('| 列 1 | 列 2 |\n| --- | --- |\n|  |  |'); return { ok: true } }
  handlers['editor.link'] = async () => { await applyLink(); return { ok: true } }
  handlers['editor.metadata.edit'] = async () => {
    if (!metadata.value) {
      const body = crepe!.editor.action(getMarkdown())
      const filename = (editorStore.currentFilePath?.split('/').at(-1) ?? '未命名').replace(/\.md$/i, '')
      const source = `---\ntitle: ${JSON.stringify(filename)}\ntags: []\n---\n${body}`
      metadata.value = splitNoteMetadata(source)
      editorStore.updateContent(source)
      editorStore.scheduleAutoSave(settingsStore.autoSaveInterval)
      await nextTick()
    }
    const input = editorRoot.value?.closest<HTMLElement>('.milkdown-host')?.querySelector<HTMLElement>('.note-metadata input')
    if (!input) return { ok: false, reason: 'unavailable' }
    input.scrollIntoView({ block: 'center' }); input.focus(); return { ok: true }
  }
  handlers['editor.heading'] = params => {
    if (!Number.isInteger(params) || Number(params) < 1 || Number(params) > 6) return { ok: false, reason: 'invalid-params' }
    crepe!.editor.action(callCommand(wrapInHeadingCommand.key, Number(params)))
    return { ok: true }
  }
  handlers['editor.font-size'] = params => {
    if (typeof params !== 'number' || !Number.isFinite(params) || params < 8 || params > 96) return { ok: false, reason: 'invalid-params' }
    fontSizeInput.value = params
    applyFontSizeValue()
    return { ok: true }
  }
  handlers['editor.insert-markdown'] = params => {
    if (typeof params !== 'string' || !params.trim() || params.length > 100000) return { ok: false, reason: 'invalid-params' }
    insertMarkdown(params)
    return { ok: true }
  }
  handlers['editor.callout'] = params => {
    if (!markdownPreferences.callouts) return { ok: false, reason: 'unsupported' }
    if (!params || typeof params !== 'object') return { ok: false, reason: 'invalid-params' }
    const { type, title = '', body = '', fold = '' } = params as Record<string, unknown>
    if (typeof type !== 'string' || !/^[\w-]{1,64}$/.test(type) || typeof title !== 'string' || /[\r\n]/.test(title)
      || typeof body !== 'string' || !['', '+', '-'].includes(String(fold)) || title.length + body.length > 100000) return { ok: false, reason: 'invalid-params' }
    insertMarkdown(`> [!${type}]${fold} ${title}\n${body.split(/\r?\n/).map(line => `> ${line}`).join('\n')}`)
    return { ok: true }
  }
  disposeCommands = registerEditorCommands({
    available: () => !loading.value && !!crepe && editorStore.mode === 'wysiwyg' && editorStore.saveStatus !== 'conflict'
      && !!targetPath && targetPath === editorStore.currentFilePath && crepe.editor.action(ctx => ctx.get(editorViewCtx).editable),
    handlers,
  })
}

function foldHeadings(action: 'toggle' | 'all' | 'none') {
  crepe?.editor.action(ctx => {
    const view = ctx.get(editorViewCtx)
    const tr = headingFoldTransaction(view.state, action)
    if (tr) view.dispatch(tr)
    if (action === 'all') {
      const viewport = editorRoot.value?.closest<HTMLElement>('.milkdown-host')
      if (viewport) viewport.scrollTop = 0
    }
  })
}
const diagramPreviews = new Map<string, { source: string; kind: string; apply: (value: HTMLElement) => void }>()
function renderDiagram(source: string, apply: (value: HTMLElement) => void, kind = 'mermaid') {
  for (const [id, entry] of diagramPreviews) {
    if (entry.apply === apply) diagramPreviews.delete(id)
  }
  const element = createMermaidPreview(source, themeStore.isDark, apply, kind, themeStore.currentThemeId)
  diagramPreviews.set(element.dataset.previewId!, { source, apply, kind })
  return element
}
watch(() => themeStore.currentThemeId, () => {
  const current = [...diagramPreviews.entries()]
  diagramPreviews.clear()
  for (const [id, entry] of current) {
    if (editorRoot.value?.querySelector(`[id="${id}"]`)) entry.apply(renderDiagram(entry.source, entry.apply, entry.kind))
  }
}, { flush: 'post' })

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
  if (command === 'inline-code' && editor.action(ctx => ctx.get(editorViewCtx).state.selection.empty)) {
    editor.action(ctx => {
      const view = ctx.get(editorViewCtx)
      const mark = view.state.schema.marks.inlineCode!
      const active = (view.state.storedMarks ?? view.state.selection.$from.marks()).some(item => item.type === mark)
      view.dispatch(active ? view.state.tr.removeStoredMark(mark) : view.state.tr.setStoredMarks([mark.create()]))
      view.focus()
    })
    return
  }
  // 顶部工具栏复用 Milkdown 命令，因此选区与浮动工具栏共享同一文档事务。
  const actions = {
    bold: callCommand(toggleStrongCommand.key),
    italic: callCommand(toggleEmphasisCommand.key),
    'ordered-list': callCommand(wrapInOrderedListCommand.key),
    'bullet-list': callCommand(wrapInBulletListCommand.key),
    'inline-code': callCommand(toggleInlineCodeCommand.key),
    'code-block': callCommand(createCodeBlockCommand.key, markdownPreferences.defaultLanguage),
    'inline-math': callCommand('ToggleLatex'),
    'math-block': callCommand(createCodeBlockCommand.key, 'LaTeX'),
  }
  editor.action(actions[command])
  editorRoot.value?.querySelector<HTMLElement>('.ProseMirror')?.focus()
}

async function applyLink() {
  if (!crepe) return
  const editor = crepe
  const snapshot = editor.editor.action(ctx => {
    const view = ctx.get(editorViewCtx)
    return { doc: view.state.doc, selection: view.state.selection }
  })
  const href = (await askPrompt(t('请输入链接地址', 'Enter link address'), 'https://'))?.trim()
  if (!href || crepe !== editor) return
  const label = snapshot.selection.empty ? await askPrompt(t('请输入链接文字', 'Enter link text'), href) : ''
  if (label === null || crepe !== editor) return

  editor.editor.action((ctx) => {
    const view = ctx.get(editorViewCtx)
    if (!view.state.doc.eq(snapshot.doc)) return
    view.dispatch(view.state.tr.setSelection(snapshot.selection))
    const commands = ctx.get(commandsCtx)
    if (view.state.selection.empty) {
      const text = label.trim() || href
      const from = view.state.selection.from
      const transaction = view.state.tr.insertText(text, from)
      transaction.setSelection(TextSelection.create(transaction.doc, from, from + text.length))
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
    defaultValue: metadata.value?.body ?? props.initialContent,
    // 使用标准 Markdown 图片节点，确保 alt 文本和相对路径可被其他编辑器直接读取。
    features: {
      [Crepe.Feature.TopBar]: false,
      [Crepe.Feature.Latex]: markdownPreferences.math,
      [Crepe.Feature.ImageBlock]: false,
    },
    featureConfigs: {
      [Crepe.Feature.Placeholder]: { text: t('开始记录你的想法…', 'Start writing your thoughts…') },
      [Crepe.Feature.CodeMirror]: {
        previewOnlyByDefault: true,
        previewToggleText: previewOnly => previewOnly ? t('编辑', 'Edit') : t('预览', 'Preview'),
        previewLabel: t('图表预览', 'Preview'),
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
  // Crepe 的 defaultsDeep 会合并语言数组与主题扩展内部配置。
  // 必须在功能配置完成后同时替换两者，以免默认语法发生冲突。
  crepe.editor.config(ctx => ctx.update(codeBlockConfig.key, config => ({
    ...config,
    languages: shikiLanguages(themeStore.resolvedCodeBlockTheme),
    renderLanguage: renderCodeLanguage,
    renderPreview: (language, content, applyPreview) => ['mermaid', 'function-plot'].includes(language.trim().toLowerCase())
      ? markdownPreferences.diagrams ? renderDiagram(content, applyPreview, language.trim().toLowerCase()) : null
      : config.renderPreview(language, content, applyPreview),
    extensions: [basicSetup, keymap.of([indentWithTab]), shikiEditorTheme(themeStore.resolvedCodeBlockTheme),
      indentUnit.of(' '.repeat(markdownPreferences.indent)), CodeEditorState.tabSize.of(markdownPreferences.indent),
      ...(markdownPreferences.wrapCode ? [CodeEditorView.lineWrapping] : [])],
  })))
  crepe.editor.config(ctx => ctx.update(remarkStringifyOptionsCtx, options => ({
    ...options, setext: markdownPreferences.heading === 'setext', bullet: markdownPreferences.bullet,
    incrementListMarker: markdownPreferences.incrementList, fence: markdownPreferences.fence,
  })))
  if (!markdownPreferences.autoLinks) crepe.editor.use($remark('disable-bare-autolinks', () => () => (tree, file) => {
    type Ast = { type: string; value?: string; url?: string; children?: Ast[]; position?: { start: { offset?: number }; end: { offset?: number } } }
    const source = String(file.value)
    const walk = (node: Ast) => {
      if (node.type === 'link' && node.position) {
        const raw = source.slice(node.position.start.offset, node.position.end.offset)
        if (/^(?:https?:\/\/|www\.)\S+$/.test(raw)) {
          node.type = 'text'; node.value = raw; delete node.children; delete node.url
        }
      }
      node.children?.forEach(walk)
    }
    walk(tree as Ast)
  }))
  crepe.editor.use(fontSizeMarkdownPlugin)
  crepe.editor.use(inlineCodeInputPlugin)
  if (markdownPreferences.callouts) crepe.editor.use(calloutPlugin)
  crepe.editor.use(headingFoldingPlugin)
  crepe.editor.use($prose(() => new Plugin({
    props: {
      nodeViews: { image: workspaceImageNodeView },
      handlePaste(_view, event) {
        const files = imageFiles(event.clipboardData?.files ?? null)
        if (!files.length) return false
        event.preventDefault(); void insertImages(files, 'paste'); return true
      },
      handleDrop(view, event) {
        const files = imageFiles(event.dataTransfer?.files ?? null)
        if (!files.length) return false
        event.preventDefault()
        const position = view.posAtCoords({ left: event.clientX, top: event.clientY })?.pos ?? view.state.selection.from
        void insertImages(files, 'drop', position); return true
      },
    },
  })))
  crepe.editor.use($prose(() => new Plugin({
    view(view) {
      const sync = (current: typeof view) => {
        canUndo.value = undoDepth(current.state) > 0
        canRedo.value = redoDepth(current.state) > 0
        const sections = headingSections(current.state.doc)
        const folded = headingFoldKey.getState(current.state)
        hasFoldableHeadings.value = sections.length > 0
        // 被隐藏的后代节点保留自身状态，但不算作可见的展开章节。
        let hiddenUntil = -1
        allHeadingsFolded.value = sections.length > 0 && sections.every(section => {
          if (section.from < hiddenUntil) return true
          if (!folded?.has(section.from)) return false
          hiddenUntil = section.end
          return true
        })
      }
      sync(view)
      return { update: sync }
    },
  })))
  if (markdownPreferences.callouts) crepe.editor.config(configureCalloutSerialization)
  crepe.on((listener) => {
    listener.markdownUpdated((_ctx, markdown, previousMarkdown) => {
      // 忽略编辑器初始化/回显事件，防止无内容变化时触发自动保存循环。
      const fullMarkdown = (metadata.value?.prefix ?? '') + markdown
      if (markdown === previousMarkdown || fullMarkdown === editorStore.content) return
      editorStore.updateContent(fullMarkdown)
      editorStore.scheduleAutoSave(settingsStore.autoSaveInterval)
    })
  })
  await crepe.create()
  if (editorRoot.value) disposeLanguagePicker = installLanguagePickerPopover(editorRoot.value)
  if (editorRoot.value) disposeCodeLabels = installCodeBlockLabels(editorRoot.value)
  if (editorRoot.value) disposeLinkNavigation = installLinkNavigation(editorRoot.value, navigateMarkdownHref)
  applyProofingPreferences()
  loading.value = false
  if (!disposed) installCommands()
})

watch([() => settingsStore.spellCheck, () => settingsStore.language], applyProofingPreferences)
watch(() => editorStore.headingRequest, request => {
  if (!request || request.path !== editorStore.currentFilePath || !crepe) return
  crepe.editor.action(ctx => {
    const view = ctx.get(editorViewCtx)
    let index = 0
    view.state.doc.forEach((node, offset) => {
      if (node.type.name !== 'heading') return
      if (index++ !== request.index) return
      view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, offset + 1)).scrollIntoView())
      view.focus()
    })
  })
})

onBeforeUnmount(() => { disposed = true; disposeCommands?.(); diagramPreviews.clear(); imageUrls.forEach(URL.revokeObjectURL); imageUrls.clear(); disposeLinkNavigation?.(); disposeCodeLabels?.(); disposeLanguagePicker?.(); void crepe?.destroy() })

defineExpose({ getEditor: () => crepe?.editor })
</script>

<template>
  <DiagramInteractions class="visual-editor" :class="{ 'hide-code-line-numbers': !markdownPreferences.lineNumbers }" :data-heading-style="headingAppearance.preferences.custom ? 'custom' : undefined" :style="headingAppearance.cssVariables">
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
    <p v-if="imageError" class="image-error" role="alert">{{ imageError }}</p>
    <p v-if="commandError" class="image-error" role="alert">{{ commandError }}</p>
    <div v-show="layout.editorToolbarVisible" class="markdown-toolbar" role="toolbar" :aria-label="t('Markdown 格式工具栏', 'Markdown formatting toolbar')">
      <div class="toolbar-group" role="group" :aria-label="t('编辑历史', 'Edit history')">
        <button type="button" :disabled="loading || !canUndo" :title="t('撤销 (Ctrl+Z)', 'Undo (Ctrl+Z)')" :aria-label="t('撤销', 'Undo')" @pointerdown.prevent="toolbarCommand('editor.undo')" @click="$event.detail === 0 && toolbarCommand('editor.undo')"><ControlIcon name="undo" /></button>
        <button type="button" :disabled="loading || !canRedo" :title="t('重做 (Ctrl+Y)', 'Redo (Ctrl+Y)')" :aria-label="t('重做', 'Redo')" @pointerdown.prevent="toolbarCommand('editor.redo')" @click="$event.detail === 0 && toolbarCommand('editor.redo')"><ControlIcon name="redo" /></button>
      </div>
      <div class="section-actions">
        <button type="button" :disabled="loading || !hasFoldableHeadings"
          :title="allHeadingsFolded ? t('展开所有章节正文', 'Expand all section content') : t('折叠所有章节，保留标题', 'Collapse all sections, keeping headings visible')"
          :aria-label="allHeadingsFolded ? t('展开所有章节', 'Unfold all sections') : t('折叠所有章节', 'Fold all sections')"
          @click="foldHeadings(allHeadingsFolded ? 'none' : 'all')">
          <ControlIcon :name="allHeadingsFolded ? 'unfold' : 'fold'" />
          <span>{{ allHeadingsFolded ? t('全部展开', 'Expand all') : t('全部折叠', 'Collapse all') }}</span>
        </button>
      </div>
      <div class="toolbar-group" role="group" :aria-label="t('标题与强调', 'Headings and emphasis')">
      <label class="toolbar-select heading-select" :title="t('设置标题级别', 'Set heading level')">
        <ControlIcon name="heading" />
        <select :aria-label="t('标题级别', 'Heading level')" @change="applyHeading">
          <option value="" selected>{{ t('标题', 'Heading') }}</option>
          <option value="paragraph">{{ t('正文', 'Paragraph') }}</option>
          <option v-for="level in 6" :key="level" :value="level">H{{ level }}</option>
        </select>
        <ControlIcon class="select-chevron" name="chevron" :size="12" />
      </label>
      <button type="button" :title="t('加粗 (Ctrl+B)', 'Bold (Ctrl+B)')" :aria-label="t('加粗', 'Bold')" @pointerdown.prevent="runCommand('bold')" @click="$event.detail === 0 && runCommand('bold')"><ControlIcon name="bold" /></button>
      <button type="button" :title="t('斜体 (Ctrl+I)', 'Italic (Ctrl+I)')" :aria-label="t('斜体', 'Italic')" @pointerdown.prevent="runCommand('italic')" @click="$event.detail === 0 && runCommand('italic')"><ControlIcon name="italic" /></button>
      <button type="button" :title="t('删除线', 'Strikethrough')" :aria-label="t('删除线', 'Strikethrough')" @pointerdown.prevent="toolbarCommand('editor.strikethrough')" @click="$event.detail === 0 && toolbarCommand('editor.strikethrough')"><ControlIcon name="strikethrough" /></button>
      </div>
      <div class="toolbar-group" role="group" :aria-label="t('列表', 'Lists')">
      <button type="button" :title="t('有序列表', 'Ordered list')" :aria-label="t('有序列表', 'Ordered list')" @pointerdown.prevent="runCommand('ordered-list')" @click="$event.detail === 0 && runCommand('ordered-list')"><ControlIcon name="orderedList" /></button>
      <button type="button" :title="t('无序列表', 'Bullet list')" :aria-label="t('无序列表', 'Bullet list')" @pointerdown.prevent="runCommand('bullet-list')" @click="$event.detail === 0 && runCommand('bullet-list')"><ControlIcon name="bulletList" /></button>
      <button type="button" :title="t('待办列表', 'Task list')" :aria-label="t('待办列表', 'Task list')" @pointerdown.prevent="toolbarCommand('editor.task-list')" @click="$event.detail === 0 && toolbarCommand('editor.task-list')"><ControlIcon name="taskList" /></button>
      <button type="button" :title="t('引用', 'Blockquote')" :aria-label="t('引用', 'Blockquote')" @pointerdown.prevent="toolbarCommand('editor.blockquote')" @click="$event.detail === 0 && toolbarCommand('editor.blockquote')"><ControlIcon name="quote" /></button>
      </div>
      <div class="toolbar-group" role="group" :aria-label="t('字号', 'Font size')">
      <label class="toolbar-select font-size-select" :title="t('选择预设字号', 'Choose a preset font size')">
        <ControlIcon name="fontSize" />
        <select :aria-label="t('文字字号', 'Font size')" @change="applyFontSize">
          <option value="" selected>{{ t('字号', 'Size') }}</option>
          <option v-for="size in [12, 14, 16, 18, 20, 24, 28, 32]" :key="size" :value="size">{{ size }} px</option>
        </select>
        <ControlIcon class="select-chevron" name="chevron" :size="12" />
      </label>
      <div class="font-size-input" :title="t('输入字号后按 Enter 或点击应用', 'Enter a font size, then press Enter or Apply')">
        <input v-model.number="fontSizeInput" type="number" min="8" max="96" step="1" :aria-label="t('自定义字号', 'Custom font size')"
          @keydown.enter.prevent="applyFontSizeValue" />
        <span>px</span>
        <button type="button" :title="t('应用自定义字号', 'Apply custom font size')" :aria-label="t('应用自定义字号', 'Apply custom font size')" @pointerdown.prevent="applyFontSizeValue" @click="$event.detail === 0 && applyFontSizeValue()">{{ t('应用', 'Apply') }}</button>
      </div>
      </div>
      <div class="toolbar-group" role="group" :aria-label="t('代码与公式', 'Code and math')">
      <button type="button" :title="t('行内代码', 'Inline code')" :aria-label="t('行内代码', 'Inline code')" @pointerdown.prevent="runCommand('inline-code')" @click="$event.detail === 0 && runCommand('inline-code')"><ControlIcon name="inlineCode" /></button>
      <button type="button" :title="t('代码块', 'Code block')" :aria-label="t('代码块', 'Code block')" @pointerdown.prevent="runCommand('code-block')" @click="$event.detail === 0 && runCommand('code-block')"><ControlIcon name="codeBlock" /></button>
      <button v-if="markdownPreferences.math" type="button" :title="t('行内公式', 'Inline formula')" :aria-label="t('行内公式', 'Inline formula')" @pointerdown.prevent="runCommand('inline-math')" @click="$event.detail === 0 && runCommand('inline-math')"><ControlIcon name="function" /></button>
      <button v-if="markdownPreferences.math" type="button" :title="t('公式块', 'Formula block')" :aria-label="t('公式块', 'Formula block')" @pointerdown.prevent="runCommand('math-block')" @click="$event.detail === 0 && runCommand('math-block')"><ControlIcon name="formula" /></button>
      </div>
      <div class="toolbar-group" role="group" :aria-label="t('插入内容', 'Insert content')">
      <button type="button" :title="t('插入表格', 'Insert table')" :aria-label="t('插入表格', 'Insert table')" @pointerdown.prevent="toolbarCommand('editor.table')" @click="$event.detail === 0 && toolbarCommand('editor.table')"><ControlIcon name="table" /></button>
      <button v-if="markdownPreferences.diagrams" type="button" :title="t('插入 Mermaid 图表', 'Insert Mermaid diagram')" :aria-label="t('插入 Mermaid 图表', 'Insert Mermaid diagram')" @pointerdown.prevent="toolbarCommand('editor.mermaid')" @click="$event.detail === 0 && toolbarCommand('editor.mermaid')"><ControlIcon name="diagram" /></button>
      <button type="button" :title="t('分隔线', 'Horizontal rule')" :aria-label="t('分隔线', 'Horizontal rule')" @pointerdown.prevent="toolbarCommand('editor.horizontal-rule')" @click="$event.detail === 0 && toolbarCommand('editor.horizontal-rule')"><ControlIcon name="rule" /></button>
      <button type="button" :title="t('插入链接', 'Insert link')" :aria-label="t('插入链接', 'Insert link')" @pointerdown.prevent="applyLink" @click="$event.detail === 0 && applyLink()"><ControlIcon name="link" /></button>
      <button type="button" :title="t('插入工作区图片', 'Insert workspace image')" :aria-label="t('插入工作区图片', 'Insert workspace image')" @pointerdown.prevent="chooseImages" @click="$event.detail === 0 && chooseImages()"><ControlIcon name="image" /></button>
      <input ref="imageInput" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple @change="selectedImages" />
      <label v-if="markdownPreferences.callouts" class="toolbar-select" :title="t('插入提示框', 'Insert callout')">
        <ControlIcon name="callout" />
        <select :aria-label="t('插入警告框', 'Insert callout')" @change="insertCallout">
          <option value="">{{ t('提示框', 'Callout') }}</option>
          <option v-for="(_, type) in calloutTypes" :key="type" :value="type">{{ type }}</option>
        </select>
        <ControlIcon class="select-chevron" name="chevron" :size="12" />
      </label>
      </div>
      <button class="hide-toolbar" type="button" :title="t('隐藏工具栏，可从视图菜单恢复', 'Hide toolbar; restore from the View menu')" :aria-label="t('隐藏工具栏', 'Hide toolbar')" @click="hideToolbar"><ControlIcon name="hide" /></button>
    </div>
    <div v-if="loading" class="editor-loading">{{ t('正在加载编辑器…', 'Loading editor…') }}</div>
    <div class="milkdown-host" :class="{ loading }">
      <section v-if="metadata" class="note-metadata" :aria-label="t('笔记属性', 'Note properties')">
        <span class="metadata-caption">{{ t('笔记属性', 'Note properties') }}</span>
        <h1 v-if="metadata.title">{{ metadata.title }}</h1>
        <div class="metadata-tags">
          <span class="metadata-label">{{ t('标签', 'Tags') }}</span>
          <span v-for="tag in metadata.tags" :key="tag" class="metadata-tag"><span>{{ tag }}</span><button type="button" :aria-label="`${t('移除标签', 'Remove tag')} ${tag}`" @click="setTags(metadata.tags.filter(item => item !== tag))">×</button></span>
          <form @submit.prevent="addTags"><input v-model="tagDraft" :aria-label="t('添加标签', 'Add tag')" :placeholder="t('+ 添加标签', '+ Add tag')" /><button v-if="tagDraft.trim()" type="submit">{{ t('添加', 'Add') }}</button></form>
        </div>
      </section>
      <div ref="editorRoot" />
    </div>
  </DiagramInteractions>
</template>

<style scoped>
.visual-editor { display: flex; flex: 1; min-height: 0; flex-direction: column; background: var(--color-background-primary); }
.image-error { margin: 0; padding: var(--space-sm) var(--space-lg); color: var(--color-error); background: var(--color-error-soft); }
.hide-code-line-numbers :deep(.cm-lineNumbers) { display: none; }
.markdown-toolbar { display: flex; align-items: center; flex-wrap: wrap; flex-shrink: 0; gap: 6px; min-height: 44px; max-height: 35vh; overflow-y: auto; padding: 6px var(--space-md); border-bottom: 1px solid var(--color-border-subtle); background: var(--color-surface-primary); }
.hide-toolbar { margin-inline-start: auto; }
.markdown-toolbar button:disabled { opacity: .4; cursor: default; }
.toolbar-group { display: inline-flex; align-items: center; gap: 2px; flex-shrink: 0; }
.toolbar-group + .toolbar-group { padding-inline-start: 6px; border-inline-start: 1px solid var(--color-border-subtle); }
.markdown-toolbar button { display: inline-grid; place-items: center; min-width: 32px; min-height: 32px; padding: 4px 6px; border-radius: var(--radius-sm); color: var(--color-text-secondary); transition: background-color var(--motion-fast), color var(--motion-fast); }
.markdown-toolbar button:hover:not(:disabled), .toolbar-select:hover { background: var(--color-accent-soft); color: var(--color-accent-primary); }
.markdown-toolbar button:active:not(:disabled) { background: var(--color-background-active); }
.markdown-toolbar button:focus-visible, .toolbar-select:focus-within { outline: 2px solid var(--color-border-focus); outline-offset: 1px; }
.section-actions { display: inline-flex; align-items: center; flex-shrink: 0; padding: 0; border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-background-secondary); }
.markdown-toolbar .section-actions button { display: inline-flex; align-items: center; justify-content: center; gap: 5px; min-height: 28px; padding: 4px 8px; font: inherit; font-size: var(--font-size-xs); line-height: 1.25; white-space: nowrap; color: var(--color-text-secondary); }
.markdown-toolbar .section-actions button:hover:not(:disabled) { background: var(--color-background-hover); color: var(--color-accent-primary); }
.markdown-toolbar .section-actions button:disabled { opacity: .45; cursor: default; }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.toolbar-select { position: relative; display: inline-flex; align-items: center; gap: 5px; min-height: 32px; padding: 3px 7px; border-radius: var(--radius-sm); color: var(--color-text-secondary); }
.toolbar-select select { appearance: none; width: auto; min-width: 42px; padding-inline-end: 15px; border: 0; outline: 0; background: transparent; color: inherit; cursor: pointer; font-size: var(--font-size-sm); }
.toolbar-select option { color: var(--color-text-primary); background: var(--color-surface-primary); }
.select-chevron { position: absolute; right: 7px; pointer-events: none; }
.font-size-input { display: inline-flex; align-items: center; height: 30px; margin-left: 2px; overflow: hidden; border: 1px solid var(--color-border-default); border-radius: var(--radius-sm); color: var(--color-text-secondary); background: var(--color-background-primary); }
.font-size-input:focus-within { border-color: var(--color-border-focus); box-shadow: 0 0 0 1px var(--color-border-focus); }
.font-size-input input { width: 42px; height: 100%; padding-left: 7px; border: 0; outline: 0; background: transparent; color: var(--color-text-primary); }
.font-size-input span { font-size: var(--font-size-xs); }
.font-size-input button { min-width: auto; min-height: 100%; margin-left: 4px; padding: 3px 7px; border-left: 1px solid var(--color-border-default); border-radius: 0; font-size: var(--font-size-xs); }
.milkdown-host { flex: 1; min-height: 0; overflow: auto; color: var(--color-text-primary); }
.milkdown-host.loading { visibility: hidden; }
.note-metadata { box-sizing: border-box; width: 90%; margin: 0 auto 20px; padding: 20px 24px; border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-primary); }
.metadata-caption { color: var(--color-text-secondary); font-size: var(--font-size-xs); }
.note-metadata h1 { margin: 10px 0 16px; font-size: 24px; color: var(--color-text-primary); overflow-wrap: anywhere; }
.metadata-tags { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.metadata-label { margin-right: 4px; color: var(--color-text-secondary); font-size: var(--font-size-sm); }
.metadata-tag { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; padding: 4px 8px; border-radius: var(--radius-full); background: var(--color-accent-soft); color: var(--color-accent-primary); font-size: var(--font-size-sm); }
.metadata-tag > span { overflow-wrap: anywhere; min-width: 0; }
.metadata-tag button { color: inherit; padding: 0 3px; }
.metadata-tags form { display: flex; gap: 6px; }
.metadata-tags input { width: 110px; padding: 5px 8px; border: 1px dashed var(--color-border-default); border-radius: var(--radius-sm); background: transparent; color: var(--color-text-primary); }
.metadata-tags input:focus { outline: 2px solid var(--color-border-focus); }
.milkdown-host :deep(.editor-mermaid-preview) { padding: 20px; overflow: auto; background: var(--color-surface-primary); color: var(--color-text-primary); }
.milkdown-host :deep(.editor-mermaid-preview svg) { display: block; max-width: 100%; height: auto; margin: auto; }
.milkdown-host :deep(.editor-mermaid-preview.has-error) { color: var(--color-error); white-space: pre-wrap; }
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
/* Mermaid 在编辑器外部测量 HTML 标签。 Crepe 的段落填充在插入固定大小的 SVGforeignObjects 后不得放大它们。 */
.milkdown-host :deep(.editor-mermaid-preview svg foreignObject p) { margin: 0; padding: 0; line-height: inherit; font-weight: inherit; }
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
.milkdown-host :deep(.ProseMirror :not(pre) > code) { padding: .12em .35em; border: 1px solid var(--color-code-border); border-radius: var(--radius-sm); background: var(--color-code-background); color: var(--color-code-text); font-size: .9em; box-decoration-break: clone; }
:global([data-theme='dark'] .milkdown-host .milkdown) { color-scheme: dark; }
@media (max-width: 680px) { .toolbar-select select { min-width: 46px; width: 46px; } }
</style>
