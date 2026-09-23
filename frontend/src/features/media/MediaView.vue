<script setup lang="ts">
import ActionDialog from '@/components/common/ActionDialog.vue'
import { useActionDialog } from '@/composables/useActionDialog'
const { actionDialog, resolveAction, askConfirm } = useActionDialog()
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { apiClient } from '@/services/apiClient'
import { isDesktop } from '@/services/platform/desktop'
import { useRoute } from 'vue-router'
import { mediaService, createMediaSubmission, type MediaJob, type MediaArtifacts } from '@/services/mediaService'
import { useCitationNavigation } from '@/composables/useCitationNavigation'
import { localeTag, t } from '@/i18n'
import FilePicker from '@/components/common/FilePicker.vue'
import { useProviderStore } from '@/stores/provider'

const route = useRoute()
const providerStore = useProviderStore()
const maxUploadMiB = isDesktop() ? 64 : 128
const submission = createMediaSubmission()
const updateExisting = ref(false)
const jobs = ref<MediaJob[]>([])
const selected = ref<MediaJob | null>(null)
const { openCitation } = useCitationNavigation()
const uploadOpen = ref(true)
const resultTab = ref<'review' | 'materials'>('review')
const artifacts = ref<MediaArtifacts>({ transcript: null, knowledge_note: null })
const materialCards = computed(() => [
  { label: t('完整转录稿', 'Full transcript'), note: artifacts.value.transcript, hint: t('保留时间戳，方便回听校对', 'Timestamps for playback and review') },
  { label: t('知识点笔记', 'Knowledge notes'), note: artifacts.value.knowledge_note, hint: t('整理概念、步骤与复习问题', 'Concepts, steps and review questions') },
])
function jobName(job: MediaJob) { return job.filename || t('课程录音', 'Course recording') }
function resizeSegment(event: Event) {
  const input = event.target as HTMLTextAreaElement
  input.style.height = 'auto'; input.style.height = `${input.scrollHeight}px`
}
watch(() => selected.value?.job_id, async (id, _old, onCleanup) => {
  let stale = false
  onCleanup(() => { stale = true })
  artifacts.value = { transcript: null, knowledge_note: null }
  artifactError.value = ''; artifactNotice.value = ''; resultTab.value = 'review'
  if (!id) return
  uploadOpen.value = false
  const name = selected.value?.filename?.replace(/\.[^.]+$/, '')
  title.value = name ? `${name} · ${t('转录稿', 'Transcript')}` : t('课堂转写', 'Class transcript')
  knowledgeTitle.value = name ? `${name} · ${t('知识点', 'Knowledge notes')}` : t('课堂知识点笔记', 'Class knowledge notes')
  try {
    const result = await mediaService.getArtifacts(id)
    if (!stale) artifacts.value = result
  } catch (reason) { if (!stale) artifactError.value = reason instanceof Error ? reason.message : String(reason) }
})
const transcriptRoot = ref<HTMLElement | null>(null)
watch([() => selected.value?.segments.length, resultTab], async () => {
  await nextTick()
  transcriptRoot.value?.querySelectorAll<HTMLTextAreaElement>('.segment textarea').forEach(input => {
    input.style.height = 'auto'; input.style.height = `${input.scrollHeight}px`
  })
})
const audioSource = ref('')
watch(() => selected.value?.attachment_id, async (id, _old, onCleanup) => {
  let stale = false
  let objectUrl: string | undefined
  onCleanup(() => { stale = true; if (objectUrl) URL.revokeObjectURL(objectUrl) })
  audioSource.value = ''
  if (!id) return
  if (!isDesktop()) { audioSource.value = mediaService.audio(id); return }
  try {
    const response = await apiClient.get<Response>(`/api/media/attachments/${encodeURIComponent(id)}`)
    const blob = await response.blob()
    if (stale) return
    objectUrl = URL.createObjectURL(blob)
    audioSource.value = objectUrl
  } catch (e) { if (!stale) error.value = (e as Error).message }
})
const file = ref<File | null>(null)
const reference = ref<File | null>(null)
const matchResult = ref('')
const terminologyPlaceholder = computed(() => t('{"错误术语": "正确术语"}', '{"incorrect term": "correct term"}'))
const localOnly = ref(false)
const diarization = ref(true)
const terminology = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const artifactError = ref('')
const artifactNotice = ref('')
const dirty = ref(false)
const title = ref(t('课堂转写', 'Class transcript'))
const knowledgeTitle = ref(t('课堂知识点笔记', 'Class knowledge notes'))
const providerId = ref('')
const model = ref('')
const models = computed(() => providerStore.modelsByProvider[providerId.value] ?? [])
const player = ref<HTMLAudioElement | null>(null)
const position = ref(0)
const speed = ref(1)
const pendingSeek = ref<number | null>(null)
const history = ref<MediaJob[]>([])
let timer: ReturnType<typeof setTimeout> | undefined
let stopped = false
const labels = computed(() => ({queued: t('排队中', 'Queued'), running: t('转写中', 'Transcribing'), processing: t('处理中', 'Processing'), completed: t('已完成', 'Completed'), failed: t('失败', 'Failed'), cancelled: t('已取消', 'Cancelled')}))
const speakers = computed(() => [...new Set(selected.value?.segments.map(s => s.speaker).filter((s): s is string => !!s) || [])])
const active = (job: MediaJob) => ['queued', 'running', 'processing'].includes(job.status)
const stamp = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`
const warningLabel = (warning: string) => warning.startsWith('MEDIA_CORRUPT_PACKETS_SKIPPED:')
  ? t(`已跳过 ${warning.split(':')[1]} 个损坏音频包；缺失时长以静音保留，请校对受影响内容。`, `Skipped ${warning.split(':')[1]} damaged audio packets; missing duration was retained as silence. Review the affected content.`)
  : ({
  DIARIZATION_UNAVAILABLE: t('当前无法分离说话人', 'Speaker identification is unavailable'),
  DIARIZATION_PARTIAL: t('部分片段没有足够语音用于说话人识别，请人工校对', 'Some segments do not contain enough speech for speaker identification; review them manually'),
  WORD_TIMESTAMPS_UNAVAILABLE: t('未提供逐字时间戳', 'Word-level timestamps are unavailable'),
  DIARIZATION_SEGMENT_LEVEL: t('说话人按音频段估计，同段多人或重叠发言需人工校对', 'Speakers are estimated per segment; multiple or overlapping speakers require manual correction'),
} as Record<string, string>)[warning] || warning

async function refresh() {
  try {
    jobs.value = (await mediaService.list()).items
    if (selected.value && !dirty.value) selected.value = jobs.value.find(j => j.job_id === selected.value?.job_id) || selected.value
  } catch (e) { error.value = (e as Error).message }
  if (!stopped) timer = setTimeout(refresh, 2000)
}
async function choose(job: MediaJob) {
  if (busy.value) return
  if (dirty.value && !(await askConfirm(t('当前校对尚未保存，切换后放弃修改？', 'The current corrections are unsaved. Discard them and switch?')))) return
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
    if (file.value!.size > maxUploadMiB * 1024 * 1024) throw new Error(t(`文件不能超过 ${maxUploadMiB} MiB。`, `Files cannot exceed ${maxUploadMiB} MiB.`))
    if (file.value!.size > 25 * 1024 * 1024 && !localOnly.value) throw new Error(t('超过 25 MiB 的录音请先启用仅本地处理。', 'Enable local-only processing for audio above 25 MiB.'))
    let terms = {}
    if (terminology.value.trim()) {
      terms = JSON.parse(terminology.value)
      if (!terms || typeof terms !== 'object' || Array.isArray(terms) || Object.values(terms).some(v => typeof v !== 'string')) throw new Error(t('术语表需要 JSON 对象，值为替换后的文本。', 'The terminology map must be a JSON object whose values are replacement text.'))
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
    if (!(await askConfirm(`${impact.message}\n${t('将保留', 'Will retain')} ${impact.retained_note_ids.length} ${t('篇已保存笔记。确定清理？', 'saved notes. Continue cleanup?')}`))) return
    await mediaService.purge(selected.value!.attachment_id)
    selected.value = await mediaService.get(selected.value!.job_id)
    dirty.value = false; history.value = []; notice.value = t('附件与转写内容已清理', 'Attachment and transcript content were removed')
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
      matchResult.value = `${t('相似度', 'Similarity')} ${result.score.toFixed(3)} · ${result.source === 'local' ? t('本地模型', 'Local model') : 'API'}${result.fallback_reason ? ` · ${t('回退：', 'Fallback: ')}${result.fallback_reason}` : ''}`
    } finally {
      const cleanup = await Promise.allSettled(temporary.map(id => mediaService.purge(id)))
      if (cleanup.some(result => result.status === 'rejected')) notice.value = t('部分临时参考附件清理失败，请检查后端连接。', 'Some temporary reference files could not be removed. Check the backend connection.')
    }
  })
}
async function createArtifacts() {
  if (!selected.value || !providerId.value || !model.value.trim()) return
  if (busy.value) return
  busy.value = true
  const jobId = selected.value.job_id
  artifactError.value = ''
  artifactNotice.value = ''
  try {
    const result = await mediaService.artifacts(jobId, {
      title: title.value,
      knowledge_title: knowledgeTitle.value,
      provider_id: providerId.value,
      model: model.value,
      update_existing: updateExisting.value,
    })
    if (selected.value?.job_id !== jobId) return
    artifacts.value = result
    artifactNotice.value = t(
      `已生成完整转录稿“${result.transcript.title}”和知识点笔记“${result.knowledge_note.title}”。`,
      `Created transcript “${result.transcript.title}” and knowledge notes “${result.knowledge_note.title}”.`,
    )
  } catch (e) {
    if (selected.value?.job_id === jobId) {
      artifactError.value = (e as Error).message
      // A transcript may already exist even if model extraction failed.
      try { artifacts.value = await mediaService.getArtifacts(jobId) } catch { /* keep original error */ }
    }
  } finally {
    busy.value = false
  }
}
function requestedTime() {
  const seconds = Number(Array.isArray(route.query.time) ? route.query.time[0] : route.query.time ?? 0)
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : 0
}
function loaded() {
  if (!player.value) return
  player.value.playbackRate = speed.value
  seek(pendingSeek.value ?? requestedTime())
  pendingSeek.value = null
}
onMounted(async () => {
  await Promise.all([refresh(), providerStore.loadProviders()])
  providerId.value = providerStore.defaultProviderId
})
watch([() => route.query.job, () => route.query.time], async ([job]) => {
  const seconds = requestedTime()
  pendingSeek.value = seconds
  if (typeof job === 'string' && selected.value?.job_id !== job) {
    try { selected.value = await mediaService.get(job) }
    catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason); return }
  }
  if (player.value && player.value.readyState >= 1) {
    seek(seconds)
    pendingSeek.value = null
  }
}, { immediate: true })
watch(providerId, async (value) => {
  model.value = providerStore.providers.find(item => item.provider_id === value)?.default_model ?? ''
  if (!value) return
  try { await providerStore.loadModels(value) } catch { /* 允许手动填写模型 ID。 */ }
})
onUnmounted(() => { stopped = true; clearTimeout(timer) })
</script>

<template>
  <section class="feature-page media-page">
    <ActionDialog v-if="actionDialog" v-bind="actionDialog" @resolve="resolveAction" />
    <header class="feature-header"><div><h1>{{ t('音视频转写', 'Media Transcription') }}</h1><p class="subtle">{{ t(`上传音频或视频音轨，转写、校对后保存到知识库。最多 ${maxUploadMiB} MiB；超过 25 MiB 请启用仅本地处理。音轨最长 1 小时。`, `Upload audio or a video soundtrack, transcribe and correct it, then save it to the knowledge base. Up to ${maxUploadMiB} MiB; enable local-only processing above 25 MiB. Audio duration is limited to one hour.`) }}</p></div></header>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div><p v-if="notice" role="status">{{ notice }}</p>
    <ol class="workflow-steps" :aria-label="t('课程材料流程', 'Course material workflow')"><li :class="{ active: !selected }">1 · {{ t('导入录音', 'Import') }}</li><li :class="{ active: selected && active(selected) }">2 · {{ t('识别转写', 'Transcribe') }}</li><li :class="{ active: selected?.status === 'completed' && resultTab === 'review' }">3 · {{ t('回听校对', 'Review') }}</li><li :class="{ active: resultTab === 'materials' }">4 · {{ t('生成笔记', 'Create notes') }}</li></ol>
    <details class="panel import-panel" :open="uploadOpen" @toggle="uploadOpen = ($event.target as HTMLDetailsElement).open">
    <summary>{{ t('导入新的课程录音', 'Import a course recording') }}<span class="subtle">MP3 · WAV · MP4 · Markdown</span></summary>
    <form class="upload" @submit.prevent="submit">
      <FilePicker :file="file" :label="t('选择附件', 'Choose attachment')" :empty-label="t('尚未选择文件', 'No file selected')" accept=".wav,.mp3,.flac,.ogg,.m4a,.mp4,.webm,.txt,.md" @select="file = $event" />
      <div class="upload-options"><label><input v-model="localOnly" type="checkbox" />{{ t('仅本地处理', 'Process locally only') }}</label>
      <label><input v-model="diarization" type="checkbox" />{{ t('识别不同说话人', 'Identify different speakers') }}</label></div>
      <p class="subtle">{{ localOnly ? t('本次任务不调用远程模型 API，模型需预先下载。', 'This job will not call a remote model API; models must already be downloaded.') : t('若配置了转写 API，将上传所选附件；API 失败后回退到本地模型。', 'When a transcription API is configured, the selected file is uploaded; failures fall back to the local model.') }}</p>
      <details class="ui-disclosure"><summary>{{ t('术语校对', 'Terminology corrections') }}</summary><p class="subtle">{{ t('在识别完成后替换文本，原始识别结果会保留。', 'Replace text after recognition while retaining the original result.') }}</p><textarea v-model="terminology" class="textarea" rows="3" :placeholder="terminologyPlaceholder" /></details>
      <div class="inline-actions upload-actions"><button type="button" class="button-secondary" :disabled="busy" @click="submission.reset(); notice = t('下一次提交将作为新任务处理', 'The next submission will be processed as a new job')">{{ t('重新处理为新任务', 'Process as new job') }}</button><button class="button-primary" :disabled="busy || !file">{{ busy ? t('处理中…', 'Processing…') : t('上传并转写', 'Upload and transcribe') }}</button></div>
      <details class="ui-disclosure"><summary>{{ t('声纹参考比对', 'Speaker reference comparison') }}</summary><p class="subtle">{{ t('将所选附件与参考音频比对。至少各含 1 秒语音；分数是相似度，不是身份认证概率。临时参考文件在比对后清理。', 'Compare the selected file with reference audio. Each must contain at least one second of speech. The score is similarity, not an identity probability. Temporary files are removed afterward.') }}</p>
        <FilePicker :file="reference" :label="t('选择参考音频', 'Choose reference audio')" :empty-label="t('尚未选择参考音频', 'No reference audio selected')" accept=".wav,.mp3,.flac,.ogg,.m4a" @select="reference = $event" />
        <button type="button" class="button-secondary" :disabled="busy || !file || !reference" @click="compareSpeaker">{{ t('比对声纹', 'Compare speakers') }}</button><p v-if="matchResult">{{ matchResult }}</p></details>
    </form>
    </details>
    <div class="media-columns">
      <aside class="panel"><h2>{{ t('转写任务', 'Transcription Jobs') }}</h2><p v-if="!jobs.length" class="subtle">{{ t('暂无转写任务', 'No transcription jobs') }}</p>
        <button v-for="job in jobs" :key="job.job_id" class="job-row" :class="{ selected: selected?.job_id === job.job_id }" :disabled="busy" @click="choose(job)">
          <strong>{{ jobName(job) }}</strong><span class="badge" :class="{ success: job.status === 'completed', error: job.status === 'failed' }">{{ labels[job.status] }}</span><small>{{ new Date(job.created_at).toLocaleString(localeTag()) }}</small>
        </button>
      </aside>
      <article v-if="selected" ref="transcriptRoot" class="panel transcript">
        <div class="playback-bar">
        <header><h2>{{ jobName(selected) }}</h2><span class="badge">{{ labels[selected.status] }} · {{ t('修订', 'Revision') }} {{ selected.revision }}</span></header>
        <progress v-if="active(selected) && selected.progress !== null" :value="selected.progress" :max="1" :aria-label="t('转写进度', 'Transcription progress')" />
        <audio ref="player" controls :src="audioSource || undefined" @loadedmetadata="loaded" @timeupdate="position = player?.currentTime || 0" />
        <label>{{ t('播放速度', 'Playback speed') }}<select v-model.number="speed" class="select" @change="player && (player.playbackRate = speed)"><option v-for="value in [0.5, 0.75, 1, 1.25, 1.5, 2]" :key="value" :value="value">{{ value }}×</option></select></label>
        <nav v-if="selected.status === 'completed'" class="result-tabs" :aria-label="t('转写结果', 'Transcription result')"><button :class="{ active: resultTab === 'review' }" :aria-pressed="resultTab === 'review'" @click="resultTab = 'review'">{{ t('回听与校对', 'Review transcript') }}<span v-if="dirty"> · {{ t('未保存', 'Unsaved') }}</span></button><button :class="{ active: resultTab === 'materials' }" :aria-pressed="resultTab === 'materials'" @click="resultTab = 'materials'">{{ t('生成与查看笔记', 'Create and open notes') }}</button></nav>
        </div>
        <p v-if="selected.error_message" class="error-banner">{{ selected.error_message }} · {{ selected.error_code }}</p>
        <p v-if="selected.fallback_reason" class="subtle">{{ t('已回退：', 'Fallback: ') }}{{ selected.fallback_reason }}</p>
        <p v-for="warning in selected.warnings" :key="warning" class="subtle">{{ warningLabel(warning) }}</p>
        <button v-if="active(selected)" class="button-secondary" :disabled="busy" @click="action(async () => { selected = await mediaService.cancel(selected!.job_id) })">{{ t('取消任务', 'Cancel job') }}</button>
        <button v-if="['failed', 'cancelled'].includes(selected.status) && selected.error_code !== 'MEDIA_PURGED'" class="button-secondary" :disabled="busy" @click="action(async () => { selected = await mediaService.retry(selected!.job_id) })">{{ t('重新处理', 'Process again') }}</button>
        <template v-if="selected.status === 'completed'">
          <div v-show="resultTab === 'review'" class="review-panel">
          <div class="speaker-names"><label v-for="speaker in speakers" :key="speaker">{{ speaker }}<input v-model="selected.speaker_names[speaker]" class="input" :placeholder="t('说话人显示名', 'Speaker display name')" @input="dirty = true" /></label></div>
          <p v-if="selected.segments.length" class="subtle">{{ t('时间戳对应音频分段边界，可点击定位播放。', 'Timestamps mark segment boundaries; click one to seek playback.') }}</p>
          <div v-for="segment in selected.segments" :key="segment.segment_id" class="segment" :class="{ current: position >= segment.start_time && position < segment.end_time }">
            <button class="button-secondary" @click="seek(segment.start_time)">{{ stamp(segment.start_time) }}</button><small>{{ selected.speaker_names[segment.speaker || ''] || segment.speaker }}</small>
            <textarea v-model="segment.text" :aria-label="`${t('转写片段', 'Transcript segment')} ${stamp(segment.start_time)}`" class="textarea" rows="2" @input="resizeSegment($event); dirty = true; selected.text = selected.segments.map(s => s.text).join('\n')" />
          </div>
          <textarea v-if="!selected.segments.length" v-model="selected.text" class="textarea" rows="12" @input="dirty = true" />
          <div class="inline-actions"><button class="button-primary" :disabled="busy || !dirty" @click="action(async () => { selected = await mediaService.save(selected!); dirty = false; notice = t('校对已保存', 'Corrections saved') })">{{ t('保存校对', 'Save corrections') }}</button>
            <button class="button-secondary" @click="action(async () => { history = (await mediaService.revisions(selected!.job_id)).items })">{{ t('修订历史', 'Revision history') }}</button></div>
          <details class="ui-disclosure"><summary>{{ t('原始识别文本', 'Original recognition text') }}</summary><pre>{{ selected.original_text }}</pre></details>
          <details v-for="revision in history" :key="revision.revision" class="ui-disclosure"><summary>{{ t('修订', 'Revision') }} {{ revision.revision }}</summary><pre>{{ revision.text }}</pre></details>
          </div>
          <section v-show="resultTab === 'materials'" class="artifact-panel">
            <div><h3>{{ t('生成课程材料', 'Create course materials') }}</h3><p class="subtle">{{ t('一次生成两份内容：带时间戳的完整转录稿，以及由所选模型提取的知识点笔记。', 'Create two outputs: a timestamped full transcript and knowledge notes extracted by the selected model.') }}</p></div>
            <div class="artifact-grid">
              <label>{{ t('转录稿标题', 'Transcript title') }}<input v-model="title" class="input" /></label>
              <label>{{ t('知识点笔记标题', 'Knowledge-note title') }}<input v-model="knowledgeTitle" class="input" /></label>
              <label>{{ t('模型提供商', 'Model provider') }}<select v-model="providerId" class="select"><option value="">{{ t('请选择', 'Select') }}</option><option v-for="item in providerStore.enabledProviders" :key="item.provider_id" :value="item.provider_id">{{ item.name }}</option></select></label>
              <label>{{ t('知识提取模型', 'Knowledge extraction model') }}<input v-model="model" class="input" list="media-models" :placeholder="t('填写模型 ID', 'Enter model ID')" /><datalist id="media-models"><option v-for="item in models" :key="item.model_id" :value="item.model_id">{{ item.name }}</option></datalist></label>
            </div>
            <div class="inline-actions"><label><input v-model="updateExisting" type="checkbox" />{{ t('安全更新上次导出的转录稿', 'Safely update the last exported transcript') }}</label><button class="button-primary" :disabled="busy || dirty || !title.trim() || !knowledgeTitle.trim() || !providerId || !model.trim()" @click="createArtifacts">{{ busy ? t('生成中…', 'Creating…') : t('生成转录稿与知识点笔记', 'Create transcript and knowledge notes') }}</button></div>
            <p v-if="dirty" class="notice-banner">{{ t('请先在校对页保存修改，再生成笔记。', 'Save corrections in the review tab before creating notes.') }}<button class="link-button" @click="resultTab = 'review'">{{ t('返回校对', 'Review') }}</button></p>
            <p v-if="artifactError" class="error-banner" role="alert">{{ artifactError }}</p>
            <p v-if="artifactNotice" class="artifact-success" role="status">{{ artifactNotice }}</p>
            <div class="material-results"><article v-for="card in materialCards" :key="card.label" class="material-result"><span class="badge" :class="{ success: card.note }">{{ card.note ? t('已保存', 'Saved') : t('尚未生成', 'Not created') }}</span><h3>{{ card.label }}</h3><p>{{ card.note?.title || card.hint }}</p><button v-if="card.note" class="button-secondary" @click="action(() => openCitation(card.note!))">{{ t('打开笔记', 'Open note') }}</button></article></div>
          </section>
        </template>
        <details v-if="!active(selected) && selected.error_code !== 'MEDIA_PURGED'" class="ui-disclosure"><summary>{{ t('管理原始附件', 'Manage source attachment') }}</summary><p class="subtle">{{ t('清理原附件不会删除已生成的笔记，但将无法继续回听。', 'Removing the source keeps generated notes, but disables playback.') }}</p><button class="button-danger" :disabled="busy" @click="purge">{{ t('清理原附件与转写', 'Remove attachment and transcript') }}</button></details>
      </article>
      <div v-else class="panel subtle">{{ t('选择任务查看转写结果。', 'Select a job to view its transcript.') }}</div>
    </div>
  </section>
</template>

<style scoped>
.media-page { gap: var(--space-lg); }
.media-page > * { flex-shrink: 0; }
.workflow-steps { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; list-style: none; padding: 0; width: 100%; max-width: 1180px; margin: 0 auto; }
.workflow-steps li { padding: 10px 12px; border-bottom: 2px solid var(--color-border-default); color: var(--color-text-secondary); }
.workflow-steps li.active { color: var(--color-accent-primary); border-color: currentColor; font-weight: 600; }
.import-panel > summary { cursor: pointer; display: flex; justify-content: space-between; gap: 12px; font-weight: 600; }
.import-panel .upload { padding: 20px 0 0; }
.media-columns > aside { align-self: start; max-height: 540px; overflow: auto; }
.media-columns > aside h2 { margin-bottom: 16px; font-size: var(--font-size-lg); }
.job-row strong { overflow-wrap: anywhere; }
.job-row .badge { align-self: flex-start; }
.playback-bar { position: sticky; top: -28px; z-index: 2; display: grid; gap: 10px; padding: 12px 0; background: var(--color-surface-primary); border-bottom: 1px solid var(--color-border-subtle); }
.playback-bar h2 { font-size: var(--font-size-lg); overflow-wrap: anywhere; }
.playback-bar header { flex-wrap: wrap; justify-content: space-between; }
.playback-bar label { justify-self: end; font-size: var(--font-size-sm); }
.playback-bar select { width: 86px; }
.result-tabs { display: flex; gap: 8px; flex-wrap: wrap; }
.result-tabs button { padding: 10px 14px; border-radius: var(--radius-md); color: var(--color-text-secondary); }
.result-tabs button.active { color: var(--color-accent-primary); background: var(--color-accent-soft); }
.review-panel { display: grid; gap: 14px; }
.segment textarea { min-height: 56px; max-height: 360px; resize: vertical; }
.segment small { width: 65px; flex-shrink: 0; overflow-wrap: anywhere; }
.material-results { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.material-result { display: grid; justify-items: start; gap: 10px; padding: 16px; background: var(--color-surface-primary); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); overflow-wrap: anywhere; }
@media(max-width:620px) { .material-results { grid-template-columns: 1fr; } .workflow-steps { grid-template-columns: 1fr 1fr; } }
.media-page > :is(.feature-header, .panel, .media-columns, .error-banner) { width: 100%; max-width: 1180px; margin-inline: auto; }
.media-page{padding:28px;overflow:auto;height:100%;display:flex;flex-direction:column;gap:20px}.upload{display:grid;gap:12px;padding:20px}.upload-options{display:flex;flex-wrap:wrap;gap:16px}.upload-actions{justify-content:flex-end}.media-columns{display:grid;grid-template-columns:260px minmax(0,1fr);gap:20px}.panel{padding:20px}.job-row{display:flex;flex-direction:column;gap:6px;width:100%;text-align:left;padding:12px;background:transparent;border:1px solid var(--color-border-default);border-radius:10px;margin-bottom:8px;cursor:pointer;color:inherit}.job-row small{overflow:hidden;text-overflow:ellipsis;max-width:100%}.selected,.current{background:var(--color-background-hover);outline:1px solid var(--color-accent-primary)}.transcript{display:flex;flex-direction:column;gap:16px}.transcript header,.segment{display:flex;gap:12px;align-items:center}.transcript>.button-danger{align-self:flex-start}.transcript>label{white-space:nowrap}.transcript>label select{width:160px}.segment textarea{flex:1}.speaker-names{display:flex;flex-wrap:wrap;gap:10px}.artifact-panel{display:grid;gap:14px;padding:18px;border:1px solid var(--color-border-default);border-radius:var(--radius-lg);background:var(--color-surface-secondary)}.artifact-panel h3,.artifact-panel p{margin:0}.artifact-success{color:var(--color-success)}.artifact-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.artifact-grid label{align-items:stretch;flex-direction:column;color:var(--color-text-secondary)}.artifact-grid :is(.input,.select){background:var(--color-surface-primary);color:var(--color-text-primary)}audio{width:100%;border-radius:var(--radius-md);accent-color:var(--color-accent-primary)}pre{white-space:pre-wrap;word-break:break-word}label{display:flex;gap:8px;align-items:center}@media(max-width:850px){.media-columns{grid-template-columns:1fr}.segment{flex-wrap:wrap}}@media(max-width:620px){.artifact-grid{grid-template-columns:1fr}}@media(max-width:560px){.upload-actions>*{flex:1}.upload-options{flex-direction:column}}
</style>
