<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Citation } from '@/contracts'
import { useChatStore } from '@/stores/chat'
import { useProviderStore } from '@/stores/provider'
import { useSkillStore } from '@/stores/skill'
import MarkdownContent from '@/components/common/MarkdownContent.vue'
import { useCitationNavigation } from '@/composables/useCitationNavigation'
import { t } from '@/i18n'
import ChatPersonaDialog from './ChatPersonaDialog.vue'
import { useChatPreferences } from '@/stores/chatPreferences'
import { usedCitations } from '@/utils/usedCitations'

const chatStore = useChatStore()
const preferences = useChatPreferences()
const showPersona = ref(false)
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
const editedText = ref('')
watch(() => chatStore.activeConversationId, () => { editingMessage.value = null })
const activities = computed(() => Object.fromEntries(chatStore.messages.map(message => {
  const entries = message.activity?.length ? message.activity : [
    ...(message.thinking ? [{ type: 'thinking' as const, text: message.thinking }] : []),
    ...(message.tool_calls ?? []).map(call => ({ type: 'tool' as const, tool_call_id: call.tool_call_id })),
  ]
  return [message.message_id, entries.map(entry => entry.type === 'thinking'
    ? { text: entry.text, call: undefined }
    : { text: undefined, call: message.tool_calls?.find(call => call.tool_call_id === entry.tool_call_id) })]
})))
async function saveEdit() {
  const id = editingMessage.value
  if (!id || !editedText.value.trim()) return
  await chatStore.retryMessage(id, editedText.value)
  editingMessage.value = null
}
const visibleCitations = computed(() => Object.fromEntries(chatStore.messages.map(message => [
  message.message_id, message.role === 'assistant' ? usedCitations(message.content, message.citations) : [],
])))

