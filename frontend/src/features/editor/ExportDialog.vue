<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import AppDialog from '@/components/common/AppDialog.vue'
import { useEditorStore } from '@/stores/editor'
import { useThemeStore } from '@/stores/theme'
import { exportService, type ExportFormat, type ExportJob } from '@/services/exportService'
const emit = defineEmits<{ close: [] }>()
const editor = useEditorStore(), theme = useThemeStore()
const format = ref<ExportFormat>('html'), page = ref('A4'), title = ref(true)
const jobs = ref<ExportJob[]>([]), error = ref(''), preparing = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined, disposed = false
let controller: AbortController | undefined
const labels = { queued: '排队中', running: '渲染中', completed: '已完成', failed: '失败', cancelled: '已取消' }
async function refresh() {
  try { const value = await exportService.list(); if (!disposed) jobs.value = value } catch (e) { error.value = String(e) }
  if (!disposed) timer = setTimeout(refresh, 1500)
}
async function start() {
  preparing.value = true; error.value = ''; controller = new AbortController()
  const snapshot = editor.content, name = editor.currentFilePath?.split('/').pop()?.replace(/\.md$/i, '') ?? '笔记'
  try {
    const job = await exportService.create(snapshot, name, format.value, { theme_id: theme.currentThemeId, include_title: title.value, page_size: page.value }, controller.signal, editor.currentFilePath ?? undefined)
    if (!disposed) jobs.value.unshift(job)
  } catch (e) { error.value = e instanceof DOMException && e.name === 'AbortError' ? '已取消导出' : String(e) }
  finally { preparing.value = false }
}
async function action(job: ExportJob, download = false) {
  try { if (download) await exportService.download(job); else await exportService.cancel(job.id) } catch (e) { error.value = String(e) }
}
onMounted(refresh)
onBeforeUnmount(() => { disposed = true; clearTimeout(timer); controller?.abort() })
</script>
<template>
  <AppDialog label="导出笔记" @close="emit('close')"><section class="modal export-modal">
    <h2>导出笔记</h2><p>导出点击时的编辑器快照，包含未保存修改。关闭窗口后后台任务继续运行。</p>
    <label for="export-format">格式</label><select id="export-format" v-model="format"><option value="html">HTML</option><option value="pdf">PDF</option><option value="docx">DOCX</option></select>
    <label>纸张 <select v-model="page"><option>A4</option><option>Letter</option></select></label>
    <label><input v-model="title" type="checkbox">包含标题</label>
    <p v-if="format !== 'html'">PDF / DOCX 使用浅色打印样式。</p>
    <button class="button-primary" :disabled="preparing || !editor.content.trim()" @click="start">{{ preparing ? '准备图表…' : '开始导出' }}</button>
    <button v-if="preparing" @click="controller?.abort()">取消准备</button>
    <p v-if="error" role="alert">{{ error }}</p>
    <ul><li v-for="job in jobs" :key="job.id">
      <strong>{{ job.fileName ?? job.id }}</strong> · {{ labels[job.status] }}
      <button v-if="job.status === 'completed'" @click="action(job, true)">下载</button>
      <button v-if="['queued','running'].includes(job.status)" @click="action(job)">取消</button>
      <p v-if="job.error" role="alert">{{ job.error }}</p>
      <ul v-if="job.warnings.length" class="export-warnings" aria-label="导出警告"><li v-for="warning in job.warnings" :key="warning">{{ warning }}</li></ul>
    </li></ul>
    <button class="button-secondary" @click="emit('close')">关闭</button>
  </section></AppDialog>
</template>
<style scoped>
.export-modal { width: min(640px, 100%); padding: 24px; background: var(--color-surface-primary); border: 1px solid var(--color-border-default); border-radius: 12px; }
label { display: inline-flex; align-items:center; gap: 8px; margin: 8px; } li { margin-block: 12px; overflow-wrap: anywhere; } button { margin: 6px; } .export-warnings { color: var(--color-warning); } [role=alert] { color:var(--color-error); }
</style>
