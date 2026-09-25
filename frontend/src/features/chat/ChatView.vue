<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Citation, WorkspaceContext } from '@/contracts'
import { useChatStore } from '@/stores/chat'
import { useProviderStore } from '@/stores/provider'
import { useSkillStore } from '@/stores/skill'
import MarkdownContent from '@/components/common/MarkdownContent.vue'
import MessageActivity from './MessageActivity.vue'
import { useCitationNavigation } from '@/composables/useCitationNavigation'
import { t } from '@/i18n'
import ChatPersonaDialog from './ChatPersonaDialog.vue'
import { useChatPreferences } from '@/stores/chatPreferences'
import { listTools } from '@/services/agentService'
import type { ToolDefinition } from '@/contracts'
import { useRouter } from 'vue-router'
import { usedCitations } from '@/utils/usedCitations'

const router = useRouter()
const suggestions = computed(() => [t('根据我的笔记整理本周复习重点', 'Summarize this week’s revision priorities from my notes'), t('解释笔记中的关键概念，并注明来源', 'Explain the key concepts in my notes and cite sources'), t('帮我规划一个循序渐进的学习任务', 'Help me plan a step-by-step study task')])
const props = defineProps<{ workspaceContext?: WorkspaceContext; embedded?: boolean }>()
const chatStore = useChatStore()
const preferences = useChatPreferences()
const showPersona = ref(false)
const settingsExpanded = ref(false)
const imageTools = ref<ToolDefinition[]>([])
const uploadInput = ref<HTMLInputElement | null>(null)
async function selectFiles(e: Event) { const input=e.target as HTMLInputElement; await chatStore.uploadFiles(Array.from(input.files ?? [])); input.value='' }
const providerStore = useProviderStore()
const skillStore = useSkillStore()
const { openCitation } = useCitationNavigation()
const loadError = ref('')
let disposed = false
onBeforeUnmount(() => { disposed = true })

const availableModels = computed(() => providerStore.modelsByProvider[chatStore.selectedProviderId] ?? [])
const streamingMessageId = computed(() => chatStore.isStreaming ? chatStore.messages.at(-1)?.message_id : undefined)
const thinkingLabel = computed(() => t('正在思考…', 'Thinking…'))
const editingMessage = ref<string | null>(null)
const timeline = ref<HTMLElement>()
const visibleCount = ref(30)
const visibleMessages = computed(() => chatStore.messages.slice(-visibleCount.value))
const nearBottom = ref(true)
const hasNewActivity = ref(false)
function trackScroll() {
  const element = timeline.value
  if (!element) return
  nearBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80
  if (nearBottom.value) hasNewActivity.value = false
}
async function latest() {
  await nextTick()
  if (timeline.value) timeline.value.scrollTop = timeline.value.scrollHeight
  nearBottom.value = true; hasNewActivity.value = false
}
async function older() {
  const element = timeline.value
  const height = element?.scrollHeight || 0
  const top = element?.scrollTop || 0
  visibleCount.value += 30
  await nextTick()
  if (element) element.scrollTop = top + element.scrollHeight - height
}
watch(() => chatStore.activeConversationId, () => { visibleCount.value = 30; nearBottom.value = true; hasNewActivity.value = false })
watch(() => [chatStore.messages.length, chatStore.messages.at(-1)?.content.length, chatStore.messages.at(-1)?.activity?.length,
  chatStore.messages.at(-1)?.tool_calls?.map(call => call.status).join(',')], () => {
  if (nearBottom.value) void latest()
  else hasNewActivity.value = true
})
const editedText = ref('')
watch(() => chatStore.activeConversationId, () => { editingMessage.value = null })
async function saveEdit() {
  const id = editingMessage.value
  if (!id || !editedText.value.trim()) return
  await chatStore.retryMessage(id, editedText.value, props.embedded ? props.workspaceContext ?? null : undefined)
  editingMessage.value = null
}
const visibleCitations = computed(() => Object.fromEntries(chatStore.messages.map(message => [
  message.message_id, message.role === 'assistant' ? usedCitations(message.content, message.citations) : [],
])))

onMounted(async () => {
  try {
    void listTools().then(items => { if (!disposed) imageTools.value=items.filter(t => /image|vision/i.test(t.name)) }).catch(() => {})
    await Promise.all([providerStore.loadProviders(), skillStore.loadSkills(), chatStore.loadConversations()])
    if (disposed || providerStore.error) return
    const selected = providerStore.enabledProviders.find(p => p.provider_id === chatStore.selectedProviderId)
    if (!selected) {
      chatStore.selectedProviderId = providerStore.defaultProviderId
    } else {
      await refreshModels(selected.provider_id)
    }
  } catch (error) {
    if (disposed) return
    loadError.value = error instanceof Error ? error.message : t('无法加载 AI 配置，请检查后端连接。', 'Unable to load AI configuration. Check the backend connection.')
  }
})