onMounted(async () => {
  try {
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

function send() { void chatStore.sendMessage(chatStore.inputText) }
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
    <header class="chat-toolbar">
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
      <label class="rag-toggle"><input v-model="chatStore.useRag" type="checkbox" :disabled="chatStore.isStreaming" />{{ t('检索知识库', 'Search knowledge base') }}</label>
      <span class="subtle">{{ t('模型先回复，按需调用知识库检索；需要提供商支持工具调用，仅显示正文引用的来源。笔记修改和技能调用请使用智能体。', 'The model responds first and can search the knowledge base as needed. Requires tool calling; only cited sources are shown. Use Agent for note edits and skills.') }}</span>
    </header>
    <div v-if="chatStore.contextNotice" class="notice-banner" role="status">{{ chatStore.contextNotice }}</div>
    <div v-if="loadError || providerStore.error || chatStore.historyError" class="error-banner chat-error">{{ loadError || providerStore.error || chatStore.historyError }}</div>
    <main class="message-timeline">
      <div v-if="!chatStore.messages.length" class="empty-state"><div><strong>{{ t('开始一段知识对话', 'Start a knowledge conversation') }}</strong><p>{{ t('请先配置模型提供商。聊天记录保存在本地数据库中。', 'Configure a model provider first. Messages are saved in the local database.') }}</p></div></div>
      <article v-for="message in chatStore.messages" :key="message.message_id" class="message" :class="message.role">
        <div class="avatar"><img v-if="message.role === 'user' ? preferences.settings.userAvatar : preferences.settings.aiAvatar" :src="message.role === 'user' ? preferences.settings.userAvatar : preferences.settings.aiAvatar" :alt="message.role === 'user' ? t('我', 'Me') : 'AI'" /><span v-else>{{ message.role === 'user' ? t('你', 'You') : 'AI' }}</span></div>
        <div class="message-body">
          <details v-if="message.thinking || message.tool_calls?.length || (message.role === 'assistant' && message.message_id === streamingMessageId)" class="thinking ui-disclosure">
            <summary>
              <span v-if="message.message_id === streamingMessageId && !message.content" class="thinking-indicator" :aria-label="thinkingLabel">
                <span class="thinking-typewriter" aria-hidden="true" :style="{ '--typing-steps': Array.from(thinkingLabel).length }">{{ thinkingLabel }}</span>
              </span>
              <span v-else>{{ t('思考过程', 'Reasoning') }}</span>
            </summary>
            <template v-for="(entry, index) in activities[message.message_id]" :key="index">
              <p v-if="entry.text !== undefined">{{ entry.text }}</p>
              <div v-else-if="entry.call" class="tool-calls"><div class="item-card"><span class="badge info">{{ entry.call.status }}</span><strong>{{ entry.call.name }}</strong><pre>{{ JSON.stringify(entry.call.parameters, null, 2) }}</pre></div></div>
            </template>
          </details>
          <div v-if="editingMessage === message.message_id" class="message-edit">
            <textarea v-model="editedText" class="textarea" :aria-label="t('编辑消息', 'Edit message')" :disabled="!chatStore.canSend" />
            <div class="inline-actions"><button class="button-primary" :disabled="!chatStore.canSend || !editedText.trim()" @click="saveEdit">{{ t('保存并重新生成', 'Save and regenerate') }}</button><button class="button-secondary" @click="editingMessage = null">{{ t('取消', 'Cancel') }}</button></div>
          </div>
          <MarkdownContent v-else-if="message.content" class="message-content" :source="message.content" :citation-aliases="Object.fromEntries((message.citations ?? []).filter(c => c.citation_id).map(c => [c.citation_id!, (message.citations ?? []).indexOf(c) + 1]))" :citation-numbers="visibleCitations[message.message_id]?.map(item => item.number)" @citation="number => message.citations?.[number - 1] && openCitationCard(message.citations[number - 1]!)" />
          <div v-if="visibleCitations[message.message_id]?.length" class="citations">
            <button v-for="{ citation, number } in visibleCitations[message.message_id]" :key="number" class="citation-card" @click="openCitationCard(citation)">
              <span class="badge info">{{ number }}</span><span><strong>{{ citation.heading_path || citation.file_path }}</strong><small>{{ citation.content }}</small></span>
            </button>
          </div>
          <time>{{ new Date(message.created_at).toLocaleTimeString() }}</time>
          <div class="message-actions inline-actions">
            <button v-if="message.role === 'assistant'" class="button-secondary" :disabled="!chatStore.canSend" @click="chatStore.retryMessage(message.message_id)">{{ t('重新生成', 'Regenerate') }}</button>
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
    <footer class="composer">
      <textarea v-model="chatStore.inputText" class="textarea" :placeholder="t('输入问题，Enter 发送，Shift + Enter 换行', 'Enter to send; Shift + Enter for a new line')"
        @keydown="composerKeydown" />
      <div class="composer-actions"><span class="subtle">{{ t('回答可能包含错误，请核对 Citation。', 'Answers may contain errors. Verify the citations.') }}</span>
        <button v-if="chatStore.isStreaming || chatStore.isPreparing" class="button-danger" @click="chatStore.stopGeneration">{{ t('停止', 'Stop') }}</button>
        <button v-else class="button-primary" :disabled="!chatStore.canSend || !chatStore.inputText.trim() || !chatStore.selectedProviderId || !chatStore.selectedModel.trim()" @click="send">{{ t('发送', 'Send') }}</button>
      </div>
    </footer>
    <ChatPersonaDialog v-if="showPersona" @close="showPersona = false" />
  </section>
</template>

<style scoped>
.chat-page { display: flex; flex-direction: column; height: 100%; min-height: 0; background: radial-gradient(circle at 85% -10%, var(--color-accent-soft), transparent 30%), var(--color-background-primary); }
.chat-toolbar { display: flex; align-items: end; flex-wrap: wrap; gap: var(--space-md); padding: var(--space-md) var(--space-xl); border-bottom: 1px solid var(--color-border-default); background: var(--color-surface-secondary); box-shadow: var(--shadow-sm); z-index: 1; }
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
.thinking { margin-bottom: var(--space-sm); color: var(--color-text-secondary); }.thinking p { margin-top: var(--space-sm); white-space: pre-wrap; }
.thinking-indicator { display: inline-block; }
.message-actions { margin-top: var(--space-sm); }
.message-edit .textarea { width: 100%; min-height: 100px; }
.thinking-typewriter { display: inline-block; white-space: nowrap; padding-inline-end: 3px; border-inline-end: 2px solid var(--color-accent-primary); animation: thinking-type 2s steps(var(--typing-steps), end) infinite; }
@keyframes thinking-type { 0% { clip-path: inset(0 100% 0 0); } 65%, 100% { clip-path: inset(0 0 0 0); } }
@media (prefers-reduced-motion: reduce) { .thinking-typewriter { animation: none; border-inline-end: 0; } }
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
