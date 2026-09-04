<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { mediaService, createMediaSubmission, type MediaJob } from '@/services/mediaService'

const route = useRoute()
const submission = createMediaSubmission()
const updateExisting = ref(false)
const jobs = ref<MediaJob[]>([])
const selected = ref<MediaJob | null>(null)
const file = ref<File | null>(null)
const reference = ref<File | null>(null)
const matchResult = ref('')
const localOnly = ref(false)
const diarization = ref(true)
const terminology = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const dirty = ref(false)
const title = ref('课堂转写')
const player = ref<HTMLAudioElement | null>(null)
const position = ref(0)
const speed = ref(1)
const history = ref<MediaJob[]>([])
let timer: ReturnType<typeof setTimeout> | undefined
let stopped = false
const labels = {queued: '排队中', running: '转写中', processing: '处理中', completed: '已完成', failed: '失败', cancelled: '已取消'}
const speakers = computed(() => [...new Set(selected.value?.segments.map(s => s.speaker).filter((s): s is string => !!s) || [])])
const active = (job: MediaJob) => ['queued', 'running', 'processing'].includes(job.status)
const stamp = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`

async function refresh() {
  try {
    jobs.value = (await mediaService.list()).items
    if (selected.value && !dirty.value) selected.value = jobs.value.find(j => j.job_id === selected.value?.job_id) || selected.value
  } catch (e) { error.value = (e as Error).message }
  if (!stopped) timer = setTimeout(refresh, 2000)
}
async function choose(job: MediaJob) {
  if (dirty.value && !window.confirm('当前校对尚未保存，切换后放弃修改？')) return
  selected.value = JSON.parse(JSON.stringify(job)); dirty.value = false; history.value = []
}
async function action(work: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await work() } catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
async function submit() {
  if (!file.value) return
  await action(async () => {
    let terms = {}
    if (terminology.value.trim()) {
      terms = JSON.parse(terminology.value)
      if (!terms || typeof terms !== 'object' || Array.isArray(terms) || Object.values(terms).some(v => typeof v !== 'string')) throw new Error('术语表需要 JSON 对象，值为替换后的文本。')
    }
    selected.value = await submission.submit(file.value!, {local_only: localOnly.value,
      diarization: diarization.value, terminology: terms})
    dirty.value = false
    jobs.value = [selected.value, ...jobs.value.filter(job => job.job_id !== selected.value?.job_id)]
  })
}
function seek(seconds: number) { if (player.value) { player.value.currentTime = seconds; position.value = seconds } }
async function purge() {
  if (!selected.value) return
  await action(async () => {
    const impact = await mediaService.impact(selected.value!.attachment_id)
    if (!window.confirm(`${impact.message}\n将保留 ${impact.retained_note_ids.length} 篇已保存笔记。确定清理？`)) return
    await mediaService.purge(selected.value!.attachment_id)
    selected.value = await mediaService.get(selected.value!.job_id)
    dirty.value = false; history.value = []; notice.value = '附件与转写内容已清理'
  })
}
async function compareSpeaker() {
  if (!file.value || !reference.value) return
  await action(async () => {
    const temporary: string[] = []
    try {
      const sample = await mediaService.upload(file.value!); temporary.push(sample.attachment_id)
      const known = await mediaService.upload(reference.value!); temporary.push(known.attachment_id)
      const result = await mediaService.match(sample.attachment_id, known.attachment_id, localOnly.value)
      matchResult.value = `相似度 ${result.score.toFixed(3)} · ${result.source === 'local' ? '本地模型' : 'API'}${result.fallback_reason ? ` · 回退：${result.fallback_reason}` : ''}`
    } finally {
      const cleanup = await Promise.allSettled(temporary.map(id => mediaService.purge(id)))
      if (cleanup.some(result => result.status === 'rejected')) notice.value = '部分临时参考附件清理失败，请检查后端连接。'
    }
  })
}
function loaded() { if (player.value) player.value.playbackRate = speed.value; const seconds = Number(route.query.time || 0); if (Number.isFinite(seconds) && seconds >= 0) seek(seconds) }
onMounted(async () => {
  await refresh()
  if (typeof route.query.job === 'string') {
    try { selected.value = await mediaService.get(route.query.job) } catch (e) { error.value = (e as Error).message }
  }
})
onUnmounted(() => { stopped = true; clearTimeout(timer) })
</script>

<template>
  <section class="media-page">
    <header><h1>音视频转写</h1><p class="subtle">上传音频或视频音轨，转写、校对后保存到知识库。单个文件最多 25 MiB。</p></header>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div><p v-if="notice" role="status">{{ notice }}</p>
    <form class="panel upload" @submit.prevent="submit">
      <label>选择附件<input type="file" accept=".wav,.mp3,.flac,.ogg,.m4a,.mp4,.webm,.txt,.md" @change="file = ($event.target as HTMLInputElement).files?.[0] || null" /></label>
      <label><input v-model="localOnly" type="checkbox" />仅本地处理</label>
      <label><input v-model="diarization" type="checkbox" />识别不同说话人</label>
      <p class="subtle">{{ localOnly ? '本次任务不调用远程模型 API，模型需预先下载。' : '若配置了转写 API，将上传所选附件；API 失败后回退到本地模型。' }}</p>
      <details><summary>术语校对</summary><p class="subtle">在识别完成后替换文本，原始识别结果会保留。</p><textarea v-model="terminology" class="input" rows="3" placeholder='{"错误术语": "正确术语"}' /></details>
      <button type="button" class="button-secondary" :disabled="busy" @click="submission.reset(); notice = '下一次提交将作为新任务处理'">重新处理为新任务</button><button class="button-primary" :disabled="busy || !file">{{ busy ? '处理中…' : '上传并转写' }}</button>
      <details><summary>声纹参考比对</summary><p class="subtle">将所选附件与参考音频比对。至少各含 1 秒语音；分数是相似度，不是身份认证概率。临时参考文件在比对后清理。</p>
        <input type="file" accept=".wav,.mp3,.flac,.ogg,.m4a" aria-label="声纹参考音频" @change="reference = ($event.target as HTMLInputElement).files?.[0] || null" />
        <button type="button" class="button-secondary" :disabled="busy || !file || !reference" @click="compareSpeaker">比对声纹</button><p v-if="matchResult">{{ matchResult }}</p></details>
    </form>
    <div class="media-columns">
      <aside class="panel"><h2>转写任务</h2><p v-if="!jobs.length" class="subtle">暂无转写任务</p>
        <button v-for="job in jobs" :key="job.job_id" class="job-row" :class="{ selected: selected?.job_id === job.job_id }" @click="choose(job)">
          <strong>{{ labels[job.status] }}</strong><span>{{ new Date(job.created_at).toLocaleString() }}</span><small>{{ job.attachment_id }}</small>
        </button>
      </aside>
      <article v-if="selected" class="panel transcript">
        <header><h2>{{ labels[selected.status] }}</h2><span class="badge">修订 {{ selected.revision }}</span></header>
        <progress v-if="active(selected) && selected.progress !== null" :value="selected.progress" :max="1" aria-label="转写进度" />
        <audio ref="player" controls :src="mediaService.audio(selected.attachment_id)" @loadedmetadata="loaded" @timeupdate="position = player?.currentTime || 0" />
        <label>播放速度<select v-model.number="speed" class="select" @change="player && (player.playbackRate = speed)"><option v-for="value in [0.5, 0.75, 1, 1.25, 1.5, 2]" :key="value" :value="value">{{ value }}×</option></select></label>
        <p v-if="selected.error_message" class="error-banner">{{ selected.error_message }} · {{ selected.error_code }}</p>
        <p v-if="selected.fallback_reason" class="subtle">已回退：{{ selected.fallback_reason }}</p>
        <p v-for="warning in selected.warnings" :key="warning" class="subtle">{{ ({DIARIZATION_UNAVAILABLE: '当前无法分离说话人', WORD_TIMESTAMPS_UNAVAILABLE: '未提供逐字时间戳', DIARIZATION_SEGMENT_LEVEL: '说话人按音频段估计，同段多人或重叠发言需人工校对'} as Record<string,string>)[warning] || warning }}</p>
        <button v-if="active(selected)" class="button-secondary" :disabled="busy" @click="action(async () => { selected = await mediaService.cancel(selected!.job_id) })">取消任务</button>
        <button v-if="['failed', 'cancelled'].includes(selected.status) && selected.error_code !== 'MEDIA_PURGED'" class="button-secondary" :disabled="busy" @click="action(async () => { selected = await mediaService.retry(selected!.job_id) })">重新处理</button>
        <button v-if="!active(selected) && selected.error_code !== 'MEDIA_PURGED'" class="button-danger" :disabled="busy" @click="purge">清理原附件与转写</button>
        <template v-if="selected.status === 'completed'">
          <div class="speaker-names"><label v-for="speaker in speakers" :key="speaker">{{ speaker }}<input v-model="selected.speaker_names[speaker]" class="input" placeholder="说话人显示名" @input="dirty = true" /></label></div>
          <p v-if="selected.segments.length" class="subtle">时间戳对应音频分段边界，可点击定位播放。</p>
          <div v-for="segment in selected.segments" :key="segment.segment_id" class="segment" :class="{ current: position >= segment.start_time && position < segment.end_time }">
            <button class="button-secondary" @click="seek(segment.start_time)">{{ stamp(segment.start_time) }}</button><small>{{ selected.speaker_names[segment.speaker || ''] || segment.speaker }}</small>
            <textarea v-model="segment.text" class="input" rows="2" @input="dirty = true; selected.text = selected.segments.map(s => s.text).join('\n')" />
          </div>
          <textarea v-if="!selected.segments.length" v-model="selected.text" class="input" rows="12" @input="dirty = true" />
          <div class="inline-actions"><button class="button-primary" :disabled="busy || !dirty" @click="action(async () => { selected = await mediaService.save(selected!); dirty = false; notice = '校对已保存' })">保存校对</button>
            <button class="button-secondary" @click="action(async () => { history = (await mediaService.revisions(selected!.job_id)).items })">修订历史</button></div>
          <details><summary>原始识别文本</summary><pre>{{ selected.original_text }}</pre></details>
          <details v-for="revision in history" :key="revision.revision"><summary>修订 {{ revision.revision }}</summary><pre>{{ revision.text }}</pre></details>
          <div class="inline-actions"><label><input v-model="updateExisting" type="checkbox" />更新上次导出的笔记（已手动修改则拒绝）</label><input v-model="title" class="input" aria-label="笔记标题" /><button class="button-primary" :disabled="busy || dirty || !title.trim()" @click="action(async () => { const note = await mediaService.note(selected!.job_id, title, updateExisting); notice = `已保存笔记：${note.title}` })">保存为笔记</button></div>
        </template>
      </article>
      <div v-else class="panel subtle">选择任务查看转写结果。</div>
    </div>
  </section>
</template>

<style scoped>
.media-page{padding:28px;overflow:auto;height:100%;display:flex;flex-direction:column;gap:20px}.upload{display:grid;gap:12px;padding:20px}.media-columns{display:grid;grid-template-columns:260px minmax(0,1fr);gap:20px}.panel{padding:20px}.job-row{display:flex;flex-direction:column;gap:6px;width:100%;text-align:left;padding:12px;background:transparent;border:1px solid var(--color-border-default);border-radius:10px;margin-bottom:8px;cursor:pointer;color:inherit}.job-row small{overflow:hidden;text-overflow:ellipsis;max-width:100%}.selected,.current{background:var(--color-background-hover);outline:1px solid var(--color-accent-primary)}.transcript{display:flex;flex-direction:column;gap:16px}.transcript header,.segment{display:flex;gap:12px;align-items:center}.transcript>.button-danger{align-self:flex-start}.transcript>label{white-space:nowrap}.transcript>label select{width:160px}.segment textarea{flex:1}.speaker-names{display:flex;flex-wrap:wrap;gap:10px}audio{width:100%}pre{white-space:pre-wrap;word-break:break-word}label{display:flex;gap:8px;align-items:center}@media(max-width:850px){.media-columns{grid-template-columns:1fr}.segment{flex-wrap:wrap}}
</style>
