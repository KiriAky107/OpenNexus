<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Citation } from '@/contracts'
import { useChatStore } from '@/stores/chat'
import { useProviderStore } from '@/stores/provider'
import { useSkillStore } from '@/stores/skill'
import MarkdownContent from '@/components/common/MarkdownContent.vue'
import { useCitationNavigation } from '@/composables/useCitationNavigation'
import { t } from '@/i18n'

const chatStore = useChatStore()
const providerStore = useProviderStore()
const skillStore = useSkillStore()
const { openCitation } = useCitationNavigation()
const loadError = ref('')
let disposed = false
onBeforeUnmount(() => { disposed = true })

const availableModels = computed(() => providerStore.modelsByProvider[chatStore.selectedProviderId] ?? [])

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
  if (!providerId) return
  try { await providerStore.loadModels(providerId) }
  catch (error) { if (!disposed && chatStore.selectedProviderId === providerId) loadError.value = error instanceof Error ? error.message : t('模型列表加载失败，请手动填写模型 ID。', 'Unable to load models. Enter a model ID manually.') }
}

watch(() => chatStore.selectedProviderId, async (providerId) => {
  chatStore.selectedModel = providerStore.providers.find(p => p.provider_id === providerId)?.default_model ?? ''
  await refreshModels(providerId)
})

function send() { void chatStore.sendMessage(chatStore.inputText) }

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
      <div class="field compact"><label>{{ t('模型 ID', 'Model ID') }}</label><input v-model="chatStore.selectedModel" class="input" list="chat-models" :placeholder="t('填写模型 ID', 'Enter model ID')" /><datalist id="chat-models"><option v-for="model in availableModels" :key="model.model_id" :value="model.model_id">{{ model.name }}</option></datalist></div>
      <label class="rag-toggle"><input v-model="chatStore.useRag" type="checkbox" :disabled="chatStore.isStreaming" />{{ t('检索知识库', 'Search knowledge base') }}</label>
      <span class="subtle">{{ t('开启后，将相关笔记片段发送给所选模型，并显示来源。技能调用请使用智能体。', 'When enabled, relevant note excerpts are sent to the selected model and citations are shown. Use Agent for skills.') }}</span>
    </header>
    <div v-if="loadError || providerStore.error || chatStore.historyError" class="error-banner chat-error">{{ loadError || providerStore.error || chatStore.historyError }}</div>
    <main class="message-timeline">
      <div v-if="!chatStore.messages.length" class="empty-state"><div><strong>{{ t('开始一段知识对话', 'Start a knowledge conversation') }}</strong><p>{{ t('请先配置模型提供商。聊天记录保存在本地数据库中。', 'Configure a model provider first. Messages are saved in the local database.') }}</p></div></div>
      <article v-for="message in chatStore.messages" :key="message.message_id" class="message" :class="message.role">
        <div class="avatar">{{ message.role === 'user' ? t('你', 'You') : 'AI' }}</div>
        <div class="message-body">
          <details v-if="message.thinking" class="thinking"><summary>{{ t('思考过程', 'Reasoning') }}</summary><p>{{ message.thinking }}</p></details>
          <MarkdownContent v-if="message.content" class="message-content" :source="message.content" />
          <div v-else-if="chatStore.isStreaming" class="message-content">{{ t('正在思考…', 'Thinking…') }}</div>
          <div v-if="message.tool_calls?.length" class="tool-calls"><div v-for="call in message.tool_calls" :key="call.tool_call_id" class="item-card"><span class="badge info">{{ call.status }}</span><strong>{{ call.name }}</strong><pre>{{ JSON.stringify(call.parameters, null, 2) }}</pre></div></div>
          <div v-if="message.citations?.length" class="citations">
            <button v-for="(citation, index) in message.citations" :key="citation.block_id" class="citation-card" @click="openCitationCard(citation)">
              <span class="badge info">{{ index + 1 }}</span><span><strong>{{ citation.heading_path || citation.file_path }}</strong><small>{{ citation.content }}</small></span>
            </button>
          </div>
          <time>{{ new Date(message.created_at).toLocaleTimeString() }}</time>
          <small v-if="message.usage" class="usage">Token {{ message.usage.total_tokens }}<span v-if="message.usage.input_tokens !== undefined && message.usage.output_tokens !== undefined"> ({{ t('输入', 'input') }} {{ message.usage.input_tokens }} / {{ t('输出', 'output') }} {{ message.usage.output_tokens }})</span></small>
        </div>
      </article>
    </main>
    <footer class="composer">
      <textarea v-model="chatStore.inputText" class="textarea" :placeholder="t('输入问题，Ctrl + Enter 发送', 'Enter a question; press Ctrl + Enter to send')"
        @keydown.ctrl.enter.prevent="send" />
      <div class="composer-actions"><span class="subtle">{{ t('回答可能包含错误，请核对 Citation。', 'Answers may contain errors. Verify the citations.') }}</span>
        <button v-if="chatStore.isStreaming || chatStore.isPreparing" class="button-danger" @click="chatStore.stopGeneration">{{ t('停止', 'Stop') }}</button>
        <button v-else class="button-primary" :disabled="!chatStore.canSend || !chatStore.inputText.trim() || !chatStore.selectedProviderId || !chatStore.selectedModel.trim()" @click="send">{{ t('发送', 'Send') }}</button>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.chat-page { display: grid; grid-template-rows: auto auto 1fr auto; height: 100%; min-height: 0; background: radial-gradient(circle at 85% -10%, var(--color-accent-soft), transparent 30%), var(--color-background-primary); }
