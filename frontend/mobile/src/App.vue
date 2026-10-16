<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Brush, CircleCheck, Document, FolderOpened, Search } from '@element-plus/icons-vue'
import WorkspaceFrame from '../../src/components/common/WorkspaceFrame.vue'
import TitleBarFrame from '../../src/components/common/TitleBarFrame.vue'
import PrimaryNavigation from '../../src/components/common/PrimaryNavigation.vue'
import ControlIcon from '../../src/components/common/ControlIcon.vue'
import AppDialog from '../../src/components/common/AppDialog.vue'
import FileTreeNode from '../../src/features/workspace/FileTreeNode.vue'
import WorkspaceLanding from '../../src/features/workspace/WorkspaceLanding.vue'
import { appLocale, t } from '../../src/i18n'
import type { FileNode } from '../../src/contracts'
import MobileEditor from './MobileEditor.vue'
import MobileCanvas from './MobileCanvas.vue'
import MobilePreview from './MobilePreview.vue'
import ExportPanel from './ExportPanel.vue'
import { mobileBackground, mobileCapabilities, mobileOnBack, mobileOpenExternal, mobileRequest, mobileVault, type MobileBackListener } from './native'
import { mobileVaultPath } from './mobileMarkdown'
import { createNoteSession, errorCode } from './noteSession'
import { createImageSession, type ImageSelection } from './imageSession'
import { errorText, languageKey, savedLanguage, text, type Language } from './locale'
import { canvasText } from './canvasLocale'
import { useMobileTheme } from './theme'
import { resolveCanvasFile } from '../../src/services/workspaceDocuments'
import type { DraftSummary, Entry, MutationIntent, NoteDocument, NotePage, Page, PendingOperation, SaveIntent } from './types'

const language = ref<Language>(savedLanguage()), copy = computed(() => text(language.value))
const appearanceEnabled = ref(false)
const { choice: themeChoice, resolved: resolvedTheme, nativeError: themeError, retryNative: retryTheme } = useMobileTheme(appearanceEnabled)
const compact = ref(window.innerWidth <= 760), primaryOpen = ref(false), secondaryOpen = ref(false)
const primaryExpanded = ref(false)
const preferencesOpen = ref(false)
function resizeWorkspace() { compact.value = window.innerWidth <= 760 }
function closePanels() { primaryOpen.value = false; secondaryOpen.value = false }
function togglePrimaryExpansion() { if (compact.value) primaryOpen.value = false; else primaryExpanded.value = !primaryExpanded.value }
const session = createNoteSession(), editor = ref<InstanceType<typeof MobileEditor>>()
const canvas = ref<InstanceType<typeof MobileCanvas>>()
const canvasFieldsPending = ref(false)
const isCanvas = computed(() => /\.canvas$/i.test(session.state.view?.draft.path ?? ''))
const canvasCopy = computed(() => canvasText(language.value))
const images = createImageSession(session, () => isCanvas.value ? canvas.value : editor.value), imageInput = ref<HTMLInputElement>()
const preview = ref(false), fileActions = ref<HTMLDetailsElement>()
const imageBlocked = computed(() => images.state.busy || Boolean(images.state.pending) || images.state.unknown)
let imageSelection: { key: string; generation: number; selection: ImageSelection } | null = null
const ready = ref(false), booting = ref(false), bootError = ref(''), version = ref('')
const exportOpen = ref(false), exportAllowed = ref(false), externalAllowed = ref(false)
const exportPanel = ref<InstanceType<typeof ExportPanel>>()
const navigationBusy = ref(false)
let backListener: MobileBackListener | undefined, backRegistration: Promise<void> | undefined
const tab = ref<'notes' | 'drafts' | 'pending'>('notes'), query = ref(''), directory = ref(''), offset = ref(0)
const notePage = ref<NotePage>({ items: [], total: 0, directories: [], unsearched_large_notes: 0 })
const draftPage = ref<Page<DraftSummary>>({ items: [], total: 0 }), pendingPage = ref<Page<PendingOperation>>({ items: [], total: 0 })
const loading = ref(false), listError = ref(''), notice = ref(''), actionBusy = ref(false)
let disposed = false, bootRevision = 0, listRevision = 0, inspectionRevision = 0
const size = 20
const dirty = computed(() => Boolean(session.state.view && (!session.state.view.draft.file_id || session.state.generation !== session.state.savedGeneration)))
const status = computed(() => session.state.composing ? copy.value.input : session.state.saving ? copy.value.saving
  : canvasFieldsPending.value ? copy.value.unkept : session.state.unknownSave ? copy.value.needsCheck : !dirty.value ? copy.value.saved
  : session.state.persistedGeneration >= session.state.generation ? copy.value.draftKept : copy.value.unkept)
