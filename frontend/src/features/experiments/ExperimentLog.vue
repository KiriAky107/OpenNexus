<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { StreamLog } from '@/services/experimentService'
import { t } from '@/i18n'
import { LOG_WINDOW_SIZE, logBoundary, logChunks } from './experimentLogWindow'

const props = defineProps<{ stream: StreamLog; name: string }>()
const view = ref<HTMLElement | null>(null), following = ref(true)
const frozen = ref({ start: 0, end: 0 }), copying = ref(false), copyStatus = ref('')
const range = computed(() => following.value
  ? { start: logBoundary(props.stream.text, props.stream.text.length - LOG_WINDOW_SIZE), end: props.stream.text.length }
  : { start: logBoundary(props.stream.text, frozen.value.start), end: logBoundary(props.stream.text, frozen.value.end) })
const chunks = computed(() => logChunks(props.stream.text, range.value.start, range.value.end))
const windowed = computed(() => range.value.start > 0 || range.value.end < props.stream.text.length)
function pause() {
  if (following.value) frozen.value = { ...range.value }
  following.value = false
}
function show(start: number) {
  following.value = false
  frozen.value = { start: logBoundary(props.stream.text, start), end: logBoundary(props.stream.text, start + LOG_WINDOW_SIZE) }
  if (view.value) view.value.scrollTop = 0
}
async function follow() {
  following.value = true
  await nextTick()
  if (view.value) view.value.scrollTop = view.value.scrollHeight
}
watch([chunks, view], () => { if (following.value && view.value) view.value.scrollTop = view.value.scrollHeight }, { flush: 'post' })
watch(() => props.stream.text.length, (length, previous) => {
  copyStatus.value = ''
  if (length < previous) { frozen.value = { start: 0, end: 0 }; following.value = true }
})
async function copyRetained() {
  if (copying.value) return
  const text = props.stream.text
  copying.value = true; copyStatus.value = ''
  try {
    await navigator.clipboard.writeText(text)
    copyStatus.value = t('已复制当时保留的文本。', 'Copied the text retained at that moment.')
  } catch { copyStatus.value = t('复制失败，可选择预览中的文本复制。', 'Copy failed. Select preview text to copy it.') }
  finally { copying.value = false }
}
</script>

<template>
  <section class="experiment-log" :data-stream="name">
    <h4>{{ name }} · {{ t('收到', 'Received') }} {{ stream.bytes_seen }} B · {{ t('保留', 'Retained') }} {{ stream.retained_bytes }} B</h4>
    <p v-if="stream.truncated" class="notice" data-log-notice="retention">{{ t('已达到日志保留额度；超出额度的后续正文未保存。', 'The log retention limit was reached. Later text beyond the limit was not saved.') }}</p>
    <p v-if="stream.display_truncated" class="notice" data-log-notice="snapshot">{{ t('此记录只提供部分已保留文本。', 'This record provides only part of the retained text.') }}</p>
    <p v-if="windowed" class="notice" data-log-notice="window">{{ t('预览显示部分保留文本，可分段查看或复制已保留文本。', 'The preview shows part of the retained text. Browse sections or copy the retained text.') }}</p>
    <p v-if="stream.invalid_utf8" class="notice">{{ t('无效 UTF-8 已替换；复制结果不是原始字节。', 'Invalid UTF-8 was replaced. Copied text is not the original bytes.') }}</p>
    <p v-if="stream.read_error" class="notice">{{ t('日志读取失败，已收到的内容可能不完整。', 'Log reading failed. Received content may be incomplete.') }}</p>
    <div class="controls" :aria-label="t('浏览保留日志', 'Browse retained log')">
      <button data-log-action="first" :disabled="range.start === 0" @click="show(0)">{{ t('开头', 'Start') }}</button>
      <button data-log-action="previous" :disabled="range.start === 0" @click="show(Math.max(0, range.start - LOG_WINDOW_SIZE))">{{ t('上一段', 'Previous') }}</button>
      <button data-log-action="next" :disabled="range.end === stream.text.length" @click="show(range.end)">{{ t('下一段', 'Next') }}</button>
      <button data-log-action="follow" :aria-pressed="following" @click="follow">{{ t('跟随保留末尾', 'Follow retained end') }}</button>
      <button data-log-action="copy" :disabled="copying || !stream.text" @click="copyRetained">{{ t('复制已保留文本', 'Copy retained text') }}</button>
    </div>
    <pre ref="view" class="log-preview" tabindex="0" :aria-label="name" :data-start="range.start" :data-end="range.end" @wheel.passive="pause" @pointerdown="pause" @keydown="pause"><span v-for="chunk in chunks" :key="chunk.index" :data-log-chunk="chunk.index" v-text="chunk.text" /></pre>
    <p v-if="!stream.complete" class="subtle">{{ t('仍在读取日志。', 'Still reading the log.') }}</p>
    <p v-if="copyStatus" role="status">{{ copyStatus }}</p>
  </section>
</template>

<style scoped>
h4 { margin:0; font-size:inherit; overflow-wrap:anywhere; }
p { margin:8px 0; overflow-wrap:anywhere; }
.notice { padding:8px; border-radius:var(--radius-sm); background:var(--color-background-secondary); }
.subtle { color:var(--color-text-secondary); }
.controls { display:flex; flex-wrap:wrap; gap:4px; }
button { padding:6px 9px; border-radius:var(--radius-sm); color:inherit; cursor:pointer; }
button:disabled { opacity:.5; cursor:default; }
button:hover:not(:disabled),button[aria-pressed=true] { background:var(--color-background-secondary); }
.log-preview { overflow:auto; overflow-anchor:none; max-height:300px; margin:8px 0; padding:10px; white-space:pre-wrap; overflow-wrap:anywhere; background:var(--color-background-secondary); font-family:var(--font-editor-mono); font-size:12px; user-select:text; }
button:focus-visible,.log-preview:focus-visible { outline:2px solid var(--color-border-focus); }
</style>
