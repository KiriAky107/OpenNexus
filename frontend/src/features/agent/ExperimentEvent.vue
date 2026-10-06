<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'
import { request, stateText, errorText, type RunRecord, type RunResult, type ImportRecord, type SelectedFile, type OutputPreview, type StreamLog } from '@/services/experimentService'

const props = defineProps<{ data: Record<string, unknown> }>()
const emit = defineEmits<{ 'open-file': [target: Record<string, unknown>] }>()
const workspace = useWorkspaceStore()
type StateData = { kind: string; state: string; vault_id: string; operation_id: string; source_run_id?: string; entry?: SelectedFile; result?: RunResult; items?: ImportRecord['items']; plan_items?: ImportRecord['plan']['items'] }
const value = computed(() => props.data as unknown as StateData)
const detail = ref<RunRecord>(), output = ref<OutputPreview>(), error = ref(''), busy = ref(false)
const result = computed(() => detail.value?.result ?? value.value.result)
const logViews = computed(() => ({ stdout: displayLog(result.value?.logs.stdout), stderr: displayLog(result.value?.logs.stderr) }))
const outputs = computed(() => result.value?.outputs?.status === 'collected' ? result.value.outputs.summary.files : [])
const isCurrentVault = computed(() => workspace.vaultId === value.value.vault_id)
const sourceRun = computed(() => value.value.kind === 'experiment_import' ? value.value.source_run_id : value.value.operation_id)
let generation = 0
watch(() => [props.data, workspace.vaultId], () => { generation++; detail.value = undefined; output.value = undefined; error.value = ''; busy.value = false })
onBeforeUnmount(() => { generation++ })
async function task(action: (version: number, vault: string) => Promise<void>) {
  if (busy.value || !isCurrentVault.value) return
  const version = generation, vault = value.value.vault_id
  busy.value = true; error.value = ''
  try { await action(version, vault) }
  catch (cause) { if (version === generation && workspace.vaultId === vault) error.value = errorText(cause) }
  finally { if (version === generation) busy.value = false }
}
function current(version: number, vault: string) { return version === generation && workspace.vaultId === vault }
function open(file: SelectedFile) {
  void task(async (version, vault) => {
    const path = await request<string | null>(vault, { kind: 'file_path', file_id: file.file_id })
    if (!current(version, vault)) return
    if (!path) throw new Error(t('文件已删除或不可访问。', 'The file was removed or is inaccessible.'))
    emit('open-file', { file_path: path, file_id: file.file_id, vault_id: vault })
  })
}
function loadRecord() {
  void task(async (version, vault) => {
    const record = await request<RunRecord | null>(vault, { kind: 'record', operation_id: sourceRun.value })
    if (!current(version, vault)) return
    if (!record) throw new Error('EXPERIMENT_OPERATION_NOT_FOUND')
    if (record.summary.request.operation_id !== sourceRun.value || record.summary.request.vault_id !== vault) throw new Error('EXPERIMENT_RESPONSE_INVALID')
    detail.value = record
  })
}
function preview(path: string) {
  void task(async (version, vault) => {
    const content = await request<OutputPreview>(vault, { kind: 'output_preview', operation_id: sourceRun.value, path })
    if (current(version, vault)) output.value = content
  })
}
function displayLog(log?: StreamLog) {
  const text = log?.text ?? '', bytes = new TextEncoder().encode(text)
  return { text: bytes.length > 16 * 1024 ? new TextDecoder().decode(bytes.subarray(0, 16 * 1024), { stream: true }) : text,
    truncated: Boolean(log?.display_truncated) || bytes.length > 16 * 1024 }
}
function copyLog(stream: 'stdout' | 'stderr') {
  void task(async (version, vault) => {
    const run = await request<RunRecord | null>(vault, { kind: 'record', operation_id: sourceRun.value })
    if (!current(version, vault)) return
    if (!run || run.summary.request.operation_id !== sourceRun.value || run.summary.request.vault_id !== vault) throw new Error('EXPERIMENT_RESPONSE_INVALID')
    await navigator.clipboard.writeText(run.result?.logs[stream]?.text ?? '')
  })
}
</script>