.chat-toolbar { display: flex; align-items: end; flex-wrap: wrap; gap: var(--space-md); padding: var(--space-md) var(--space-xl); border-bottom: 1px solid var(--color-border-default); background: var(--color-surface-secondary); box-shadow: var(--shadow-sm); z-index: 1; }
.compact { min-width: 160px; }
.rag-toggle { display: flex; align-items: center; gap: var(--space-xs); min-height: 36px; color: var(--color-text-secondary); }
.chat-error { margin: var(--space-md) var(--space-xl) 0; }
.message-timeline { min-height: 0; overflow: auto; padding: var(--space-xl) max(var(--space-xl), calc((100% - 820px) / 2)); user-select: text; }
.message { display: grid; grid-template-columns: 36px 1fr; gap: var(--space-md); margin-bottom: var(--space-xl); animation: message-in var(--motion-normal) both; }
.avatar { display: grid; place-items: center; width: 34px; height: 34px; border: 1px solid var(--color-border-default); border-radius: var(--radius-full); background: var(--color-background-tertiary); box-shadow: var(--shadow-sm); font-weight: 700; }
.assistant .avatar { background: var(--color-accent-soft); color: var(--color-accent-primary); }
.message-body { min-width: 0; padding: var(--space-md) var(--space-lg); border: 1px solid var(--color-border-subtle); border-radius: 4px var(--radius-lg) var(--radius-lg) var(--radius-lg); background: color-mix(in srgb, var(--color-surface-primary) 88%, transparent); box-shadow: var(--shadow-sm); }
.user .message-body { background: var(--color-accent-soft); border-color: color-mix(in srgb, var(--color-accent-primary) 14%, transparent); }
.message-content { white-space: pre-wrap; line-height: var(--line-height-relaxed); }
.thinking { margin-bottom: var(--space-sm); color: var(--color-text-secondary); }.thinking p { margin-top: var(--space-sm); white-space: pre-wrap; }
.tool-calls { display: grid; gap: var(--space-sm); margin-top: var(--space-md); }.tool-calls .item-card { display: grid; gap: var(--space-xs); }.tool-calls pre { overflow: auto; font-size: var(--font-size-xs); }
.usage { display: block; margin-top: var(--space-xs); color: var(--color-text-tertiary); }
.message time { display: block; margin-top: var(--space-sm); color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.citations { display: grid; gap: var(--space-sm); margin-top: var(--space-md); }
.citation-card { display: flex; align-items: flex-start; gap: var(--space-sm); padding: var(--space-md); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); background: var(--color-surface-primary); text-align: left; transition: border-color var(--motion-fast), transform var(--motion-fast), box-shadow var(--motion-fast); }
.citation-card:hover { border-color: var(--color-accent-secondary); transform: translateY(-1px); box-shadow: var(--shadow-sm); }
.citation-card small { display: block; margin-top: 2px; color: var(--color-text-secondary); }
.composer { padding: var(--space-md) max(var(--space-xl), calc((100% - 820px) / 2)); border-top: 1px solid var(--color-border-default); background: var(--color-surface-secondary); box-shadow: 0 -8px 24px color-mix(in srgb, var(--color-text-primary) 5%, transparent); }
.composer .textarea { min-height: 72px; }
.composer-actions { display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); margin-top: var(--space-sm); }

@keyframes message-in { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
</style>