async function refreshModels(providerId: string) {
  loadError.value = ''
  if (!providerId || providerStore.modelsByProvider[providerId] !== undefined) return
  try { await providerStore.loadModels(providerId) }
  catch (error) { if (!disposed && chatStore.selectedProviderId === providerId) loadError.value = error instanceof Error ? error.message : t('模型列表加载失败，请手动填写模型 ID。', 'Unable to load models. Enter a model ID manually.') }
}

watch(() => chatStore.selectedProviderId, async (providerId) => {
  chatStore.selectedModel = providerStore.providers.find(p => p.provider_id === providerId)?.default_model ?? ''
  await refreshModels(providerId)
})

function send() { void chatStore.sendMessage(chatStore.inputText, undefined, props.embedded ? props.workspaceContext ?? null : undefined) }
function composerKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  if (!event.repeat) send()
}


async function openCitationCard(citation: Citation) {
  loadError.value = ''
  try {
    await openCitation(citation)
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : t('引用定位失败', 'Failed to open citation')
  }
}
</script>

<template>
  <section class="chat-page">
    <header class="chat-toolbar" :class="{ embedded }">
      <template v-if="embedded"><select class="select" aria-label="恢复聊天记录" :value="chatStore.activeConversationId" :disabled="chatStore.isPreparing" @change="chatStore.setActiveConversation(($event.target as HTMLSelectElement).value)"><option v-for="conversation in chatStore.sortedConversations" :key="conversation.conversation_id" :value="conversation.conversation_id">{{ conversation.title }}</option></select></template>
      <button v-if="embedded" class="button-secondary config-toggle" :aria-expanded="settingsExpanded" @click="settingsExpanded = !settingsExpanded">{{ settingsExpanded ? '收起聊天设置 ▴' : '聊天设置 ▾' }}</button>
      <div v-show="!embedded || settingsExpanded" class="chat-settings">
      <button v-if="embedded" class="button-secondary" @click="chatStore.createNewConversation()">新对话</button>
      <div class="field compact"><label>Provider</label><select v-model="chatStore.selectedProviderId" class="select">
        <option v-for="provider in providerStore.enabledProviders" :key="provider.provider_id" :value="provider.provider_id">{{ provider.name }}</option>
      </select></div>
      <div class="field compact"><label for="chat-model-select">{{ t('模型 ID', 'Model ID') }}</label>
        <select v-if="availableModels.length" id="chat-model-select" v-model="chatStore.selectedModel" class="select">
          <option v-if="!availableModels.some(m => m.model_id === chatStore.selectedModel)" :value="chatStore.selectedModel">{{ chatStore.selectedModel || t('选择模型', 'Select model') }}</option>
          <option v-for="model in availableModels" :key="model.model_id" :value="model.model_id">{{ model.name }}</option>
        </select>
        <input v-else id="chat-model-select" v-model="chatStore.selectedModel" class="input" data-field="manual-model" :placeholder="t('填写模型 ID', 'Enter model ID')" />
      </div>
      <button type="button" class="button-secondary" @click="showPersona = true">{{ t('人设与头像', 'Persona and avatars') }}</button>
      <label class="rag-toggle"><input v-model="chatStore.allowAgent" type="checkbox" :disabled="chatStore.isStreaming" />{{ t('允许管理与委托智能体', 'Allow Agent management and delegation') }}</label>
      <label class="rag-toggle"><input v-model="chatStore.useRag" type="checkbox" :disabled="chatStore.isStreaming" />{{ t('检索知识库', 'Search knowledge base') }}</label>
      <span class="subtle">{{ t('模型先回复，按需调用知识库检索；需要提供商支持工具调用，仅显示正文引用的来源。开启智能体后可委托笔记和任务工作，写入操作仍需确认。', 'The model responds first and can search the knowledge base as needed. Requires tool calling; only cited sources are shown. Use Agent for note edits and skills.') }}</span>
      <details class="ui-disclosure image-routing"><summary>图片降级处理</summary><p class="subtle">优先当前模型视觉；选择下列处理器后，允许本次会话将图片交给对应服务。MCP 优先于插件。</p><select v-for="(source,index) in (['mcp_server','plugin'] as const)" :key="source" class="select" :aria-label="index === 0 ? 'MCP 图片处理器' : 'Plugin 图片处理器'" v-model="chatStore.imageFallbackTools[index]"><option value="">不启用此级降级</option><option v-for="tool in imageTools.filter(item => item.source === source)" :key="tool.name" :value="tool.name">{{ tool.name }}</option></select></details>
      </div>
    </header>
    <div v-if="workspaceContext" class="notice-banner">每次发送附带当前文件（含未保存编辑）：{{ workspaceContext.file_path }}</div>
    <div v-if="chatStore.contextNotice" class="notice-banner" role="status">{{ chatStore.contextNotice }}</div>
    <div v-if="loadError || providerStore.error || chatStore.historyError" class="error-banner chat-error">{{ loadError || providerStore.error || chatStore.historyError }}</div>
    <main ref="timeline" class="message-timeline" @scroll.passive="trackScroll">
      <button v-if="chatStore.messages.length > visibleCount" class="button-secondary" @click="older">{{ t('加载更早的消息', 'Load earlier messages') }}</button>
      <div v-if="!chatStore.messages.length" class="empty-state"><div><strong>{{ t('开始一段知识对话', 'Start a knowledge conversation') }}</strong><p>{{ t('围绕当前知识库提问，回答可以引用原文并定位到笔记。', 'Ask about this vault, with sources that open the original notes.') }}</p><div class="chat-suggestions"><button v-for="suggestion in suggestions" :key="suggestion" class="button-secondary" @click="chatStore.inputText = suggestion">{{ suggestion }}</button></div><button v-if="!providerStore.enabledProviders.length && !embedded" class="button-secondary" @click="router.push({ name: 'settings' })">{{ t('配置模型提供商', 'Configure a provider') }}</button></div></div>
      <article v-for="message in visibleMessages" :key="message.message_id" class="message" :class="message.role">
        <div class="avatar"><img v-if="message.role === 'user' ? preferences.settings.userAvatar : preferences.settings.aiAvatar" :src="message.role === 'user' ? preferences.settings.userAvatar : preferences.settings.aiAvatar" :alt="message.role === 'user' ? t('我', 'Me') : 'AI'" /><span v-else>{{ message.role === 'user' ? t('你', 'You') : 'AI' }}</span></div>
        <div class="message-body"><small v-if="message.attachments?.length">附件：{{ message.attachments.map(id=>id.split('.').at(-1)).join('、') }}</small><details v-if="message.workspace_context" class="ui-disclosure"><summary>发送时的文件：{{ message.workspace_context.file_path }}</summary><pre class="context-snapshot">{{ message.workspace_context.content }}</pre></details>
          <details v-if="message.thinking || (message.role === 'assistant' && message.message_id === streamingMessageId)" class="thinking ui-disclosure">
            <summary>
              <span v-if="message.message_id === streamingMessageId && !message.content" class="thinking-indicator" :aria-label="thinkingLabel">
                <span class="thinking-typewriter" aria-hidden="true" :style="{ '--typing-steps': Array.from(thinkingLabel).length }">{{ thinkingLabel }}</span>
              </span>
              <span v-else>{{ t('思考过程', 'Reasoning') }}</span>
            </summary>
            <p v-if="message.thinking">{{ message.thinking }}</p>
          </details>
          <div v-if="editingMessage === message.message_id" class="message-edit">
            <textarea v-model="editedText" class="textarea" :aria-label="t('编辑消息', 'Edit message')" :disabled="!chatStore.canSend" />
            <div class="inline-actions"><button class="button-primary" :disabled="!chatStore.canSend || !editedText.trim()" @click="saveEdit">{{ t('保存并重新生成', 'Save and regenerate') }}</button><button class="button-secondary" @click="editingMessage = null">{{ t('取消', 'Cancel') }}</button></div>
          </div>
          <MessageActivity v-else-if="message.role === 'assistant'" :message="message" :citation-numbers="visibleCitations[message.message_id]?.map(item => item.number)" @citation="number => message.citations?.[number - 1] && openCitationCard(message.citations[number - 1]!)" />
          <MarkdownContent v-else-if="message.content" class="message-content" :source="message.content" />
          <div v-if="visibleCitations[message.message_id]?.length" class="citations">
            <button v-for="{ citation, number } in visibleCitations[message.message_id]" :key="number" class="citation-card" @click="openCitationCard(citation)">
              <span class="badge info">{{ number }}</span><span><strong>{{ citation.heading_path || citation.file_path }}</strong><small>{{ citation.content }}</small></span>
            </button>
          </div>
          <time>{{ new Date(message.created_at).toLocaleTimeString() }}</time>
          <div class="message-actions inline-actions">
            <button v-if="message.role === 'assistant'" class="button-secondary" :disabled="!chatStore.canSend" @click="chatStore.retryMessage(message.message_id, undefined, props.embedded ? props.workspaceContext ?? null : undefined)">{{ t('重新生成', 'Regenerate') }}</button>
            <button v-if="message.role === 'user' && editingMessage !== message.message_id" class="button-secondary" :disabled="!chatStore.canSend" @click="editingMessage = message.message_id; editedText = message.content">{{ t('编辑', 'Edit') }}</button>
            <template v-if="message.versions && message.versions.length > 1">
              <button class="button-secondary" :aria-label="t('上一版本', 'Previous version')" :disabled="!chatStore.canSend || message.versions.indexOf(message.message_id) <= 0" @click="chatStore.switchVersion(message.versions[message.versions.indexOf(message.message_id) - 1]!)">‹</button>
              <span>{{ message.versions.indexOf(message.message_id) + 1 }} / {{ message.versions.length }}</span>
              <button class="button-secondary" :aria-label="t('下一版本', 'Next version')" :disabled="!chatStore.canSend || message.versions.indexOf(message.message_id) >= message.versions.length - 1" @click="chatStore.switchVersion(message.versions[message.versions.indexOf(message.message_id) + 1]!)">›</button>
            </template>
          </div>
          <small v-if="message.usage" class="usage">Token {{ message.usage.total_tokens }}<span v-if="message.usage.input_tokens !== undefined && message.usage.output_tokens !== undefined"> ({{ t('输入', 'input') }} {{ message.usage.input_tokens }} / {{ t('输出', 'output') }} {{ message.usage.output_tokens }})</span></small>
        </div>
      </article>
    </main>
    <button v-if="hasNewActivity" class="button-secondary new-activity" @click="latest">{{ t('有新内容，查看最新进展 ↓', 'New activity — show latest ↓') }}</button>
    <footer class="composer">
      <input ref="uploadInput" type="file" multiple hidden accept=".ppt,.pptx,.docx,.md,.txt,.wav,.mp3,.flac,.ogg,.m4a,.mp4,.webm,.png,.jpg,.jpeg,.webp" @change="selectFiles" />
      <div class="attachment-list"><button class="button-secondary" :disabled="chatStore.uploading || chatStore.isStreaming" @click="uploadInput?.click()">{{ chatStore.uploading ? '上传中…' : '上传文件' }}</button><span v-for="(file,index) in chatStore.pendingAttachments" :key="file.attachment_id" class="badge">{{ file.name }} <button aria-label="移除附件" @click="chatStore.pendingAttachments.splice(index,1)">×</button></span></div>
      <textarea v-model="chatStore.inputText" class="textarea" :placeholder="t('输入问题，Enter 发送，Shift + Enter 换行', 'Enter to send; Shift + Enter for a new line')"
        @keydown="composerKeydown" />
      <div class="composer-actions"><span class="subtle">{{ t('回答可能包含错误，请核对 Citation。', 'Answers may contain errors. Verify the citations.') }}</span>
        <button v-if="chatStore.isStreaming || chatStore.isPreparing" class="button-danger" @click="chatStore.stopGeneration">{{ t('停止', 'Stop') }}</button>
        <button v-else class="button-primary" :disabled="!chatStore.canSend || (!chatStore.inputText.trim() && !chatStore.pendingAttachments.length) || !chatStore.selectedProviderId || !chatStore.selectedModel.trim()" @click="send">{{ t('发送', 'Send') }}</button>
      </div>
    </footer>
    <ChatPersonaDialog v-if="showPersona" @close="showPersona = false" />
  </section>