const total = computed(() => tab.value === 'notes' ? notePage.value.total : tab.value === 'drafts' ? draftPage.value.total : pendingPage.value.total)
const current = computed(() => session.state.view?.current ?? null)
const documentKey = () => session.state.view ? `${session.state.view.draft.id}:${session.state.view.draft.edit_session}` : ''
watch(documentKey, () => { images.bind(); imageSelection = null; preview.value = false; canvasFieldsPending.value = false })
const basename = (path: string) => path.split('/').at(-1) || path
const parent = (path: string) => path.split('/').slice(0, -1).join('/')
const fileNodes = computed<FileNode[]>(() => [
  ...notePage.value.directories.map(path => ({ id: `folder:${path}`, name: basename(path), path, type: 'folder' as const })),
  ...notePage.value.items.map(entry => ({ id: entry.file_id, name: basename(entry.path), path: entry.path, type: 'file' as const })),
])
const navigationGroups = computed(() => [
  { key: 'knowledge', label: t('知识与学习', 'Knowledge'), items: [
    { name: 'workspace', icon: FolderOpened, label: t('工作区', 'Workspace') },
    { name: 'search', icon: Search, label: t('搜索', 'Search') },
    { name: 'drafts', icon: Document, label: copy.value.drafts },
    { name: 'pending', icon: CircleCheck, label: copy.value.pending },
  ] },
  { key: 'extensions', label: t('外观', 'Appearance'), items: [{ name: 'themes', icon: Brush, label: t('主题', 'Themes') }] },
])
async function navigateSection(name: string) {
  if (session.state.composing || session.state.saving || imageBlocked.value || canvasFieldsPending.value) return
  closePanels()
  if (name === 'themes' || name === 'settings') { preferencesOpen.value = true; return }
  if (name === 'workspace' || name === 'search') {
    if (name === 'search') secondaryOpen.value = true
    else if (session.state.view) { secondaryOpen.value = true; return }
    selectTab('notes')
  } else if (name === 'drafts' || name === 'pending') { await leave(); if (!session.state.view) selectTab(name) }
}
async function openTreeNode(node: FileNode) {
  if (node.type === 'folder') { enterDirectory(node.path); return }
  const entry = notePage.value.items.find(entry => entry.file_id === node.id)
  if (entry) { await openNote(entry); if (session.state.view?.draft.file_id === entry.file_id) closePanels() }
}
const displayError = (code: string) => errorText(code, language.value)
const number = (value: number) => new Intl.NumberFormat(language.value).format(value)

interface PathDialog { kind: 'path'; action: 'create' | 'copy' | 'rename'; path: string; format: 'markdown' | 'canvas'; doc: NoteDocument | null; busy: boolean; error: string }
interface MutationDialog { kind: 'mutation'; intent: MutationIntent; doc: NoteDocument | null; busy: boolean; error: string }
interface DiscardDialog { kind: 'discard'; id: string; edit_session: string; busy: boolean; error: string }
interface LinkDialog { kind: 'link'; href: string; busy: boolean; error: string }
const dialog = ref<PathDialog | MutationDialog | DiscardDialog | LinkDialog | null>(null), pathInput = ref<HTMLInputElement>()
watch(language, value => { appLocale.value = value; document.documentElement.lang = value; try { localStorage.setItem(languageKey, value) } catch { /* Session preference still works. */ } }, { immediate: true })

