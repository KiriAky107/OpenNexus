<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import AppDialog from '@/components/common/AppDialog.vue'
import { hostInvoke } from '@/services/platform/desktop'
import { useWorkspaceStore } from '@/stores/workspace'
import { t } from '@/i18n'

const props = defineProps<{ slot: string; sourceRevision: string }>()
const emit = defineEmits<{ close: []; updated: [] }>()
const workspace = useWorkspaceStore()
interface Target { file_id: string; path: string; hash: string; revision: number }
interface FileReview { key: string; target: Target; fingerprint: string; before: string | null; after: string; after_sha256: string }
interface Review { slot: string; package_name: string; package_version: string; entry: string | null; inputs: string[]; files: FileReview[] }
interface Applied { operation_id: string; key: string; path: string; file_id: string }
const directory = ref('experiments/community'), notePath = ref(''), review = ref<Review | null>(null)
const selected = ref<string[]>([]), imported = ref<Applied[]>([]), busy = ref(false), applying = ref(false), error = ref('')
let generation = 0, activeRequest: string | undefined, cancelledGeneration: number | undefined
function cancelRequest() {
  cancelledGeneration = generation
  if (activeRequest) void hostInvoke('extension_stage_cancel', { requestId: activeRequest }).catch(() => undefined)
}
function invalidate() { ++generation; cancelRequest(); review.value = null; selected.value = []; busy.value = false; applying.value = false }
function message(reason: unknown) {
  const code = reason instanceof Error ? reason.message : String(reason)
  const messages: Record<string, string> = {
    REVISION_CONFLICT: t('目标文件已改变，请重新预览。', 'The target file changed. Preview again.'),
    EXPERIMENT_IMPORT_TARGET_CHANGED: t('目标文件的修订或身份已改变，请重新预览。', 'The target revision or identity changed. Preview again.'),
    EXTENSION_CANDIDATE_CHANGED: t('模板或审核内容已改变，请重新预览。', 'The template or review changed. Preview again.'),
    EXTENSION_TEMPLATE_INVALID: t('请核对实验目录、笔记路径和模板文件格式。', 'Check the experiment directory, note path and template format.'),
    EXTENSION_TEMPLATE_EMPTY_TARGET: t('此模板需要选择一个 Markdown 笔记路径。', 'Choose a Markdown note path for this template.'),
    EXTENSION_TEMPLATE_TARGET_LIMIT: t('目标内容超出预览限制，请选择其他目标。', 'The target exceeds the review limit. Choose another target.'),
    EXTENSION_SOURCE_UNTRUSTED: t('来源的信任设置已改变，请重新核对签名密钥。', 'Source trust changed. Review the signing key again.'),
    EXTENSION_KEY_REVOKED: t('签名密钥已撤销，请核对社区来源。', 'The signing key was revoked. Review the catalog source.'),
    EXTENSION_RELEASE_WITHDRAWN: t('此版本已撤回，请选择其他版本。', 'This release was withdrawn. Choose another version.'),
    EXTENSION_TRUST_UNAVAILABLE: t('暂时无法核对来源，请稍后重试。', 'The source could not be checked. Retry later.'),
    REQUEST_CANCELLED: t('已停止本次导入，请核对下方已完成的文件。', 'This import stopped. Check the completed files below.'),
    VAULT_CHANGED: t('知识库已切换，请重新预览目标。', 'The vault changed. Preview the targets again.'),
  }
  return messages[code] ?? code
}
function checked(value: Review) {
  const digest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
  const uuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
  if (!value || value.slot !== props.slot || typeof value.package_name !== 'string' || typeof value.package_version !== 'string' || !Array.isArray(value.files) || !value.files.length || value.files.length > 33 || !Array.isArray(value.inputs)) throw new Error('EXTENSION_TEMPLATE_INVALID')
  const keys = new Set<string>(), paths = new Set<string>(), ids = new Set<string>()
  for (const file of value.files) {
    if (!file || typeof file.key !== 'string' || !file.key || keys.has(file.key) || !file.target || typeof file.target.path !== 'string' || !file.target.path || paths.has(file.target.path) || !uuid(file.target.file_id) || ids.has(file.target.file_id) || !Number.isSafeInteger(file.target.revision) || file.target.revision < 0 || (file.target.hash !== '' && !digest(file.target.hash)) || (file.target.hash === '') !== (file.target.revision === 0) || !digest(file.fingerprint) || !digest(file.after_sha256) || typeof file.after !== 'string' || (file.before !== null && typeof file.before !== 'string') || Boolean(file.target.hash) !== (file.before !== null)) throw new Error('EXTENSION_TEMPLATE_INVALID')
    keys.add(file.key); paths.add(file.target.path); ids.add(file.target.file_id)
  }
  if ((value.entry !== null && (typeof value.entry !== 'string' || !paths.has(value.entry))) || value.inputs.some(path => typeof path !== 'string' || !paths.has(path) || path === value.entry) || new Set(value.inputs).size !== value.inputs.length) throw new Error('EXTENSION_TEMPLATE_INVALID')
  return value
}
async function inspect() {
  invalidate(); error.value = ''
  const vault = workspace.vaultId, current = ++generation
  if (!vault) return
  busy.value = true
  try {
    const value = await hostInvoke<Review>('extension_template_preview', { request: { vault_id: vault, slot: props.slot, directory: directory.value, note_path: notePath.value || null } })
    if (current === generation && workspace.vaultId === vault) review.value = checked(value)
  } catch (reason) { if (current === generation) error.value = message(reason) }
  finally { if (current === generation) busy.value = false }
}
function saved(key: string, path: string) { return imported.value.some(file => file.key === key && file.path === path) }
async function apply(file: FileReview) {
  if (!review.value || !selected.value.includes(file.key) || busy.value || saved(file.key, file.target.path)) return
  const vault = workspace.vaultId, current = ++generation, operationId = crypto.randomUUID()
  if (!vault) return
  let requestId: string | undefined, dispatched = false
  busy.value = true; applying.value = true; cancelledGeneration = undefined; error.value = ''
  const finish = () => { imported.value.push({ operation_id: operationId, key: file.key, path: file.target.path, file_id: file.target.file_id }); emit('updated') }
  const matches = (entry: { path?: string; hash?: string; file_id?: string } | null | undefined) => entry?.path === file.target.path && entry.hash === file.after_sha256 && entry.file_id === file.target.file_id
  try {
    requestId = await hostInvoke<string>('extension_stage_prepare')
    if (current !== generation || workspace.vaultId !== vault || cancelledGeneration === current) { await hostInvoke('extension_stage_cancel', { requestId }); if (cancelledGeneration === current) throw new Error('REQUEST_CANCELLED'); return }
    activeRequest = requestId; dispatched = true
    const receipt = await hostInvoke<{ operation_id: string; state: string; key: string; entry: Target }>('extension_template_apply', { request: { request_id: requestId, application: { vault_id: vault, slot: props.slot, key: file.key, target: file.target, fingerprint: file.fingerprint, operation_id: operationId } } })
    if (receipt?.operation_id !== operationId || receipt.state !== 'applied' || receipt.key !== file.key || !matches(receipt.entry)) throw new Error(t('导入结果尚未确认，请核对目标文件。', 'The import is unconfirmed. Check the target file.'))
    if (current === generation && workspace.vaultId === vault) finish()
  } catch (reason) {
    if (current === generation && workspace.vaultId === vault) {
      let recovered = false
      if (dispatched) {
        const state = await hostInvoke<{ operation_id: string; state: string; result: Target | null } | null>('workspace_operation', { vaultId: vault, operationId }).catch(() => null)
        recovered = !!state && state.operation_id === operationId && state.state === 'committed' && matches(state.result)
      }
      if (current === generation && workspace.vaultId === vault) { if (recovered) finish(); else { review.value = null; selected.value = []; error.value = message(reason) } }
    }
  } finally {
    if (activeRequest === requestId) activeRequest = undefined
    if (requestId) void hostInvoke('extension_stage_cancel', { requestId }).catch(() => undefined)
    if (current === generation) { busy.value = false; applying.value = false }
  }
}
watch([directory, notePath], () => { invalidate(); error.value = '' })
watch(() => [props.slot, props.sourceRevision], () => { invalidate(); error.value = t('模板或来源已改变，请重新预览。', 'The template or source changed. Preview again.') }, { flush: 'sync' })
watch(() => workspace.vaultId, () => { invalidate(); imported.value = []; error.value = message('VAULT_CHANGED') }, { flush: 'sync' })
onBeforeUnmount(invalidate)
</script>