</template>

<style scoped>
.chat-suggestions { display: grid; gap: 10px; margin: 24px auto; max-width: 520px; }
.chat-suggestions button { padding: 12px 16px; min-height: 42px; line-height: 1.6; text-align: left; }
.message-body > small { display:inline-block; padding:4px 10px; margin-bottom:10px; border:1px solid var(--color-border-subtle); border-radius:var(--radius-md);color:var(--color-text-secondary); }
.context-snapshot { max-height: 180px; overflow: auto; white-space: pre-wrap; }
.chat-page { display: flex; flex-direction: column; height: 100%; min-height: 0; background: radial-gradient(circle at 85% -10%, var(--color-accent-soft), transparent 30%), var(--color-background-primary); }
.chat-toolbar { display: flex; align-items: end; flex-wrap: wrap; gap: var(--space-md); padding: var(--space-md) var(--space-xl); border-bottom: 1px solid var(--color-border-default); background: var(--color-surface-secondary); box-shadow: var(--shadow-sm); z-index: 1; }
.attachment-list { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.image-routing { flex-basis: 100%; }
.chat-settings { display: flex; align-items: end; flex-wrap: wrap; gap: var(--space-md); width: min(100%, 820px); min-width: 0; margin: 0 auto; }
.chat-settings > .subtle { flex-basis: 100%; }
.chat-toolbar.embedded { flex-shrink: 0; }
.chat-toolbar.embedded .chat-settings { max-height: 210px; overflow: auto; }
.config-toggle { margin-left: auto; }
.compact { min-width: 160px; }
.rag-toggle { display: flex; align-items: center; gap: var(--space-xs); min-height: 36px; color: var(--color-text-secondary); }
.chat-error { margin: var(--space-md) var(--space-xl) 0; }
.message-timeline { flex: 1; min-height: 0; overflow: auto; padding: var(--space-xl) max(var(--space-xl), calc((100% - 820px) / 2)); user-select: text; }
.message { display: grid; grid-template-columns: 36px 1fr; gap: var(--space-md); margin-bottom: var(--space-xl); animation: message-in var(--motion-normal) both; }
.avatar img { width: 100%; height: 100%; object-fit: cover; border-radius: inherit; }
.avatar { display: grid; place-items: center; width: 34px; height: 34px; border: 1px solid var(--color-border-default); border-radius: var(--radius-full); background: var(--color-background-tertiary); box-shadow: var(--shadow-sm); font-weight: 700; }
.assistant .avatar { background: var(--color-accent-soft); color: var(--color-accent-primary); }
.message-body { min-width: 0; padding: var(--space-md) var(--space-lg); border: 1px solid var(--color-border-subtle); border-radius: 4px var(--radius-lg) var(--radius-lg) var(--radius-lg); background: color-mix(in srgb, var(--color-surface-primary) 88%, transparent); box-shadow: var(--shadow-sm); }
.user .message-body { background: var(--color-accent-soft); border-color: color-mix(in srgb, var(--color-accent-primary) 14%, transparent); }
.message-content { white-space: pre-wrap; line-height: var(--line-height-relaxed); }
.new-activity { align-self: center; margin: var(--space-xs); }
@media (max-width: 640px) { .message-timeline { padding: var(--space-sm); } .message { grid-template-columns: 24px minmax(0, 1fr); gap: var(--space-xs); } .avatar { width: 24px; height: 24px; } .message-body { padding: var(--space-sm); } }
.thinking { margin-bottom: var(--space-sm); color: var(--color-text-secondary); }.thinking p { margin-top: var(--space-sm); white-space: pre-wrap; }
.thinking-indicator { display: inline-block; }
.message-actions { margin-top: var(--space-sm); }
.message-edit .textarea { width: 100%; min-height: 100px; }
.thinking-typewriter { display: inline-block; white-space: nowrap; padding-inline-end: 3px; border-inline-end: 2px solid var(--color-accent-primary); animation: thinking-type 2s steps(var(--typing-steps), end) infinite; }
@keyframes thinking-type { 0% { clip-path: inset(0 100% 0 0); } 65%, 100% { clip-path: inset(0 0 0 0); } }
@media (prefers-reduced-motion: reduce) { .thinking-typewriter { animation: none; border-inline-end: 0; } .message { animation: none; } }
.tool-calls { display: grid; gap: var(--space-sm); margin-top: var(--space-md); }.tool-calls .item-card { display: grid; gap: var(--space-xs); }.tool-calls pre { overflow: auto; font-size: var(--font-size-xs); }
.usage { display: block; margin-top: var(--space-xs); color: var(--color-text-tertiary); }
.message time { display: block; margin-top: var(--space-sm); color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.citations { display: grid; gap: var(--space-sm); margin-top: var(--space-md); }
.citation-card { display: flex; align-items: flex-start; gap: var(--space-sm); padding: var(--space-md); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-primary); text-align: left; transition: border-color var(--motion-fast), transform var(--motion-fast), box-shadow var(--motion-fast); }
.citation-card:hover { border-color: var(--color-accent-secondary); transform: translateY(-1px); box-shadow: var(--shadow-sm); }
.citation-card small { display: block; margin-top: 2px; color: var(--color-text-secondary); }
.composer { flex-shrink: 0; padding: var(--space-md) max(var(--space-xl), calc((100% - 820px) / 2)); border-top: 1px solid var(--color-border-default); background: var(--color-surface-secondary); box-shadow: 0 -8px 24px color-mix(in srgb, var(--color-text-primary) 5%, transparent); }
.composer .textarea { min-height: 72px; }
.composer-actions { display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); margin-top: var(--space-sm); }

@keyframes message-in { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
</style>
