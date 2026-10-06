<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { t } from '@/i18n'
import { request, errorText, type AgentExperimentReview, type OutputManifest, type OutputPreview, type SelectedFile, type TextPreview } from '@/services/experimentService'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{ review: AgentExperimentReview }>()
const emit = defineEmits<{ ready: [value: boolean] }>()
const workspace = useWorkspaceStore()
const summary = computed(() => props.review.kind === 'experiment_run' ? props.review.record.summary : props.review.record.plan.source)
type Comparison = { target: SelectedFile; bytes: number; preview: TextPreview | null; after?: TextPreview; image?: OutputPreview }
const comparisons = ref<Comparison[]>([])
const loading = ref(false), error = ref('')
let generation = 0
const PREVIEW_BYTES = 32 * 1024
watch(() => [props.review, workspace.vaultId] as const, async ([review, vault]) => {
  const version = ++generation
  comparisons.value = []; error.value = ''; loading.value = false; emit('ready', false)
  if (vault !== review.vault_id) return
  if (review.kind === 'experiment_run') { emit('ready', true); return }
  loading.value = true
  try {
    const values: Comparison[] = []
    for (const item of review.record.plan.items) {
      if (version !== generation) return
      const target = await request<Comparison>(review.vault_id, { kind: 'import_target_preview', operation_id: review.operation_id, output_path: item.output.path })
      if (version !== generation) return
      if (target.target.file_id !== item.target.file_id || target.target.path !== item.target.path
        || target.target.hash !== item.target.hash || target.target.revision !== item.target.revision)
        throw new Error('EXPERIMENT_IMPORT_TARGET_CHANGED')
      if (item.output.kind === 'png') {
        target.image = await request<OutputPreview>(review.vault_id, { kind: 'output_preview', operation_id: review.record.plan.request.run_id, path: item.output.path })
        if (target.image.manifest.sha256 !== item.output.sha256 || target.image.content.format !== 'png') throw new Error('EXPERIMENT_RESPONSE_INVALID')
      } else {
        const chunk = await request<{ manifest: OutputManifest; offset: number; next_offset: number | null; content_base64: string }>(review.vault_id,
          { kind: 'output_read', operation_id: review.record.plan.request.run_id, path: item.output.path, offset: 0, limit: PREVIEW_BYTES })
        const bytes = Uint8Array.from(atob(chunk.content_base64), c => c.charCodeAt(0))
        if (chunk.manifest.sha256 !== item.output.sha256 || chunk.manifest.bytes !== item.output.bytes || chunk.offset !== 0
          || bytes.length > PREVIEW_BYTES || bytes.length > item.output.bytes
          || (chunk.next_offset === null ? bytes.length !== item.output.bytes : chunk.next_offset !== bytes.length || bytes.length === 0)) throw new Error('EXPERIMENT_RESPONSE_INVALID')
        const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes, { stream: chunk.next_offset !== null })
        target.after = { text, truncated: chunk.next_offset !== null, lines_shown: text.split('\n').length }
      }
      if (version !== generation) return
      values.push(target)
    }
    comparisons.value = values; emit('ready', true)
  } catch (cause) { if (version === generation) error.value = errorText(cause) }
  finally { if (version === generation) loading.value = false }
}, { immediate: true })
onBeforeUnmount(() => { generation++ })
</script>

