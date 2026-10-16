<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { editorViewCtx, parserCtx, serializerCtx } from '@milkdown/kit/core'
import { getMarkdown } from '@milkdown/kit/utils'
import { closeHistory, undo, redo } from '@milkdown/kit/prose/history'
import RichMarkdownEditor from '../../src/features/editor/RichMarkdownEditor.vue'
import type { RichEditorPlatform } from '../../src/features/editor/richEditorPlatform'
import { defaultMarkdownPreferences } from '../../src/stores/markdownPreferences'
import { splitNoteMetadata } from '../../src/features/editor/noteMetadata'
import type { CommandHandler, CommandResult, EditorCommandId } from '../../src/services/editorCommandService'
import { mobileRequest } from './native'
import { mobileVaultPath } from './mobileMarkdown'
import type { BuiltinTheme } from './theme'

const props = defineProps<{ modelValue: string; path: string; label: string; language: string; theme: BuiltinTheme; readOnly?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [source: string]; compositionStart: []; compositionEnd: []; error: [code: string]; image: []; link: [href: string] }>()
const surface = ref<InstanceType<typeof RichMarkdownEditor>>()
const state = reactive({ content: props.modelValue, ready: false, composing: false })
const initialPath = props.path, initialSource = props.modelValue, newline = props.modelValue.match(/\r\n|\r|\n/)?.[0] ?? '\n'
let disposed = false, active: { available: () => boolean; handlers: Partial<Record<EditorCommandId, CommandHandler>> } | undefined
let transactionQueued = false
const current = () => !disposed && props.path === initialPath
// Exact original/acknowledged bytes remain attached to their actual parsed document.
const originals = new Map<string, string>()
let originalsSize = 0
function remember(normalized: string, raw: string) {
  if (originals.has(normalized)) return
  while (originals.size > 1 && originalsSize + normalized.length + raw.length > 4 * 1024 * 1024) {
    const key = [...originals.keys()][1]!, value = originals.get(key)!
    originalsSize -= key.length + value.length; originals.delete(key)
  }
  originals.set(normalized, raw); originalsSize += normalized.length + raw.length
}
function renderedSource() {
  const editor = surface.value?.getEditor()
  return editor ? (splitNoteMetadata(state.content)?.prefix ?? '') + editor.action(getMarkdown()) : null
}
function update(content: string) {
  if (!current() || !state.ready) return
  const value = originals.get(content) ?? content.replace(/\r\n|\r|\n/g, newline)
  if (value === state.content) return
  state.content = value; emit('update:modelValue', value)
}
async function execute(id: EditorCommandId, params?: unknown): Promise<CommandResult> {
  const target = active
  if (!target?.available()) return { ok: false, reason: 'unavailable' }
  const action = target.handlers[id]
  if (!action) return { ok: false, reason: 'unsupported' }
  try { return await action(params) } catch { return { ok: false, reason: 'failed' } }
}
const platform: RichEditorPlatform = {
  document: {
    get content() { return state.content }, get currentFilePath() { return props.path }, currentNoteId: null,
    mode: 'wysiwyg', saveStatus: 'dirty', headingRequest: null, referenceRequest: null, blockRequest: null,
    captureDocument: () => current, updateContent: update, scheduleAutoSave: () => {},
  },
  settings: { spellCheck: true, get language() { return props.language }, autoSaveInterval: 0 },
  theme: { get isDark() { return props.theme === 'dark' }, get currentThemeId() { return props.theme }, get resolvedCodeBlockTheme() { return props.theme === 'dark' ? 'github-dark' : 'github-light' } },
  layout: reactive({ editorToolbarVisible: true, focusMode: false }), headings: { preferences: { custom: false }, cssVariables: {} },
  markdown: { ...defaultMarkdownPreferences },
  assets: {
    resolvePath: mobileVaultPath, remoteImages: false, requestImage: () => emit('image'),
    async read(path) {
      const { content_base64: encoded } = await mobileRequest<{ content_base64: string }>({ kind: 'read_image', path })
      if (!encoded || encoded.length > 4 * Math.ceil(5 * 1024 * 1024 / 3)) throw Error('MOBILE_IMAGE_TOO_LARGE')
      const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0)), extension = path.split('.').at(-1)?.toLowerCase()
      return new Blob([bytes], { type: /jpe?g/.test(extension ?? '') ? 'image/jpeg' : `image/${extension}` })
    },
  },
  commands: { register(target) { active = target; return () => { if (active === target) active = undefined } }, execute },
  openLink: href => { if (current()) emit('link', href); return false },
  documentChanged() {
    // Milkdown coalesces its public listener; a native acknowledged image followed
    // immediately by Undo must still publish the actual final document.
    if (transactionQueued) return
    transactionQueued = true
    queueMicrotask(() => { transactionQueued = false; const source = current() && state.ready ? renderedSource() : null; if (source !== null) update(source) })
  },
}
function ready() {
  const normalized = renderedSource()
  if (normalized !== null) remember(normalized, initialSource)
  state.ready = true
}
function view() { return surface.value?.getEditor()?.action(ctx => ctx.get(editorViewCtx)) }
function selection() {
  const editor = surface.value?.getEditor()
  if (!editor || !state.ready || !current() || state.composing || props.readOnly) return null
  const marker = crypto.randomUUID()
  const template = editor.action(ctx => {
    const currentView = ctx.get(editorViewCtx)
    const node = currentView.state.schema.nodes.image!.create({ src: `opennexus-rich-image-${marker}`, alt: '' })
    const transaction = currentView.state.tr.replaceSelectionWith(node)
    return (splitNoteMetadata(state.content)?.prefix ?? '') + ctx.get(serializerCtx)(transaction.doc)
  }).replace(/\r\n|\r|\n/g, newline)
  return { template, marker }
}
function acceptImage(before: string, after: string) {
  const editor = surface.value?.getEditor()
  if (!editor || !current() || state.composing || state.content !== before) return false
  return editor.action(ctx => {
    const parsed = ctx.get(parserCtx)(splitNoteMetadata(after)?.body ?? after)
    if (!parsed) return false
    remember((splitNoteMetadata(after)?.prefix ?? '') + ctx.get(serializerCtx)(parsed), after)
    const currentView = ctx.get(editorViewCtx)
    currentView.dispatch(closeHistory(currentView.state.tr).replaceWith(0, currentView.state.doc.content.size, parsed.content))
    currentView.dispatch(closeHistory(currentView.state.tr))
    surface.value?.acknowledgeDocument()
    state.content = after; emit('update:modelValue', after)
    return true
  })
}
watch(() => props.modelValue, content => { if (content !== state.content) emit('error', 'MOBILE_EDITOR_CONTENT_CHANGED') })
onBeforeUnmount(() => { disposed = true; active = undefined; originals.clear() })
defineExpose({
  getEditor: () => surface.value?.getEditor(), source: () => state.content, selection, acceptImage,
  undo: () => { const currentView = view(); return Boolean(currentView && !props.readOnly && !state.composing && undo(currentView.state, currentView.dispatch)) },
  redo: () => { const currentView = view(); return Boolean(currentView && !props.readOnly && !state.composing && redo(currentView.state, currentView.dispatch)) },
  blur: () => view()?.dom.blur(), focus: () => view()?.focus(),
})
</script>

<template><RichMarkdownEditor ref="surface" :initial-content="initialSource" :platform="platform" :read-only="readOnly" :label="label" touch @ready="ready" @composition-start="state.composing=true;emit('compositionStart')" @composition-end="state.composing=false;emit('compositionEnd')" /></template>