async function load() {
  const revision = ++listRevision, selected = tab.value, page = offset.value, q = query.value, folder = directory.value
  loading.value = true; listError.value = ''
  if (!session.state.view) session.clearError()
  try {
    const [notes, drafts, pending] = await Promise.all([
      mobileRequest<NotePage>({ kind: 'list_notes', offset: selected === 'notes' ? page : 0, limit: size, query: q, directory: folder }),
      mobileRequest<Page<DraftSummary>>({ kind: 'list_drafts', offset: selected === 'drafts' ? page : 0, limit: size }),
      mobileRequest<Page<PendingOperation>>({ kind: 'list_pending', offset: selected === 'pending' ? page : 0, limit: size }),
    ])
    if (disposed || revision !== listRevision) return
    notePage.value = notes; draftPage.value = drafts; pendingPage.value = pending
  } catch (error) { if (!disposed && revision === listRevision) listError.value = errorCode(error) }
  finally { if (!disposed && revision === listRevision) loading.value = false }
}
async function boot() {
  const revision = ++bootRevision; booting.value = true; bootError.value = ''
  try {
    const capabilities = await mobileCapabilities()
    if (!capabilities.notes) throw new Error('MOBILE_NOTES_UNAVAILABLE')
    await mobileVault()
    if (disposed || revision !== bootRevision) return
    if (capabilities.navigation === true) await registerBack()
    appearanceEnabled.value = capabilities.appearance === true
    if (disposed || revision !== bootRevision) return
    version.value = capabilities.version; exportAllowed.value = capabilities.exports === true; externalAllowed.value = capabilities.external_links === true; ready.value = true; await load()
  } catch (error) { if (!disposed && revision === bootRevision) bootError.value = errorCode(error) }
  finally { if (!disposed && revision === bootRevision) booting.value = false }
}
function selectTab(value: typeof tab.value) { inspectionRevision++; tab.value = value; offset.value = 0; void load() }
function enterDirectory(path: string) { inspectionRevision++; directory.value = path; query.value = ''; offset.value = 0; void load() }
function search() { inspectionRevision++; offset.value = 0; void load() }
function turnPage(delta: number) { inspectionRevision++; offset.value = Math.max(0,offset.value+delta*size); void load() }
async function runEditor(action: () => Promise<unknown>) { try { await action() } catch { /* The session owns and scopes its feedback. */ } }
async function closeEditor() { await session.close(); if (!disposed) await load() }
async function leave() {
  if (navigationBusy.value || session.state.composing || canvasFieldsPending.value || imageBlocked.value) return
  navigationBusy.value = true
  try { await runEditor(closeEditor) } finally { navigationBusy.value = false }
}
async function registerBack() {
  backRegistration ??= mobileOnBack(() => { void navigateBack() }).then(listener => {
    if (disposed) void Promise.resolve(listener.unregister()).catch(() => undefined)
    else backListener = listener
  }).catch(() => { backRegistration = undefined; throw Error('MOBILE_NAVIGATION_UNAVAILABLE') })
  await backRegistration
}
async function navigateBack() {
  if (disposed || !ready.value || navigationBusy.value || session.state.composing || session.state.saving || actionBusy.value || imageBlocked.value) return
  navigationBusy.value = true
  try {
    if (dialog.value) { await cancelDialog(); return }
    if (exportOpen.value) { exportPanel.value?.handleBack(); return }
    if (preferencesOpen.value) { preferencesOpen.value = false; return }
    if (primaryOpen.value || secondaryOpen.value) { closePanels(); return }
    if (canvasFieldsPending.value) return
    if (fileActions.value?.open) { fileActions.value.open = false; return }
    if (preview.value) { preview.value = false; return }
    if (isCanvas.value && canvas.value?.handleBack()) return
    if (session.state.view) { await closeEditor(); return }
    if (tab.value !== 'notes') { selectTab('notes'); return }
    if (query.value) { query.value = ''; search(); return }
    if (directory.value) { enterDirectory(parent(directory.value)); return }
    await mobileBackground()
  } catch (error) {
    if (!disposed) { if (session.state.view) session.reportError(errorCode(error)); else listError.value = errorCode(error) }
  } finally { navigationBusy.value = false }
}
async function save() { await runEditor(async () => { await session.save(); await load() }) }
function undo() { if (isCanvas.value) canvas.value?.undo(); else editor.value?.undo() }
function redo() { if (isCanvas.value) canvas.value?.redo(); else editor.value?.redo() }
function selectImage() {
  const selection = isCanvas.value ? canvas.value?.imagePosition() : editor.value?.selection()
  if (!selection || imageBlocked.value || session.state.composing || session.state.pendingSave) return
  imageSelection = { key: documentKey(), generation: session.state.generation, selection }
  if (fileActions.value) fileActions.value.open = false
  editor.value?.blur()
  imageInput.value?.click()
}
function togglePreview() { if (fileActions.value) fileActions.value.open = false; preview.value = !preview.value; editor.value?.blur() }
async function openExport() {
  await runEditor(async () => {
    await session.flush(); editor.value?.blur()
    if (fileActions.value) fileActions.value.open = false
    exportOpen.value = true
  })
}
function reviewedPath(href: string) { const source = session.state.view?.draft.path; return !source ? null : isCanvas.value ? resolveCanvasFile(href)?.slice(1) ?? null : mobileVaultPath(source, href) }
function canOpenLink(href: string) { return /^https?:\/\//i.test(href) ? externalAllowed.value : Boolean(reviewedPath(href)) }
async function openReviewedLink() {
  const active=dialog.value;if (!active || active.kind!=='link' || active.busy) return
  active.busy=true;active.error=''
  try {
    if (/^https?:\/\//i.test(active.href)) await mobileOpenExternal(active.href)
    else {
      const path=reviewedPath(active.href)
      if (!path) throw Error('MOBILE_LINK_UNSUPPORTED')
      const document=await mobileRequest<NoteDocument>({kind:'read_path',path})
      if (dialog.value!==active) return
      await session.openNote(document.file_id)
    }
    if (dialog.value===active) dialog.value=null
  } catch (error) { if (dialog.value===active) active.error=errorCode(error) }
  finally { if (dialog.value===active) active.busy=false }
}
async function imageSelected(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0], selected = imageSelection
  input.value = ''; imageSelection = null
  if (file && selected) await images.importFile(file, selected.selection, selected.key, selected.generation)
}
function openNote(entry: Entry) { inspectionRevision++; notice.value = ''; void runEditor(() => session.openNote(entry.file_id)) }
function openDraft(draft: DraftSummary) { inspectionRevision++; notice.value = ''; void runEditor(() => session.openDraft(draft.id)) }
function openPath(action: 'create' | 'copy', format: 'markdown' | 'canvas' = isCanvas.value ? 'canvas' : 'markdown') {
  inspectionRevision++
  dialog.value = { kind: 'path', action, path: '', format, doc: null, busy: false, error: '' }
  void nextTick(() => pathInput.value?.focus())
}
function notePath(value: string, format: 'markdown' | 'canvas') { const path = value.trim(), suffix = format === 'canvas' ? '.canvas' : '.md'; return path.toLowerCase().endsWith(suffix) ? path : `${path}${suffix}` }
async function reviewFile(action: 'rename' | 'delete') {
  const key = documentKey(), generation = session.state.generation, file = session.state.view?.draft.file_id
  if (!key || !file || dirty.value || session.state.composing || actionBusy.value) return
  actionBusy.value = true
  try {
    await session.flush()
    const doc = await mobileRequest<NoteDocument>({ kind: 'read_note', file_id: file })
    if (disposed || key !== documentKey() || generation !== session.state.generation) return
    if (action === 'rename') {
      dialog.value = { kind: 'path', action, path: doc.path, format: /\.canvas$/i.test(doc.path) ? 'canvas' : 'markdown', doc, busy: false, error: '' }
      await nextTick(); pathInput.value?.focus()
    } else {
      const intent = await mobileRequest<MutationIntent>({ kind: 'prepare_mutation', action, file_id: doc.file_id, expected: doc.hash, destination: '' })
      if (!disposed && key === documentKey()) dialog.value = { kind: 'mutation', intent, doc, busy: false, error: '' }
    }
  } catch (error) { if (!disposed && key === documentKey()) session.reportError(errorCode(error)) }
  finally { if (!disposed) actionBusy.value = false }
}
async function submitPath() {
  const active = dialog.value
  if (!active || active.kind !== 'path' || !active.path.trim() || active.busy) return
  const path = notePath(active.path, active.format); active.busy = true; active.error = ''
  try {
    if (active.action === 'create') await session.create(path)
    else if (active.action === 'copy') await session.copy(path)
    else {
      const doc = active.doc
      if (!doc) throw new Error('FILE_NOT_FOUND')
      const intent = await mobileRequest<MutationIntent>({ kind: 'prepare_mutation', action: 'rename', file_id: doc.file_id, expected: doc.hash, destination: path })
      if (dialog.value === active) dialog.value = { kind: 'mutation', intent, doc, busy: false, error: '' }
      return
    }
    if (dialog.value === active) dialog.value = null
  } catch (error) { if (dialog.value === active) active.error = errorCode(error) }
  finally { if (dialog.value === active) active.busy = false }
}
async function checkMutation() {
  const active = dialog.value
  if (!active || active.kind !== 'mutation' || active.busy) return
  active.busy = true; active.error = ''
  try {
    const observed = await mobileRequest<MutationIntent>({ kind: 'observe_mutation', operation_id: active.intent.operation_id })
    if (dialog.value === active) active.intent = observed
  } catch (error) { if (dialog.value === active) active.error = errorCode(error) }
  finally { if (dialog.value === active) active.busy = false }
}
async function finishMutation(intent: MutationIntent) {
  dialog.value = null
  // File actions require a clean draft and freeze the editor until resolved.
  if (session.state.view) await session.close()
  if (intent.kind === 'rename') directory.value = parent(intent.destination)
  notice.value = copy.value.completed; await load()
}
async function applyMutation() {
  const active = dialog.value
  if (!active || active.kind !== 'mutation' || active.busy) return
  active.busy = true; active.error = ''
  try {
    const checked = await mobileRequest<MutationIntent>({ kind: 'observe_mutation', operation_id: active.intent.operation_id })
    const result = checked.state === 'committed' ? checked : await mobileRequest<MutationIntent>({ kind: 'apply_mutation', operation_id: checked.operation_id })
    if (dialog.value !== active) return
    active.intent = result
    if (result.state === 'committed') await finishMutation(result)
  } catch (error) { if (dialog.value === active) active.error = errorCode(error) }
  finally { if (dialog.value === active) active.busy = false }
}
async function cancelDialog() {
  const active = dialog.value
  if (!active || active.busy) return
  if (active.kind !== 'mutation') { dialog.value = null; return }
  if (active.intent.state === 'committed') { await finishMutation(active.intent); return }
  active.busy = true; active.error = ''
  try {
    const checked = await mobileRequest<MutationIntent>({ kind: 'observe_mutation', operation_id: active.intent.operation_id })
    const result = checked.state === 'committed' ? checked : await mobileRequest<MutationIntent>({ kind: 'cancel_mutation', operation_id: checked.operation_id })
    if (dialog.value !== active) return
    active.intent = result
    if (result.state === 'cancelled') dialog.value = null
  } catch (error) { if (dialog.value === active) active.error = errorCode(error) }
  finally { if (dialog.value === active) active.busy = false }
}
async function inspect(operation: PendingOperation) {
  if (loading.value) return
  const revision = ++inspectionRevision, key = documentKey()
  const active = () => !disposed && revision === inspectionRevision && key === documentKey()
  listError.value = ''
  try {
    if (operation.kind === 'save' || operation.kind === 'image') {
      const result = await mobileRequest<SaveIntent>({ kind: operation.kind === 'save' ? 'observe_save' : 'observe_image', operation_id: operation.operation_id })
      if (!active()) return
      if (result.state === 'committed') notice.value = copy.value.completed
      if (operation.draft_id) await session.openDraft(operation.draft_id)
      await load()
    } else {
      const intent = await mobileRequest<MutationIntent>({ kind: 'observe_mutation', operation_id: operation.operation_id })
      if (!active()) return
      let doc: NoteDocument | null = null
      if (intent.state !== 'committed') { try { doc = await mobileRequest<NoteDocument>({ kind: 'read_note', file_id: intent.file_id }) } catch { /* The original receipt is still readable. */ } }
      if (active()) dialog.value = { kind: 'mutation', intent, doc, busy: false, error: '' }
    }
  } catch (error) { if (active()) listError.value = errorCode(error) }
}
function reviewDiscard() {
  const draft = session.state.view?.draft
  if (draft) dialog.value = { kind: 'discard', id: draft.id, edit_session: draft.edit_session, busy: false, error: '' }
}
async function discard() {
  const active = dialog.value
  if (!active || active.kind !== 'discard' || active.busy) return
  active.busy = true
  try {
    let missing = false
    try { await mobileRequest({ kind: 'draft_status', draft_id: active.id }) }
    catch (error) { if (errorCode(error) === 'DRAFT_NOT_FOUND') missing = true; else throw error }
    if (!missing) await mobileRequest({ kind: 'discard_draft', draft_id: active.id, edit_session: active.edit_session })
    if (dialog.value !== active) return
    dialog.value = null
    // The explicit discard must not flush its removed draft again.
    await session.closeAfterDiscard(active.id)
    await load()
  } catch (error) { if (dialog.value === active) active.error = errorCode(error) }
  finally { if (dialog.value === active) active.busy = false }
}
function pause() { if (document.visibilityState === 'hidden') void session.flush().catch(() => undefined) }
onMounted(() => { void boot(); window.addEventListener('resize', resizeWorkspace); document.addEventListener('visibilitychange', pause) })
onBeforeUnmount(() => { disposed = true; bootRevision++; listRevision++; inspectionRevision++; void Promise.resolve(backListener?.unregister()).catch(() => undefined); backListener = undefined; window.removeEventListener('resize', resizeWorkspace); document.removeEventListener('visibilitychange', pause); images.dispose(); session.dispose() })
</script>

<template>
  <WorkspaceFrame class="android-workspace" :compact="compact" :primary-open="primaryOpen" :secondary-open="secondaryOpen" :close-panels-label="copy.close" @close-panels="closePanels">
    <template #titlebar>
      <TitleBarFrame :compact="compact" :document-title="session.state.view ? basename(session.state.view.draft.path) : t('工作区', 'Workspace')" :dirty="dirty" :dark="resolvedTheme === 'dark'">
        <template #leading><button v-if="compact" class="icon-btn" type="button" :aria-label="t('主导航', 'Main navigation')" :aria-expanded="primaryOpen" @click="primaryOpen = !primaryOpen; secondaryOpen = false"><ControlIcon name="outline" /></button><button v-if="compact" class="icon-btn" type="button" :aria-label="t('文件', 'Files')" :aria-expanded="secondaryOpen" @click="secondaryOpen = !secondaryOpen; primaryOpen = false"><ControlIcon name="files" /></button></template>
        <button v-if="session.state.view" class="icon-btn" type="button" :aria-label="copy.back" :disabled="navigationBusy || session.state.saving || session.state.composing || actionBusy || imageBlocked || canvasFieldsPending" @click="leave"><ControlIcon name="panelClose" /></button>
        <button class="icon-btn" type="button" :aria-label="resolvedTheme === 'dark' ? t('切换浅色主题', 'Switch to light theme') : t('切换深色主题', 'Switch to dark theme')" @click="themeChoice = resolvedTheme === 'dark' ? 'light' : 'dark'"><ControlIcon :name="resolvedTheme === 'dark' ? 'sun' : 'moon'" /></button>
        <button class="icon-btn" type="button" :aria-label="t('设置', 'Settings')" @click="preferencesOpen = true"><ControlIcon name="toolbar" /></button>
      </TitleBarFrame>
    </template>
    <template #primary><PrimaryNavigation :groups="navigationGroups" :current-name="tab === 'notes' ? 'workspace' : tab" :expanded="compact || primaryExpanded" :system-open="false" @navigate="navigateSection" @toggle-expanded="togglePrimaryExpansion" @toggle-system="() => undefined" /></template>
    <template #secondary>
      <aside class="secondary-sidebar mobile-files">
        <header class="sidebar-header"><strong>{{ t('文件', 'Files') }}</strong><button class="icon-btn" type="button" :disabled="!ready" :aria-label="copy.newNote" @click="openPath('create', 'markdown')">+</button><button class="icon-btn" type="button" :aria-label="copy.close" @click="secondaryOpen = false"><ControlIcon name="panelClose" /></button></header>
        <form v-if="ready" class="file-search" @submit.prevent="search"><input v-model="query" :aria-label="copy.search" :placeholder="copy.search" /><button class="button-secondary">{{ copy.find }}</button></form>
        <div class="sidebar-content"><p v-if="!ready">{{ booting ? copy.checking : displayError(bootError) }}</p><template v-else><button v-if="directory" class="button-secondary" @click="enterDirectory(parent(directory))">../ {{ directory }}</button><FileTreeNode v-for="node in fileNodes" :key="node.id" :node="node" :active-path="session.state.view?.draft.path ?? null" @open="openTreeNode" /><p v-if="!fileNodes.length">{{ copy.noNotes }}</p><nav v-if="notePage.total > size" class="pagination"><button :disabled="offset === 0 || loading" @click="turnPage(-1)">{{ copy.previous }}</button><button :disabled="offset + size >= notePage.total || loading" @click="turnPage(1)">{{ copy.next }}</button></nav></template></div>
      </aside>
    </template>
    <p v-if="themeError" class="theme-notice" role="status">{{ copy.themeNativeFailed }}<button class="quiet" @click="retryTheme">{{ copy.retry }}</button></p>

    <main v-if="!ready" class="startup-card" :aria-busy="booting">
      <h1>{{ booting ? copy.checking : copy.failed }}</h1>
      <p v-if="bootError" role="alert">{{ displayError(bootError) }} <small>{{ bootError }}</small></p>
      <button :disabled="booting" @click="boot">{{ copy.retry }}</button>
    </main>

    <main v-else-if="session.state.view" class="editor-page" :aria-busy="session.state.loading">
      <nav class="editor-toolbar" :aria-label="copy.more">
        <button :disabled="navigationBusy || !dirty || session.state.saving || session.state.composing || Boolean(session.state.pendingSave) || session.state.unknownSave || imageBlocked || canvasFieldsPending" @click="save">{{ session.state.saving ? copy.saving : copy.save }}</button>
        <button class="quiet" :disabled="preview || session.state.composing || Boolean(dialog) || imageBlocked" @click="undo">{{ copy.undo }}</button>
        <button class="quiet" :disabled="preview || session.state.composing || Boolean(dialog) || imageBlocked" @click="redo">{{ copy.redo }}</button>
        <input ref="imageInput" class="sr-only" type="file" accept="image/png,image/jpeg,image/gif,image/webp" :aria-label="copy.addImage" @change="imageSelected" />
        <details ref="fileActions" class="file-actions"><summary>{{ copy.more }}</summary><div class="action-menu">
          <button v-if="!isCanvas" class="quiet" :disabled="session.state.composing" @click="togglePreview">{{ preview ? copy.editText : copy.preview }}</button>
          <button v-if="exportAllowed" class="quiet" :disabled="session.state.composing || session.state.saving || imageBlocked" @click="openExport">{{ copy.exportContent }}</button>
          <button v-if="!isCanvas" class="quiet" :disabled="preview || session.state.composing || session.state.saving || Boolean(session.state.pendingSave) || imageBlocked" @click="selectImage">{{ copy.addImage }}</button>
          <button class="quiet" :disabled="session.state.composing || canvasFieldsPending" @click="openPath('copy')">{{ copy.copy }}</button>
          <button class="quiet" :disabled="dirty || session.state.saving || actionBusy || imageBlocked" @click="reviewFile('rename')">{{ copy.rename }}</button>
          <button class="quiet danger" :disabled="dirty || session.state.saving || actionBusy || imageBlocked" @click="reviewFile('delete')">{{ copy.delete }}</button>
          <button class="quiet" :disabled="session.state.saving || session.state.composing" @click="reviewDiscard">{{ copy.discard }}</button>
        </div></details>
      </nav>
      <p class="save-status" role="status">{{ status }}</p>
      <section v-if="images.state.busy || images.state.error || images.state.pending || images.state.unknown" class="feedback" :aria-busy="images.state.busy">
        <p v-if="images.state.busy" role="status">{{ copy.importingImage }}</p>
        <p v-if="images.state.error" role="alert">{{ displayError(images.state.error) }} <small>{{ images.state.error }}</small></p>
        <template v-if="images.state.pending || images.state.unknown">
          <p>{{ copy.imagePending }}</p>
          <details v-if="images.state.pending"><summary>{{ copy.details }}</summary><code>{{ images.state.pending.operation_id }}</code><p>{{ images.state.pending.reference }}</p><code>{{ images.state.pending.hash }}</code><pre>{{ images.state.pending.content_after }}</pre></details>
          <div class="button-row"><button class="quiet" :disabled="images.state.busy" @click="images.check">{{ copy.check }}</button><button v-if="images.state.pending" :disabled="images.state.busy" @click="images.retry">{{ copy.retryOriginal }}</button><button v-if="images.state.pending" class="quiet" :disabled="images.state.busy" @click="images.cancel">{{ copy.cancelOriginal }}</button></div>
        </template>
      </section>
      <section v-if="session.state.error" class="feedback" role="alert">
        <p>{{ displayError(session.state.error) }}</p><small>{{ session.state.error }}</small>
        <div class="button-row"><button class="quiet" @click="runEditor(session.flush)">{{ copy.retryDraft }}</button><button class="quiet" @click="openPath('copy')">{{ copy.copy }}</button></div>
      </section>
      <section v-if="session.state.pendingSave || session.state.unknownSave" class="feedback">
        <p>{{ copy.resultPending }}</p>
        <details v-if="session.state.pendingSave"><summary>{{ copy.details }}</summary><code>{{ session.state.pendingSave.operation_id }}</code><pre>{{ session.state.pendingSave.content }}</pre></details>
        <div class="button-row">
          <button class="quiet" :disabled="session.state.saving" @click="runEditor(session.checkResult)">{{ copy.check }}</button>
          <button v-if="session.state.pendingSave" :disabled="session.state.saving" @click="runEditor(session.retryOriginal)">{{ copy.retryOriginal }}</button>
          <button v-if="session.state.pendingSave" class="quiet" :disabled="session.state.saving" @click="runEditor(session.cancelOriginal)">{{ copy.cancelOriginal }}</button>
        </div>
      </section>
      <details v-if="session.state.error === 'REVISION_CONFLICT' && current" class="feedback"><summary>{{ copy.currentVersion }}</summary><pre>{{ current.content }}</pre><button class="quiet" @click="openPath('copy')">{{ copy.copy }}</button></details>
      <p v-if="session.state.view.current_error" class="feedback">{{ copy.missing }}</p>
      <MobileCanvas v-if="isCanvas" :key="documentKey()" ref="canvas" :model-value="session.state.content" :language="language"
        :read-only="Boolean(dialog) || exportOpen || actionBusy || imageBlocked || navigationBusy" @update:model-value="session.edit" @composition-start="session.compositionStart"
        @composition-end="session.compositionEnd" @error="session.reportError" @image="selectImage" @validation="pending => canvasFieldsPending = pending" @link="href => dialog = { kind: 'link', href, busy: false, error: '' }" />
      <MobileEditor v-else v-show="!preview" :key="documentKey()" ref="editor" :model-value="session.state.content" :label="copy.body" :language="language"
        :read-only="Boolean(dialog) || exportOpen || actionBusy || imageBlocked || navigationBusy" @update:model-value="session.edit" @composition-start="session.compositionStart"
        @composition-end="session.compositionEnd" @error="session.reportError" />
      <MobilePreview v-if="preview" :content="session.state.content" :path="session.state.view.draft.path" :label="copy.preview"
        :image-unavailable="copy.imageUnavailable" :image-limit="copy.imageLimit" @link="href => dialog = { kind: 'link', href, busy: false, error: '' }" />
    </main>

    <WorkspaceLanding v-else-if="tab === 'notes' && !query && !directory" :notes="fileNodes.filter(node => node.type === 'file')" :error="listError || session.state.error" @new-note="openPath('create', 'markdown')" @open-note="path => { const entry = notePage.items.find(item => item.path === path); if (entry) openNote(entry) }">
      <template #actions><button class="button-primary" @click="openPath('create', 'markdown')">{{ copy.newNote }}</button><button class="button-secondary" @click="openPath('create', 'canvas')">{{ canvasCopy.newCanvas }}</button><button class="button-secondary" @click="secondaryOpen = true">{{ t('文件', 'Files') }}</button></template>
    </WorkspaceLanding>
    <main v-else class="notes-page">
      <p class="vault-status">{{ copy.ready }}</p>
      <div class="library-actions"><button @click="openPath('create', 'markdown')">{{ copy.newNote }}</button><button class="quiet" @click="openPath('create', 'canvas')">{{ canvasCopy.newCanvas }}</button><button class="quiet" :disabled="loading" @click="load">{{ copy.refresh }}</button><button v-if="exportAllowed" class="quiet" @click="openExport">{{ copy.exportContent }}</button></div>
      <nav class="library-tabs" :aria-label="copy.notes">
        <button class="quiet" :aria-pressed="tab === 'notes'" @click="selectTab('notes')">{{ copy.notes }}</button>
        <button class="quiet" :aria-pressed="tab === 'drafts'" @click="selectTab('drafts')">{{ copy.drafts }} <span>{{ number(draftPage.total) }}</span></button>
        <button class="quiet" :aria-pressed="tab === 'pending'" @click="selectTab('pending')">{{ copy.pending }} <span>{{ number(pendingPage.total) }}</span></button>
      </nav>
      <p v-if="notice" role="status">{{ notice }}</p>
      <p v-if="listError || session.state.error" class="feedback" role="alert">{{ displayError(listError || session.state.error) }}</p>
      <template v-if="tab === 'notes'">
        <form class="search-row" @submit.prevent="search"><input v-model="query" :aria-label="copy.search" :placeholder="copy.search" /><button class="quiet" :disabled="loading">{{ copy.find }}</button></form>
        <nav class="directory-row" :aria-label="copy.path"><button class="quiet" @click="enterDirectory('')">{{ copy.root }}</button><button v-if="directory" class="quiet" @click="enterDirectory(parent(directory))">../</button><span>{{ directory }}</span></nav>
        <button v-for="folder in notePage.directories" :key="folder" class="folder-row quiet" @click="enterDirectory(folder)">{{ basename(folder) }}/</button>
        <p v-if="notePage.unsearched_large_notes" class="muted">{{ copy.skipped }}</p>
        <button v-for="entry in notePage.items" :key="entry.file_id" class="note-row" data-note-row @click="openNote(entry)"><strong>{{ basename(entry.path) }}</strong><span>{{ entry.path }}</span></button>
        <p v-if="!loading && !notePage.items.length" class="empty-message">{{ copy.noNotes }}</p>
      </template>
      <template v-else-if="tab === 'drafts'">
        <button v-for="draft in draftPage.items" :key="draft.id" class="note-row" @click="openDraft(draft)"><strong>{{ draft.path }}</strong><span>{{ copy.draftKept }} · {{ number(draft.bytes) }} B</span></button>
        <p v-if="!loading && !draftPage.items.length" class="empty-message">{{ copy.noDrafts }}</p>
      </template>
      <template v-else>
        <button v-for="operation in pendingPage.items" :key="operation.operation_id" class="note-row" @click="inspect(operation)"><strong>{{ operation.path }}</strong><span>{{ copy.check }} · {{ operation.kind }}</span><code>{{ operation.operation_id }}</code></button>
        <p v-if="!loading && !pendingPage.items.length" class="empty-message">{{ copy.noPending }}</p>
      </template>
      <p v-if="loading" role="status">{{ copy.loading }}</p>
      <nav v-if="total > size" class="pagination" :aria-label="copy.notes"><button class="quiet" :disabled="offset === 0 || loading" @click="turnPage(-1)">{{ copy.previous }}</button><span>{{ number(offset + 1) }}–{{ number(Math.min(offset + size, total)) }} / {{ number(total) }}</span><button class="quiet" :disabled="offset + size >= total || loading" @click="turnPage(1)">{{ copy.next }}</button></nav>
      <footer><p>{{ copy.storage }}</p><span>OpenNexus {{ version }}</span></footer>
    </main>

    <AppDialog v-if="dialog" :label="copy.review" :dismissible="!dialog.busy" @close="cancelDialog">
      <section class="mobile-dialog modal-card" aria-labelledby="dialog-title">
        <label class="language dialog-language"><span class="sr-only">{{ copy.language }}</span><select v-model="language" :aria-label="copy.language"><option value="zh-CN">中文</option><option value="en">English</option></select></label><label class="language theme-choice"><span class="sr-only">{{ copy.theme }}</span><select v-model="themeChoice" :aria-label="copy.theme"><option value="system">{{ copy.themeSystem }}</option><option value="light">{{ copy.themeLight }}</option><option value="dark">{{ copy.themeDark }}</option><option value="sepia">{{ copy.themeSepia }}</option></select></label>
        <form v-if="dialog.kind === 'path'" @submit.prevent="submitPath">
          <h2 id="dialog-title">{{ dialog.action === 'create' ? (dialog.format === 'canvas' ? canvasCopy.newCanvas : copy.createTitle) : dialog.action === 'copy' ? copy.copyTitle : copy.renameTitle }}</h2>
          <label>{{ copy.path }}<input ref="pathInput" v-model="dialog.path" :disabled="dialog.busy" :aria-label="copy.path" autocomplete="off" autocapitalize="off" /></label>
          <p>{{ dialog.format === 'canvas' ? (language === 'zh-CN' ? '使用相对路径，例如 画布/想法.canvas。未填写 .canvas 时会自动添加。' : 'Use a relative path such as Canvas/ideas.canvas. The .canvas extension is added when omitted.') : copy.pathHelp }}</p><p v-if="dialog.action !== 'rename'">{{ copy.draftHelp }}</p>
          <details v-if="dialog.doc"><summary>{{ copy.currentVersion }}</summary><pre>{{ dialog.doc.content }}</pre></details>
          <p v-if="dialog.error" role="alert">{{ displayError(dialog.error) }}</p>
          <div class="button-row"><button type="button" class="quiet" :disabled="dialog.busy" @click="cancelDialog">{{ copy.cancel }}</button><button :disabled="dialog.busy || !dialog.path.trim()">{{ dialog.action === 'rename' ? copy.review : copy.create }}</button></div>
        </form>
        <template v-else-if="dialog.kind === 'mutation'">
          <h2 id="dialog-title">{{ dialog.intent.kind === 'delete' ? copy.deleteTitle : copy.renameTitle }}</h2>
          <p v-if="dialog.intent.kind === 'delete'">{{ copy.deleteHelp }}</p><p>{{ copy.frozen }}</p>
          <dl><dt>{{ copy.path }}</dt><dd>{{ dialog.intent.path }}</dd><template v-if="dialog.intent.destination"><dt>{{ copy.rename }}</dt><dd>{{ dialog.intent.destination }}</dd></template><dt>{{ copy.original }}</dt><dd><code>{{ dialog.intent.operation_id }}</code></dd><dt>{{ copy.savedVersion }}</dt><dd><code>{{ dialog.intent.expected }}</code></dd></dl>
          <p role="status">{{ dialog.intent.state === 'committed' ? copy.completed : dialog.intent.state === 'cancelled' ? copy.cancelled : dialog.intent.state === 'rejected' ? copy.rejected : copy.prepared }}</p>
          <details v-if="dialog.doc"><summary>{{ copy.currentVersion }}</summary><pre>{{ dialog.doc.content }}</pre></details>
          <p v-if="dialog.error" role="alert">{{ displayError(dialog.error) }} <small>{{ dialog.error }}</small></p>
          <div class="button-row"><button class="quiet" :disabled="dialog.busy" @click="cancelDialog">{{ dialog.intent.state === 'committed' ? copy.close : copy.cancel }}</button><button class="quiet" :disabled="dialog.busy" @click="checkMutation">{{ copy.check }}</button><button v-if="dialog.intent.state === 'prepared'" :disabled="dialog.busy" @click="applyMutation">{{ copy.confirm }}</button></div>
        </template>
        <template v-else-if="dialog.kind === 'link'">
          <h2 id="dialog-title">{{ copy.linkAddress }}</h2><pre>{{ dialog.href }}</pre>
          <p v-if="dialog.error" role="alert">{{ displayError(dialog.error) }}</p>
          <div class="button-row"><button class="quiet" :disabled="dialog.busy" @click="cancelDialog">{{ copy.close }}</button><button :disabled="dialog.busy || !canOpenLink(dialog.href)" @click="openReviewedLink">{{ copy.open }}</button></div>
        </template>
        <template v-else>
          <h2 id="dialog-title">{{ copy.discardTitle }}</h2><p>{{ copy.discardHelp }}</p>
          <p v-if="dialog.error" role="alert">{{ displayError(dialog.error) }}</p>
          <div class="button-row"><button class="quiet" :disabled="dialog.busy" @click="cancelDialog">{{ copy.cancel }}</button><button class="danger" :disabled="dialog.busy" @click="discard">{{ copy.discard }}</button></div>
        </template>
      </section>
    </AppDialog>
    <ExportPanel v-if="exportOpen" ref="exportPanel" :language="language" @language="value => language=value" @close="exportOpen=false" />
    <AppDialog v-if="preferencesOpen" :label="t('设置', 'Settings')" @close="preferencesOpen = false"><section class="mobile-dialog modal-card"><header class="preferences-title"><h2>{{ t('外观与语言', 'Appearance and language') }}</h2><button type="button" class="button-secondary" @click="preferencesOpen = false">{{ copy.close }}</button></header><label>{{ copy.language }}<select v-model="language" :aria-label="copy.language"><option value="zh-CN">中文</option><option value="en">English</option></select></label><label>{{ copy.theme }}<select v-model="themeChoice" :aria-label="copy.theme"><option value="system">{{ copy.themeSystem }}</option><option value="light">{{ copy.themeLight }}</option><option value="dark">{{ copy.themeDark }}</option><option value="sepia">{{ copy.themeSepia }}</option></select></label></section></AppDialog>
  </WorkspaceFrame>
</template>