<template>
  <section class="experiment-event" @click.stop>
    <p><strong>{{ value.kind === 'experiment_import' ? t('成果导入', 'Output import') : t('实验运行', 'Experiment run') }} · {{ stateText(value.state) }}</strong></p>
    <p class="subtle">{{ t('请求', 'Request') }}: {{ value.operation_id }}</p>
    <p v-if="!isCurrentVault" class="subtle">{{ t('记录属于其他知识库；切回该知识库后可打开文件。', 'This record belongs to another vault; return to it to open its files.') }}</p>
    <div class="inline-actions">
      <button v-if="value.entry" class="button-secondary" :disabled="busy || !isCurrentVault" @click="open(value.entry)">{{ t('打开源文件', 'Open source file') }} · {{ value.entry.path }}</button>
      <button v-if="sourceRun" class="button-secondary" :disabled="busy || !isCurrentVault" @click="loadRecord">{{ t('查看运行记录', 'View run record') }}</button>
    </div>
    <p v-if="detail">{{ t('运行记录状态', 'Run record state') }}: {{ stateText(detail.state) }} · {{ detail.summary.request.runtime_id }} · {{ detail.summary.request.entry.hash }}</p>
    <template v-if="result">
      <p>{{ t('退出码', 'Exit code') }}: {{ result.exit_code ?? '—' }} · {{ result.elapsed_ms }} ms<span v-if="result.error"> · {{ errorText(result.error) }}</span></p>
      <details v-for="stream in ['stdout', 'stderr'] as const" :key="stream" class="ui-disclosure">
        <summary>{{ stream === 'stdout' ? t('标准输出', 'Standard output') : t('错误输出', 'Standard error') }} · {{ result.logs[stream]?.bytes_seen ?? 0 }} B</summary>
        <pre>{{ logViews[stream].text }}</pre>
        <p v-if="logViews[stream].truncated">{{ t('仅展示前 16 KiB；可复制完整已保留日志。', 'Only the first 16 KiB is displayed; copy the complete retained log below.') }}</p>
        <p v-if="result.logs[stream]?.truncated">{{ t('日志保留上限已达到，后续字节未保留。', 'The log retention limit was reached; later bytes were not retained.') }}</p>
        <p v-if="result.logs[stream]?.invalid_utf8">{{ t('部分日志字节不是有效 UTF-8，展示时已替换损坏字符。', 'Some log bytes are invalid UTF-8; damaged characters were replaced for display.') }}</p>
        <p v-if="result.logs[stream]?.read_error || result.logs[stream]?.complete === false">{{ t('日志读取未完整结束，请核对运行错误。', 'Log capture did not finish completely; check the run error.') }}</p>
        <p>{{ t('实际保留', 'Retained') }}: {{ result.logs[stream]?.retained_bytes ?? 0 }} B</p>
        <button class="button-secondary" :disabled="busy || !isCurrentVault" @click="copyLog(stream)">{{ t('复制已保留日志', 'Copy retained log') }}</button>
      </details>
      <div v-if="outputs.length" class="output-list"><strong>{{ t('生成文件；导入需单独确认', 'Generated files; import requires separate confirmation') }}</strong><button v-for="file in outputs" :key="file.path" class="button-secondary" :disabled="busy || !isCurrentVault" @click="preview(file.path)">{{ file.path }} · {{ file.bytes }} B</button></div>
    </template>
    <ul v-if="value.items"><li v-for="(item, index) in value.items" :key="index">{{ stateText(item.state) }} · {{ item.entry?.path ?? value.plan_items?.[index]?.target.path ?? '—' }}<span v-if="item.error"> · {{ errorText(item.error) }}</span><button v-if="item.entry" class="button-secondary" :disabled="busy || !isCurrentVault" @click="open(item.entry)">{{ t('打开导入文件', 'Open imported file') }}</button></li></ul>
    <section v-if="output" class="output-preview"><strong>{{ output.manifest.path }} · {{ output.manifest.bytes }} B</strong><pre v-if="output.content.format === 'text'">{{ output.content.preview.text }}</pre><pre v-else-if="output.content.format === 'csv'">{{ output.content.rows.map(row => JSON.stringify(row)).join('\n') }}</pre><img v-else :src="'data:image/png;base64,' + output.content.content_base64" :alt="t('生成图片预览', 'Generated image preview')" /><p v-if="(output.content.format === 'text' && output.content.preview.truncated) || (output.content.format === 'csv' && output.content.truncated)">{{ t('这是有界预览，文件包含更多内容。', 'This bounded preview omits some file content.') }}</p></section>
    <p v-if="busy" role="status">{{ t('读取记录…', 'Reading record…') }}</p><p v-if="error" class="error-banner" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.experiment-event, .output-list { display: grid; gap: var(--space-sm); min-width: 0; overflow-wrap: anywhere; }
.experiment-event { margin-top: var(--space-sm); }
p { margin: 0; }
pre { max-height: 220px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; font: var(--font-size-xs)/1.6 var(--font-ui-mono); }
img { max-width: 100%; max-height: 256px; object-fit: contain; }
li { margin: var(--space-sm) 0; }
</style>