<template>
  <section class="experiment-review" :aria-label="review.kind === 'experiment_run' ? t('审核实验运行', 'Review experiment run') : t('审核成果导入', 'Review output import')">
    <strong>{{ review.kind === 'experiment_run' ? t('单次隔离运行', 'One isolated run') : t('单独确认成果导入', 'Separate output import confirmation') }}</strong>
    <p>{{ t('入口', 'Entry') }}: <code>{{ summary.request.entry.path }}</code></p>
    <template v-if="review.kind === 'experiment_run'">
      <p>{{ t('运行环境', 'Runtime') }}: {{ summary.request.runtime_id }} · {{ t('网络关闭', 'Network disabled') }}</p>
      <p>{{ t('仅复制以下入口和输入，输入只读。', 'Only this entry and the selected inputs are copied; inputs are read-only.') }}</p>
      <ul><li v-for="file in [summary.request.entry, ...summary.request.inputs]" :key="file.file_id"><code>{{ file.path }}</code> · {{ summary.sizes[file.path] ?? '—' }} B</li></ul>
      <dl class="budgets">
        <dt>{{ t('最长运行时间', 'Wall time') }}</dt><dd>{{ summary.request.limits.wall_seconds }} s</dd>
        <dt>CPU</dt><dd>{{ summary.request.limits.cpu_seconds }} s</dd>
        <dt>{{ t('内存', 'Memory') }}</dt><dd>{{ summary.request.limits.memory_mib }} MiB</dd>
        <dt>{{ t('进程数', 'Processes') }}</dt><dd>{{ summary.request.limits.processes }}</dd>
        <dt>{{ t('临时磁盘', 'Temporary disk') }}</dt><dd>{{ summary.request.limits.disk_mib }} MiB</dd>
        <dt>{{ t('输出文件', 'Outputs') }}</dt><dd>{{ summary.request.limits.output_mib }} MiB · {{ summary.request.limits.objects }} {{ t('项', 'objects') }}</dd>
        <dt>{{ t('日志保留上限', 'Log retention limit') }}</dt><dd>{{ summary.request.limits.log_kib }} KiB</dd>
      </dl>
      <p>{{ t('运行产生的文件不会自动导入知识库。', 'Generated files are not automatically imported into the vault.') }}</p>
    </template>
    <template v-else>
      <p>{{ t('每个文件单独提交。发生冲突时，已导入的文件会保留，剩余项停止。', 'Each file commits separately. A conflict preserves imported files and stops the remaining items.') }}</p>
      <article v-for="(item, index) in review.record.plan.items" :key="item.write_id" class="import-comparison">
        <strong>{{ item.target.hash ? t('覆盖文件', 'Replace file') : t('创建文件', 'Create file') }}: <code>{{ item.target.path }}</code></strong>
        <p><code>{{ item.output.path }}</code> · {{ comparisons[index]?.bytes ?? '—' }} → {{ item.output.bytes }} B</p>
        <details v-if="comparisons[index]" open class="ui-disclosure">
          <summary>{{ t('核对覆盖内容', 'Review replacement content') }}</summary>
          <div class="comparison-columns">
            <section><strong>{{ t('当前内容', 'Current content') }}</strong><pre v-if="comparisons[index]?.preview">{{ comparisons[index]?.preview?.text }}</pre><p v-else>{{ item.target.hash ? t('图片文件；请核对摘要和大小。', 'Image file; check its digest and size.') : t('新建文件', 'New file') }}</p><p v-if="comparisons[index]?.preview?.truncated">{{ t('当前内容仅显示前缀，实际替换整个文件。', 'Only a prefix of current content is shown; the entire file is replaced.') }}</p></section>
            <section><strong>{{ t('导入内容', 'Imported content') }}</strong><pre v-if="comparisons[index]?.after">{{ comparisons[index]?.after?.text }}</pre><p v-if="comparisons[index]?.after?.truncated">{{ t('导入内容仅显示前 32 KiB，实际导入完整文件。', 'Import preview shows the first 32 KiB; the complete file is imported.') }}</p><img v-if="comparisons[index]?.image?.content.format === 'png'" :src="'data:image/png;base64,' + (comparisons[index]!.image!.content as Extract<OutputPreview['content'], { format: 'png' }>).content_base64" :alt="t('生成图片预览', 'Generated image preview')" /></section>
          </div>
          <p class="digest">{{ t('当前摘要', 'Current digest') }}: {{ item.target.hash || '—' }}<br />{{ t('导入摘要', 'Import digest') }}: {{ item.output.sha256 }}</p>
        </details>
      </article>
    </template>
    <p v-if="loading" role="status">{{ t('正在读取实际覆盖预览…', 'Reading the actual replacement preview…') }}</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <details class="ui-disclosure"><summary>{{ t('源版本与审核详情', 'Source revisions and review details') }}</summary><p v-for="file in [summary.request.entry, ...summary.request.inputs]" :key="file.file_id"><code>{{ file.path }}</code> · r{{ file.revision }}<br />{{ file.hash }}</p><p>{{ t('请求', 'Request') }}: {{ review.operation_id }}<br />{{ t('审核摘要', 'Review digest') }}: {{ review.fingerprint }}</p></details>
    <p>{{ t('允许本次后，还需在桌面原生确认框中核对同一请求。', 'Allow once opens native desktop confirmation for this same request.') }}</p>
  </section>
</template>

<style scoped>
.experiment-review { display: grid; gap: var(--space-sm); min-width: 0; overflow-wrap: anywhere; }
p { margin: 0; }
.budgets { display: grid; grid-template-columns: minmax(100px, 1fr) minmax(0, 2fr); gap: 4px 12px; }
dd { margin: 0; }
.import-comparison { display: grid; gap: var(--space-sm); padding: var(--space-sm); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); }
.comparison-columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr)); gap: var(--space-sm); }
.comparison-columns > section { min-width: 0; }
pre { max-height: 220px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; font: var(--font-size-xs)/1.6 var(--font-ui-mono); }
img { display: block; max-width: 100%; max-height: 220px; object-fit: contain; }
.digest { font: var(--font-size-xs)/1.6 var(--font-ui-mono); }
</style>
