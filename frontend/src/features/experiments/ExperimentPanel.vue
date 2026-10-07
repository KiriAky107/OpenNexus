<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useWorkspaceStore } from '@/stores/workspace'
import { useEditorStore } from '@/stores/editor'
import { useExperimentPane } from '@/composables/useExperimentPane'
import * as experiments from '@/services/experimentService'
import type { HistoryItem, ImportHistoryItem, ImportRecord, Limits, LiveRun, Origin, OutputPreview, Page, RunRecord, RunRequest, SelectedFile, SourcePreview, Status, TextPreview } from '@/services/experimentService'
import { loadWorkspaceImage } from '@/services/workspaceService'
import { t } from '@/i18n'
import ExperimentLog from './ExperimentLog.vue'

const workspace = useWorkspaceStore(), editor = useEditorStore(), pane = useExperimentPane()
const status = ref<Status | null>(null), candidates = ref<SelectedFile[]>([]), entry = ref(''), inputs = ref<string[]>([]), limits = ref<Limits | null>(null)
const history = ref<HistoryItem[]>([]), historyCursor = ref<experiments.HistoryCursor | null>(null)
const record = ref<RunRecord | null>(null), live = ref<LiveRun | null>(null), source = ref<SourcePreview | null>(null), output = ref<OutputPreview | null>(null)
const importRecord = ref<ImportRecord | null>(null), imports = ref<ImportHistoryItem[]>([]), importCursor = ref<experiments.HistoryCursor | null>(null)
const allImports = ref<ImportHistoryItem[]>([]), allImportCursor = ref<experiments.HistoryCursor | null>(null)
const selectedOutputs = ref<string[]>([]), destinations = ref<Record<string, string>>({}), origins = ref<Origin[]>([]), originCursor = ref<experiments.HistoryCursor | null>(null), originFile = ref('')
const target = ref<{ target: SelectedFile; bytes: number; preview: TextPreview | null } | null>(null)
const busy = ref(false), error = ref(''), pollingError = ref(''), imageUrl = ref('')
let epoch = 0, selectionVersion = 0, previewVersion = 0, pollTimer: ReturnType<typeof setTimeout> | undefined, pollRunning = false, failures = 0, stopImport = false
let originVersion = 0
const pendingRun = ref<RunRequest | null>(null)
const cleanupReview = ref<experiments.CleanupReview | null>(null)
let pendingImport: experiments.ImportRequest | null = null
const scripts = computed(() => candidates.value.filter(f => /\.py$/i.test(f.path)))
const inputOptions = computed(() => candidates.value.filter(f => f.path !== entry.value))
const active = computed(() => Boolean(record.value && ['starting', 'running', 'cancel_requested'].includes(record.value.state)))
const files = computed(() => record.value?.result?.outputs?.status === 'collected' ? record.value.result.outputs.summary.files : [])
const logs = computed(() => live.value?.logs ?? record.value?.result?.logs)
const runId = computed(() => record.value?.summary.request.operation_id ?? '')
const importRunId = computed(() => importRecord.value?.plan.request.run_id ?? runId.value)
function current(v: string, e: number) { return epoch === e && workspace.vaultId === v }
function api<T>(kind: string, args: Record<string, unknown> = {}, vault = workspace.vaultId) { return experiments.request<T>(vault, { kind, ...args }) }
function releaseImage() { if (imageUrl.value) URL.revokeObjectURL(imageUrl.value); imageUrl.value = '' }
function stopPolling() { clearTimeout(pollTimer); pollTimer = undefined }
function resetSelection() { selectionVersion++; previewVersion++; record.value = null; live.value = null; source.value = null; output.value = null; target.value = null; importRecord.value = null; imports.value = []; importCursor.value = null; selectedOutputs.value = []; destinations.value = {}; pendingImport = null; stopImport = true; stopPolling(); releaseImage() }
async function task(fn: (vault: string, version: number) => Promise<void>) {
  if (busy.value) return
  const vault = workspace.vaultId, version = epoch
  busy.value = true; error.value = ''
  try { await fn(vault, version) }
  catch (reason) { if (current(vault, version)) error.value = experiments.errorText(reason) }
  finally { if (current(vault, version)) busy.value = false }
}
async function refreshHistory(vault: string, version: number, more = false) {
  const page = await api<Page<HistoryItem>>('history', { limit: 20, cursor: more ? historyCursor.value : null }, vault)
  if (!current(vault, version)) return
  history.value = more ? [...history.value, ...page.items] : page.items; historyCursor.value = page.next_cursor
}
async function refreshAllImports(vault: string, version: number, more = false) {
  const page = await api<Page<ImportHistoryItem>>('all_import_history', { limit: 10, cursor: more ? allImportCursor.value : null }, vault)
  if (!current(vault, version)) return
  allImports.value = more ? [...allImports.value, ...page.items] : page.items; allImportCursor.value = page.next_cursor
}
async function initialize() {
  cleanupReview.value = null
  epoch++; resetSelection(); busy.value = false; status.value = null; candidates.value = []; history.value = []; allImports.value = []; allImportCursor.value = null; origins.value = []; originCursor.value = null; originFile.value = ''; pendingRun.value = null; pollingError.value = ''; failures = 0; inputs.value = []
  await task(async (vault, version) => {
    const [capability, available] = await Promise.all([api<Status>('status', {}, vault), experiments.files(), refreshHistory(vault, version), refreshAllImports(vault, version)])
    if (!current(vault, version)) return
    status.value = capability; candidates.value = available; limits.value = { ...capability.limits }
    const selected = editor.currentFilePath?.replace(/^\//, '')
    entry.value = available.find(f => f.path === selected && /\.py$/i.test(f.path))?.path ?? scripts.value[0]?.path ?? ''
  })
}
async function reviewCleanup() {
  cleanupReview.value = null
  await task(async (vault, version) => {
    const review = await api<experiments.CleanupReview | null>('cleanup_review', {}, vault)
    if (current(vault, version)) cleanupReview.value = review
  })
}
async function recoverCleanup() {
  const review = cleanupReview.value
  if (!review) return
  await task(async (vault, version) => {
    try {
      await api('recover_cleanup', { fingerprint: review.fingerprint }, vault)
      const capability = await api<Status>('status', {}, vault)
      if (current(vault, version)) status.value = capability
    } finally {
      if (current(vault, version)) cleanupReview.value = null
    }
  })
}
watch(() => workspace.vaultId, initialize, { immediate: true })
watch(entry, () => { inputs.value = inputs.value.filter(p => p !== entry.value); pendingRun.value = null })
watch(() => editor.currentFilePath, path => {
  const relative = path?.replace(/^\//, '')
  if (relative && candidates.value.some(f => f.path === relative) && /\.py$/i.test(relative)) entry.value = relative
})
watch(() => workspace.activeFilePath, () => { originVersion++; origins.value = []; originCursor.value = null; originFile.value = '' })
async function refreshImports(vault: string, version: number, operation: string, more = false) {
  const page = await api<Page<ImportHistoryItem>>('import_history', { run_id: operation, limit: 10, cursor: more ? importCursor.value : null }, vault)
  if (!current(vault, version) || (runId.value !== operation && importRunId.value !== operation)) return
  imports.value = more ? [...imports.value, ...page.items] : page.items; importCursor.value = page.next_cursor
}
function schedulePoll() {
  stopPolling()
  if (!active.value) return
  pollTimer = setTimeout(poll, Math.min(1000 * 2 ** failures, 8000))
}
async function poll() {
  if (pollRunning || !active.value) { schedulePoll(); return }
  pollRunning = true
  const vault = workspace.vaultId, version = epoch, operation = runId.value, selection = selectionVersion
  try {
    const [updated, snapshot] = await Promise.all([api<RunRecord | null>('record', { operation_id: operation }, vault), api<LiveRun | null>('live', { operation_id: operation }, vault)])
    if (!current(vault, version) || selection !== selectionVersion) return
    if (!updated) throw new Error('EXPERIMENT_OPERATION_NOT_FOUND')
    record.value = updated; live.value = snapshot; failures = 0; pollingError.value = ''
    if (!active.value) await refreshHistory(vault, version)
  } catch (reason) { if (current(vault, version) && selection === selectionVersion) { failures++; pollingError.value = experiments.errorText(reason) } }
  finally { pollRunning = false; schedulePoll() }
}
async function acceptRecord(value: RunRecord, vault: string, version: number) {
  if (!current(vault, version)) return
  resetSelection(); record.value = value
  destinations.value = Object.fromEntries(files.value.map(f => [f.path, experiments.defaultDestination(f, runId.value)]))
  const selection = selectionVersion
  await Promise.all([
    api<SourcePreview>('source_preview', { operation_id: runId.value, path: value.summary.request.entry.path }, vault).then(p => { if (current(vault, version) && selection === selectionVersion) source.value = p }),
    refreshImports(vault, version, runId.value),
  ])
  schedulePoll()
}
function viewRun(operation: string) { return task(async (vault, version) => { const value = await api<RunRecord | null>('record', { operation_id: operation }, vault); if (!value) throw new Error('EXPERIMENT_OPERATION_NOT_FOUND'); await acceptRecord(value, vault, version) }) }
function prepareRun() { return task(async (vault, version) => {
  if (!status.value?.available || !limits.value) return
  if (!pendingRun.value) {
    const path = editor.currentFilePath, content = editor.content
    if (path && [entry.value, ...inputs.value].includes(path.replace(/^\//, ''))) {
      if (['conflict', 'external_changed'].includes(editor.saveStatus)) throw new Error('EXPERIMENT_INPUT_CHANGED')
      await editor.save()
      if (!current(vault, version) || path !== editor.currentFilePath || content !== editor.content) return
      if (['dirty', 'saving', 'save_failed', 'conflict', 'external_changed'].includes(editor.saveStatus)) throw new Error('EXPERIMENT_INPUT_CHANGED')
    }
    const fresh = await experiments.files()
    if (!current(vault, version)) return
    pendingRun.value = experiments.runRequest(vault, entry.value, inputs.value, fresh, status.value.runtime!.runtime_id, limits.value)
  }
  const value = await api<RunRecord>('prepare', { request: pendingRun.value }, vault)
  await acceptRecord(value, vault, version)
  if (current(vault, version)) { pendingRun.value = null; await refreshHistory(vault, version) }
}) }
function confirmRun(kind: 'confirm_run' | 'start_approved') { return task(async (vault, version) => {
  const selected = record.value
  if (!selected) return
  const value = await api<RunRecord>(kind, { operation_id: runId.value, ...(kind === 'confirm_run' ? { fingerprint: selected.summary.fingerprint } : {}) }, vault)
  if (!current(vault, version)) return
  record.value = value; schedulePoll(); await refreshHistory(vault, version)
}) }
async function cancelRun() {
  const vault = workspace.vaultId, version = epoch, operation = runId.value
  try { const value = await api<RunRecord>('cancel_run', { operation_id: operation }, vault); if (current(vault, version) && runId.value === operation) { record.value = value; schedulePoll() } }
  catch (reason) { if (current(vault, version)) error.value = experiments.errorText(reason) }
}
function sourcePreview(path: string) { return task(async (vault, version) => {
  const selection = selectionVersion, operation = runId.value
  const value = await api<SourcePreview>('source_preview', { operation_id: operation, path }, vault)
  if (current(vault, version) && selection === selectionVersion) source.value = value
}) }
function outputPreview(path: string, preparedTarget = false) { return task(async (vault, version) => {
  const sequence = ++previewVersion, operation = runId.value
  const value = await api<OutputPreview>('output_preview', { operation_id: operation, path }, vault)
  if (!current(vault, version) || sequence !== previewVersion) return
  output.value = value; target.value = null
  if (preparedTarget && importRecord.value) {
    const prior = await api<typeof target.value>('import_target_preview', { operation_id: importRecord.value.plan.request.operation_id, output_path: path }, vault)
    if (current(vault, version) && sequence === previewVersion) target.value = prior
  }
}) }
function changeSelection() { importRecord.value = null; target.value = null; pendingImport = null }
function prepareImport() { return task(async (vault, version) => {
  if (!pendingImport) pendingImport = { vault_id: vault, operation_id: crypto.randomUUID(), run_id: runId.value, selections: selectedOutputs.value.map(path => ({ output_path: path, destination: destinations.value[path] ?? '' })) }
  const value = await api<ImportRecord>('import_prepare', { request: pendingImport }, vault)
  if (!current(vault, version)) return
  importRecord.value = value; pendingImport = null; await Promise.all([refreshImports(vault, version, runId.value), refreshAllImports(vault, version)])
}) }
function viewImport(operation: string, globalHistory = false) { return task(async (vault, version) => {
  const value = await api<ImportRecord | null>('import_record', { operation_id: operation }, vault)
  if (!current(vault, version) || !value) return
  if (globalHistory) {
    const parent = await api<RunRecord | null>('record', { operation_id: value.plan.request.run_id }, vault).catch(reason => {
      if ((reason instanceof Error ? reason.message : String(reason)) === 'EXPERIMENT_RECORD_FORGOTTEN') return null
      throw reason
    })
    if (!current(vault, version)) return
    if (parent) await acceptRecord(parent, vault, version)
    else resetSelection()
  }
  if (!current(vault, version)) return
  importRecord.value = value; pendingImport = null; selectedOutputs.value = value.plan.request.selections.map(s => s.output_path)
  destinations.value = Object.fromEntries(value.plan.request.selections.map(s => [s.output_path, s.destination])); target.value = null
}) }
async function advanceImport(vault: string, version: number) {
  stopImport = false
  while (current(vault, version) && !stopImport && importRecord.value?.state === 'approved') {
    const operation = importRecord.value.plan.request.operation_id
    const value = await api<ImportRecord>('import_next', { operation_id: operation }, vault)
    if (!current(vault, version) || importRecord.value?.plan.request.operation_id !== operation) return
    if (stopImport) {
      const latest = await api<ImportRecord | null>('import_record', { operation_id: operation }, vault)
      if (current(vault, version) && importRecord.value?.plan.request.operation_id === operation && latest && latest.state !== 'approved') importRecord.value = latest
      break
    }
    const previous = importRecord.value.items.map(i => i.state).join(',')
    importRecord.value = value
    if (value.state === 'approved' && value.items.map(i => i.state).join(',') === previous) break
  }
  if (!current(vault, version)) return
  await Promise.all([workspace.refreshFileTree(), refreshImports(vault, version, importRunId.value), refreshAllImports(vault, version)])
}
function confirmImport() { return task(async (vault, version) => {
  const value = importRecord.value
  if (!value) return
  const confirmed = await api<ImportRecord>('confirm_import', { operation_id: value.plan.request.operation_id, fingerprint: value.fingerprint }, vault)
  if (!current(vault, version)) return
  importRecord.value = confirmed
  if (confirmed.state === 'approved') await advanceImport(vault, version)
  else await Promise.all([refreshImports(vault, version, importRunId.value), refreshAllImports(vault, version)])
}) }
async function cancelImport() {
  stopImport = true
  const value = importRecord.value, vault = workspace.vaultId, version = epoch
  if (!value) return
  try { const result = await api<ImportRecord>('cancel_import', { operation_id: value.plan.request.operation_id, fingerprint: value.fingerprint }, vault); if (current(vault, version) && importRecord.value?.plan.request.operation_id === value.plan.request.operation_id) { importRecord.value = result; await Promise.all([refreshImports(vault, version, importRunId.value), refreshAllImports(vault, version)]) } }
  catch (reason) { if (current(vault, version)) error.value = experiments.errorText(reason) }
}
function forgetImport() { return task(async (vault, version) => {
  const value = importRecord.value
  if (!value) return
  await api('forget_import', { operation_id: value.plan.request.operation_id, fingerprint: value.fingerprint }, vault)
  if (!current(vault, version)) return
  importRecord.value = null; await refreshAllImports(vault, version)
}) }
function forgetRun() { return task(async (vault, version) => {
  const selected = record.value
  if (!selected) return
  await api('forget_run', { operation_id: runId.value, fingerprint: selected.summary.fingerprint }, vault)
  if (current(vault, version)) { resetSelection(); await refreshHistory(vault, version) }
}) }
function download(path: string, isSource: boolean) { return task(async (vault, version) => {
  const bytes = await experiments.originalBytes(vault, runId.value, path, isSource)
  if (!current(vault, version)) return
  const url = URL.createObjectURL(new Blob([bytes as Uint8Array<ArrayBuffer>], { type: 'application/octet-stream' }))
  const link = document.createElement('a'); link.href = url; link.download = path.split('/').at(-1) ?? 'output'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}) }
function openFile(file_id: string) { return task(async (vault, version) => {
  const path = await api<string>('file_path', { file_id }, vault)
  if (!current(vault, version)) return
  if (/\.png$/i.test(path)) {
    const blob = await loadWorkspaceImage(path)
    if (!current(vault, version)) return
    releaseImage(); imageUrl.value = URL.createObjectURL(blob)
  } else { await editor.loadFile(`/${path}`) }
}) }
function showOrigins(more = false) { return task(async (vault, version) => {
  const sequence = ++originVersion
  const file_id = more ? originFile.value : workspace.activeFile?.id
  if (!file_id) return
  const page = await api<Page<Origin>>('origins', { file_id, limit: 10, cursor: more ? originCursor.value : null }, vault)
  if (!current(vault, version) || sequence !== originVersion) return
  originFile.value = file_id; origins.value = more ? [...origins.value, ...page.items] : page.items; originCursor.value = page.next_cursor
}) }
onBeforeUnmount(() => { epoch++; stopImport = true; stopPolling(); releaseImage() })
</script>

<template>
  <aside id="experiment-panel" class="experiment-panel" :aria-label="t('实验运行与成果', 'Experiment runs and outputs')">
    <header class="panel-header"><h2>{{ t('实验', 'Experiments') }}</h2><div><button :disabled="busy" @click="initialize">{{ t('刷新', 'Refresh') }}</button><button :aria-label="t('关闭实验面板', 'Close experiment panel')" @click="pane.close">×</button></div></header>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="pollingError" class="notice" role="status">{{ t('暂时无法读取运行状态，将重试：', 'Run status is temporarily unavailable; retrying: ') }}{{ pollingError }}</p>
    <div class="panel-scroll" :aria-busy="busy">
      <section class="run-form">
        <p v-if="status && !status.available" class="notice" role="status">{{ experiments.errorText(status.error ?? '') }}</p>
        <section v-if="status?.cleanup" class="cleanup-review notice" :aria-label="t('待清理的实验资源', 'Pending experiment cleanup')">
          <p>{{ t('上次实验的临时资源需要核对。已保存的成果和知识库文件会保留。', 'Temporary resources from a previous experiment need verification. Saved outputs and vault files are preserved.') }}</p>
          <p v-if="status.cleanup.error" class="subtle">{{ experiments.errorText(status.cleanup.error) }}</p>
          <p v-if="cleanupReview">{{ t('临时文件和文件夹：', 'Temporary files and folders: ') }}{{ cleanupReview.temporary_objects }} · {{ t('运行环境访问权限：', 'Runtime access permissions: ') }}{{ cleanupReview.borrowed_objects }}</p>
          <p v-if="cleanupReview" class="subtle">{{ t('确认后会删除临时容器内尚未导入的文件；已保留的成果不受影响。', 'Confirmation removes unimported files in the temporary container; retained outputs are preserved.') }}</p>
          <button data-action="review-cleanup" :disabled="busy" @click="reviewCleanup">{{ t('核对临时资源', 'Review temporary resources') }}</button>
          <button v-if="cleanupReview" data-action="recover-cleanup" :disabled="busy" @click="recoverCleanup">{{ t('确认并清理', 'Confirm cleanup') }}</button>
        </section>
        <label>{{ t('Python 源文件', 'Python source') }}<select v-model="entry" :disabled="busy"><option value="">{{ t('选择源文件', 'Select source') }}</option><option v-for="file in scripts" :key="file.file_id" :value="file.path">{{ file.path }}</option></select></label>
        <details><summary>{{ t('输入文件与资源限制', 'Inputs and limits') }}</summary>
          <fieldset :disabled="busy"><legend>{{ t('选择只读输入（最多31份）', 'Select read-only inputs (up to 31)') }}</legend><label v-for="file in inputOptions" :key="file.file_id" class="check"><input v-model="inputs" type="checkbox" :value="file.path" :disabled="inputs.length >= 31 && !inputs.includes(file.path)" />{{ file.path }}</label></fieldset>
          <div v-if="limits" class="limits"><label>{{ t('墙钟秒数', 'Wall seconds') }}<input v-model.number="limits.wall_seconds" type="number" min="1" max="120" /></label><label>{{ t('CPU秒数', 'CPU seconds') }}<input v-model.number="limits.cpu_seconds" type="number" min="1" max="60" /></label><label>{{ t('内存 MiB', 'Memory MiB') }}<input v-model.number="limits.memory_mib" type="number" min="64" max="512" /></label><label>{{ t('进程数', 'Processes') }}<input v-model.number="limits.processes" type="number" min="1" max="8" /></label><label>{{ t('磁盘占用 MiB', 'Disk usage MiB') }}<input v-model.number="limits.disk_mib" type="number" min="8" max="128" /></label><label>{{ t('成果 MiB', 'Output MiB') }}<input v-model.number="limits.output_mib" type="number" min="1" :max="limits.disk_mib" /></label><label>{{ t('日志 KiB', 'Logs KiB') }}<input v-model.number="limits.log_kib" type="number" min="16" max="1024" /></label><label>{{ t('目录对象数', 'Directory objects') }}<input v-model.number="limits.objects" type="number" min="32" max="2048" /></label></div>
          <p class="subtle">{{ t('网络关闭；输入只读。磁盘采用超限监测，运行与成果导入分别确认。', 'Network disabled; inputs read-only. Disk use is monitored. Running and importing require separate confirmation.') }}</p>
        </details>
        <button class="button-primary" data-action="prepare-run" :disabled="busy || !entry || !status?.available || active" @click="prepareRun">{{ pendingRun ? t('核对上次准备请求', 'Reconcile prepared request') : t('准备运行', 'Prepare run') }}</button>
        <button v-if="pendingRun" :disabled="busy" @click="pendingRun = null">{{ t('重新选择源版本', 'Choose a new source version') }}</button>
      </section>
      <details open class="history"><summary>{{ t('运行历史', 'Run history') }} · {{ history.length }}</summary><button v-for="item in history" :key="item.operation_id" class="history-row" :disabled="busy" @click="viewRun(item.operation_id)"><strong>{{ item.entry.path.split('/').at(-1) }}</strong><span>{{ experiments.stateText(item.state) }} · {{ new Date(item.created_ms).toLocaleString() }}</span></button><button v-if="historyCursor" :disabled="busy" @click="task((v,e) => refreshHistory(v,e,true))">{{ t('更多运行', 'More runs') }}</button></details>
      <details v-if="allImports.length" class="all-imports"><summary>{{ t('导入历史（包含已清理运行）', 'Import history, including cleared runs') }}</summary><button v-for="item in allImports" :key="item.operation_id" class="history-row" :disabled="busy" @click="viewImport(item.operation_id,true)">{{ item.entry.path.split('/').at(-1) }} · {{ experiments.stateText(item.state) }} · {{ item.committed }}/{{ item.files }}</button><button v-if="allImportCursor" :disabled="busy" @click="task((v,e) => refreshAllImports(v,e,true))">{{ t('更多导入', 'More imports') }}</button></details>
      <section v-if="record" class="run-detail">
        <div class="section-heading"><h3>{{ experiments.stateText(record.state) }}</h3><button v-if="active" class="button-secondary" data-action="stop-run" @click="cancelRun">{{ t('停止', 'Stop') }}</button></div>
        <p class="path">{{ record.summary.request.entry.path }}</p>
        <p v-if="record.error || record.result?.error" class="notice error">{{ experiments.errorText(record.result?.error ?? record.error) }}</p>
        <button v-if="record.state === 'awaiting_confirmation'" class="button-primary" data-action="confirm-run" :disabled="busy || !status?.available" @click="confirmRun('confirm_run')">{{ t('确认并运行…', 'Confirm and run…') }}</button>
        <button v-if="record.state === 'approved'" class="button-primary" :disabled="busy || !status?.available" @click="confirmRun('start_approved')">{{ t('继续已批准运行', 'Continue approved run') }}</button>
        <p v-if="record.result">{{ t('退出码', 'Exit code') }}: {{ record.result.exit_code ?? '—' }} · {{ record.result.elapsed_ms }} ms</p><p v-else-if="live">{{ live.elapsed_ms }} ms</p>
        <details><summary>{{ t('运行依据', 'Run evidence') }}</summary><p>{{ record.summary.request.runtime_id }} · {{ record.summary.total_bytes }} B</p><p>{{ t('批准时间', 'Approved at') }}: {{ record.approved_ms ? new Date(record.approved_ms).toLocaleString() : '—' }}</p><p class="hash">{{ record.summary.request.entry.hash }}</p><p class="hash">{{ record.summary.fingerprint }}</p><p>{{ t('内存峰值 / CPU / 磁盘', 'Peak memory / CPU / Disk') }}: {{ record.result?.peak_memory_bytes ?? '—' }} B / {{ record.result?.user_cpu_ticks ?? '—' }} ×100 ns / {{ record.result?.final_disk_bytes ?? '—' }} B</p></details>
        <details open><summary>{{ t('本次源版本与输入', 'Source version and inputs') }}</summary><div class="file-buttons"><button :disabled="busy" @click="sourcePreview(record.summary.request.entry.path)">{{ record.summary.request.entry.path.split('/').at(-1) }}</button><button v-for="input in record.summary.request.inputs" :key="input.file_id" :disabled="busy" @click="sourcePreview(input.path)">{{ input.path.split('/').at(-1) }}</button></div><template v-if="source"><p class="subtle">{{ source.source.path }} · r{{ source.source.revision }} · {{ source.bytes }} B</p><p v-if="source.preview.truncated" class="notice">{{ t('源版本预览已截断；下载可取得完整原始文件。', 'Source preview is truncated. Download the complete original file.') }}</p><pre class="text-preview">{{ source.preview.text }}</pre><div class="file-buttons"><button :disabled="busy" @click="download(source.source.path,true)">{{ t('下载完整源版本', 'Download original source') }}</button><button v-if="source.current_path" :disabled="busy" @click="openFile(source.source.file_id)">{{ t('打开当前文件', 'Open current file') }}</button></div></template></details>
        <details v-if="logs" open><summary>{{ t('运行日志', 'Run logs') }}</summary><ExperimentLog v-for="(stream,name) in logs" :key="`${runId}:${name}`" :name="name" :stream="stream" /></details>
        <section v-if="files.length" class="outputs"><h3>{{ t('成果', 'Outputs') }}</h3><p class="subtle">{{ t('先预览，再选择目标路径。', 'Preview outputs, then choose destination paths.') }}</p><div v-for="file in files" :key="file.path" class="output-item"><label class="check"><input v-model="selectedOutputs" type="checkbox" :value="file.path" :disabled="busy || importRecord?.state === 'approved'" @change="changeSelection" />{{ file.path }} · {{ file.bytes }} B</label><div class="file-buttons"><button :disabled="busy" @click="outputPreview(file.path)">{{ t('预览', 'Preview') }}</button><button :disabled="busy" @click="download(file.path,false)">{{ t('下载原文件', 'Download original') }}</button></div><label v-if="selectedOutputs.includes(file.path)">{{ t('知识库目标路径', 'Destination in vault') }}<input v-model="destinations[file.path]" :disabled="busy || importRecord?.state === 'approved'" @input="changeSelection" /></label></div><button class="button-secondary" data-action="prepare-import" :disabled="busy || !selectedOutputs.length || importRecord?.state === 'approved'" @click="prepareImport">{{ t('准备导入', 'Prepare import') }}</button></section>
        <p v-if="record.result?.outputs?.status === 'rejected'" class="notice error">{{ experiments.errorText(record.result.outputs.error) }}</p>
        <details v-if="record.result?.outputs?.status === 'collected' && record.result.outputs.summary.skipped.length"><summary>{{ t('未采集的文件', 'Skipped outputs') }}</summary><p v-for="file in record.result.outputs.summary.skipped" :key="file.path">{{ file.path }} · {{ file.bytes }} B · {{ t('不支持的类型', 'Unsupported type') }}</p></details>
        <section v-if="output" class="output-preview"><h3>{{ output.manifest.path }}</h3><p class="hash">SHA-256 {{ output.manifest.sha256 }}</p><template v-if="output.content.format === 'text'"><p v-if="output.content.preview.truncated" class="notice">{{ t('成果预览已截断，原文件未截断。', 'Output preview is truncated. The original file is unchanged.') }}</p><pre class="text-preview">{{ output.content.preview.text }}</pre></template><template v-else-if="output.content.format === 'csv'"><p v-if="output.content.truncated" class="notice">{{ t('表格预览已截断，原文件未截断。', 'Table preview is truncated. The original file is unchanged.') }}</p><div class="table-preview" tabindex="0"><table><tbody><tr v-for="(row,r) in output.content.rows" :key="r"><td v-for="(cell,c) in row" :key="c">{{ cell }}</td></tr></tbody></table></div></template><img v-else :src="`data:image/png;base64,${output.content.content_base64}`" :width="output.content.width" :height="output.content.height" :alt="output.manifest.path" /><template v-if="target"><h4>{{ t('将被替换的当前内容', 'Current content to replace') }}</h4><p>{{ target.target.path }} · {{ target.bytes }} B · r{{ target.target.revision }}</p><p class="hash">{{ target.target.hash || t('新文件', 'New file') }}</p><pre v-if="target.preview" class="text-preview">{{ target.preview.text }}</pre><p v-if="target.preview?.truncated" class="notice">{{ t('当前内容预览已截断。', 'Current content preview is truncated.') }}</p></template></section>
        <details v-if="imports.length"><summary>{{ t('本次导入记录', 'Imports from this run') }}</summary><button v-for="item in imports" :key="item.operation_id" class="history-row" :disabled="busy" @click="viewImport(item.operation_id)">{{ experiments.stateText(item.state) }} · {{ item.committed }}/{{ item.files }} · {{ new Date(item.created_ms).toLocaleString() }}</button><button v-if="importCursor" :disabled="busy" @click="task((v,e) => refreshImports(v,e,runId,true))">{{ t('更多导入记录', 'More imports') }}</button></details>
        <button v-if="!['awaiting_confirmation','approved','starting','running','cancel_requested'].includes(record.state)" :disabled="busy" @click="forgetRun">{{ t('清理本次运行记录…', 'Clear this run record…') }}</button>
      </section>
        <section v-if="importRecord" class="import-plan"><p v-if="!record" class="notice">{{ t('运行记录已清理，导入依据与逐项回执仍保留。', 'The run record was cleared. Import evidence and individual receipts are retained.') }}</p><p>{{ importRecord.plan.source.request.entry.path }} · r{{ importRecord.plan.source.request.entry.revision }}</p><p class="hash">{{ importRecord.plan.source.request.entry.hash }}</p><h3>{{ t('导入核对', 'Import review') }} · {{ experiments.stateText(importRecord.state) }}</h3><p class="notice">{{ t('每个文件单独提交，多文件可能部分成功。取消保留已导入文件。', 'Each file commits separately; a batch may partially succeed. Cancelling keeps imported files.') }}</p><div v-for="(item,index) in importRecord.plan.items" :key="item.write_id" class="output-item"><p>{{ item.output.path }} → {{ item.target.path }}</p><p>{{ item.target.hash ? t('覆盖现有文件', 'Replace existing file') : t('新文件', 'New file') }} · r{{ item.target.revision }} · {{ experiments.stateText(importRecord.items[index]?.state ?? '') }}</p><p v-if="importRecord.items[index]?.error" class="error">{{ experiments.errorText(importRecord.items[index]?.error) }}</p><button v-if="record && importRecord.state === 'awaiting_confirmation'" :disabled="busy" @click="outputPreview(item.output.path,true)">{{ t('核对内容差异', 'Review contents') }}</button><button v-if="importRecord.items[index]?.entry" :disabled="busy" @click="openFile(importRecord.items[index]!.entry!.file_id)">{{ t('打开成果', 'Open artifact') }}</button></div><button v-if="importRecord.state === 'awaiting_confirmation'" class="button-primary" data-action="confirm-import" :disabled="busy || !record" @click="confirmImport">{{ t('确认导入…', 'Confirm import…') }}</button><button v-if="importRecord.state === 'approved'" class="button-primary" :disabled="busy" @click="task(advanceImport)">{{ t('继续未完成项', 'Continue pending items') }}</button><button v-if="['approved','awaiting_confirmation'].includes(importRecord.state)" class="button-secondary" @click="cancelImport">{{ t('取消导入', 'Cancel import') }}</button><button v-if="!['awaiting_confirmation','approved'].includes(importRecord.state)" :disabled="busy" @click="forgetImport">{{ t('清理这次来源记录…', 'Clear this import provenance…') }}</button></section>
      <section class="origins"><button :disabled="busy || !workspace.activeFile" @click="showOrigins(false)">{{ t('查看当前文件的成果来源', 'View artifact origins for this file') }}</button><article v-for="origin in origins" :key="origin.import_id"><p>{{ origin.output.path }} → {{ origin.current_path ?? t('文件已删除', 'File deleted') }}</p><p>{{ t('来源', 'Source') }}: {{ origin.source.request.entry.path }} · r{{ origin.source.request.entry.revision }}</p><p class="hash">{{ origin.source.request.entry.hash }}</p><p>{{ origin.source.request.runtime_id }} · {{ origin.execution.outcome }} · {{ origin.execution.exit_code ?? '—' }} · {{ origin.execution.elapsed_ms }} ms</p><button :disabled="busy" @click="viewRun(origin.run_id)">{{ t('查看运行记录', 'View run record') }}</button><button v-if="origin.current_path" :disabled="busy" @click="openFile(origin.imported.file_id)">{{ t('打开成果', 'Open artifact') }}</button></article><button v-if="originCursor" :disabled="busy" @click="showOrigins(true)">{{ t('更多来源记录', 'More origins') }}</button></section>
      <section v-if="imageUrl"><button @click="releaseImage">{{ t('关闭图片', 'Close image') }}</button><img :src="imageUrl" :alt="t('知识库中的成果图片', 'Artifact image from the vault')" /></section>
    </div>
  </aside>
</template>

<style scoped>
.experiment-panel { display:flex; flex-direction:column; flex:0 0 min(460px,45%); width:460px; min-width:330px; min-height:0; border-left:1px solid var(--color-border-default); background:var(--color-background-primary); color:var(--color-text-primary); font-size:var(--font-size-sm); }
.panel-header,.section-heading,.file-buttons { display:flex; align-items:center; justify-content:space-between; gap:var(--space-sm); flex-wrap:wrap; }
.panel-header { flex-shrink:0; padding:var(--space-md); border-bottom:1px solid var(--color-border-subtle); }
h2,h3,h4 { margin:0; font-size:inherit; } .panel-scroll { overflow:auto; overscroll-behavior:contain; padding:var(--space-md); min-height:0; }
section,details { margin-block:var(--space-sm); } summary { cursor:pointer; padding-block:8px; color:var(--color-text-secondary); } label { display:grid; gap:5px; margin-block:8px; } .check { display:flex; align-items:center; overflow-wrap:anywhere; }
select,input:not([type=checkbox]) { width:100%; box-sizing:border-box; padding:7px; border:1px solid var(--color-border-default); border-radius:var(--radius-sm); background:var(--color-background-secondary); color:inherit; }
fieldset { border:1px solid var(--color-border-subtle); max-height:220px; overflow:auto; padding:8px; } .limits { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
button { cursor:pointer; padding:6px 9px; border-radius:var(--radius-sm); color:inherit; } button:hover:not(:disabled) { background:var(--color-background-secondary); } button:disabled { opacity:.5; cursor:default; } button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible { outline:2px solid var(--color-border-focus); }
.history-row { display:grid; width:100%; gap:4px; text-align:left; border-bottom:1px solid var(--color-border-subtle); } .history-row span,.subtle { color:var(--color-text-secondary); } .notice { margin:8px 0; padding:8px; border-radius:var(--radius-sm); background:var(--color-background-secondary); overflow-wrap:anywhere; } .error { color:var(--color-error); }
.text-preview { overflow:auto; max-height:300px; margin:8px 0; padding:10px; white-space:pre-wrap; overflow-wrap:anywhere; background:var(--color-background-secondary); font-family:var(--font-editor-mono); font-size:12px; user-select:text; }
.output-item,.origins article { padding:8px 0; border-bottom:1px solid var(--color-border-subtle); } p { margin:8px 0; overflow-wrap:anywhere; } .hash { font-family:var(--font-editor-mono); font-size:11px; overflow-wrap:anywhere; color:var(--color-text-secondary); } .path { font-weight:600; }
.table-preview { overflow:auto; max-height:300px; } table { border-collapse:collapse; } td { padding:6px; max-width:240px; border:1px solid var(--color-border-default); white-space:pre-wrap; overflow-wrap:anywhere; } img { display:block; object-fit:contain; max-width:100%; height:auto; max-height:400px; }
@media (max-width:920px) { .experiment-panel { position:absolute; right:0; top:0; bottom:0; z-index:12; width:min(100%,480px); min-width:0; box-shadow:var(--shadow-md); } }
</style>