<template>
  <AppDialog :label="t('导入社区模板', 'Import a catalog template')" :dismissible="!applying" @close="emit('close')"><section class="modal-card template-candidate">
    <h2>{{ t('导入模板文件', 'Import template files') }}</h2>
    <p>{{ t('选择目标，勾选所需文件，再逐个确认导入。每个文件独立写入；停止后保留已完成的文件。', 'Choose the targets, select the files, then confirm each import. Each file is written separately; stopping keeps completed files.') }}</p>
    <p>{{ t('导入后从文件树打开实验源文件，核对输入并单独确认运行。', 'After importing, open the experiment source in the file tree, review its inputs and separately confirm running it.') }}</p>
    <div class="destinations"><label>{{ t('实验目录', 'Experiment directory') }}<input v-model="directory" class="input" :disabled="busy" placeholder="experiments/course" /></label><label>{{ t('笔记路径（可选）', 'Note path (optional)') }}<input v-model="notePath" class="input" :disabled="busy" placeholder="template.md" /></label></div>
    <button class="button-secondary" :disabled="busy" @click="inspect">{{ t('预览文件差异', 'Preview file differences') }}</button>
    <p v-if="busy" role="status">{{ t('正在核对…', 'Checking…') }}</p><p v-if="error" role="alert" class="error-banner">{{ error }}</p>
    <template v-if="review"><h3>{{ review.package_name }} · {{ review.package_version }}</h3><p v-if="review.entry">{{ t('运行入口：', 'Run entry: ') }}{{ review.entry }}</p><p v-if="review.inputs.length">{{ t('声明输入：', 'Declared inputs: ') }}{{ review.inputs.join(' · ') }}</p>
      <article v-for="file in review.files" :key="file.key" class="file-review"><label><input v-model="selected" type="checkbox" :value="file.key" :disabled="busy || saved(file.key, file.target.path)" />{{ file.target.path }}</label><p>{{ file.before === null ? t('新建文件', 'New file') : t('覆盖前需要审核差异', 'Review the differences before replacing') }}</p>
        <details><summary>{{ t('查看完整前后内容', 'View the full before and after content') }}</summary><div class="differences"><section><h4>{{ t('当前内容', 'Current content') }}</h4><pre v-if="file.before !== null">{{ file.before }}</pre><p v-else>{{ t('目标文件尚不存在', 'The target file does not exist') }}</p></section><section><h4>{{ t('导入内容', 'Imported content') }}</h4><pre>{{ file.after }}</pre></section></div></details>
        <p v-if="saved(file.key, file.target.path)" role="status">{{ t('此文件已导入', 'This file was imported') }}</p><button v-else class="button-primary" :disabled="busy || !selected.includes(file.key)" @click="apply(file)">{{ t('确认导入此文件', 'Confirm this file import') }}</button>
      </article>
    </template>
    <section v-if="imported.length" class="imported-files"><h3>{{ t(`已导入 ${imported.length} 个文件`, `${imported.length} files imported`) }}</h3><ul><li v-for="file in imported" :key="file.operation_id">{{ file.path }}</li></ul></section>
    <button v-if="applying" class="button-secondary" @click="cancelRequest">{{ t('停止并核对结果', 'Stop and check the result') }}</button><button v-else class="button-secondary" @click="emit('close')">{{ t('关闭', 'Close') }}</button>
  </section></AppDialog>
</template>

<style scoped>
.template-candidate{width:min(1000px,calc(100vw - 32px));max-height:85vh;overflow:auto;padding:var(--space-xl);background:var(--color-surface-primary);color:var(--color-text-primary);border:1px solid var(--color-border-default);border-radius:var(--radius-lg)}
.template-candidate p,.template-candidate h2{margin-block:var(--space-sm)}.destinations,.differences{display:grid;grid-template-columns:1fr 1fr;gap:var(--space-md);margin-block:var(--space-md)}label{overflow-wrap:anywhere}.destinations label{display:grid;gap:var(--space-xs);min-width:0}.file-review{padding:var(--space-md);margin-block:var(--space-md);border:1px solid var(--color-border-subtle);border-radius:var(--radius-md)}.file-review button{margin-top:var(--space-sm)}.differences section{min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto}.template-candidate h3,.template-candidate p,.imported-files li{overflow-wrap:anywhere}summary{cursor:pointer}.imported-files{margin-block:var(--space-md)}
@media(max-width:700px){.destinations,.differences{grid-template-columns:1fr}}
</style>
