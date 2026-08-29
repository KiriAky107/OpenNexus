<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { Citation } from '@/contracts'
import { useChatStore } from '@/stores/chat'
import { useEditorStore } from '@/stores/editor'
import { useProviderStore } from '@/stores/provider'
import { useSkillStore } from '@/stores/skill'
import { useWorkspaceStore } from '@/stores/workspace'

const chatStore = useChatStore()
const providerStore = useProviderStore()
const skillStore = useSkillStore()
const workspaceStore = useWorkspaceStore()
const editorStore = useEditorStore()
const router = useRouter()
const loadError = ref('')

const availableModels = computed(() => providerStore.modelsByProvider[chatStore.selectedProviderId] ?? [])

onMounted(async () => {
  try {
    await Promise.all([providerStore.loadProviders(), skillStore.loadSkills()])
    await providerStore.loadModels(chatStore.selectedProviderId)
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '无法加载 AI 配置，当前展示本地数据。'
  }
})

watch(() => chatStore.selectedProviderId, async (providerId) => {
  try {
    await providerStore.loadModels(providerId)
    const firstModel = providerStore.modelsByProvider[providerId]?.[0]
    if (firstModel) chatStore.selectedModel = firstModel.model_id
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '模型列表加载失败'
  }
})

function send() { void chatStore.sendMessage(chatStore.inputText) }

async function openCitation(citation: Citation) {
  workspaceStore.openFile(citation.file_path)
  await editorStore.loadFile(citation.file_path)
  editorStore.highlightBlock(citation.block_id)
  await router.push('/workspace')
}
</script>

<template>
  <section class="chat-page">
    <header class="chat-toolbar">
      <div class="field compact"><label>Provider</label><select v-model="chatStore.selectedProviderId" class="select">
        <option v-for="provider in providerStore.enabledProviders" :key="provider.provider_id" :value="provider.provider_id">{{ provider.name }}</option>
      </select></div>
      <div class="field compact"><label>Model</label><select v-model="chatStore.selectedModel" class="select">
        <option v-for="model in availableModels" :key="model.model_id" :value="model.model_id">{{ model.name }}</option>
      </select></div>
      <div class="field compact"><label>Skill</label><select v-model="chatStore.selectedSkillId" class="select">
        <option :value="null">不使用 Skill</option><option v-for="skill in skillStore.enabledSkills" :key="skill.skill_id" :value="skill.skill_id">{{ skill.name }}</option>
      </select></div>
      <label class="rag-toggle"><input v-model="chatStore.useRag" type="checkbox" /> 使用知识库</label>
    </header>
    <div v-if="loadError" class="error-banner chat-error">{{ loadError }}</div>
    <main class="message-timeline">
      <div v-if="!chatStore.messages.length" class="empty-state"><div><strong>开始一段知识对话</strong><p>可以直接提问，也可以打开 RAG 让模型基于当前 Vault 回答。</p></div></div>
      <article v-for="message in chatStore.messages" :key="message.message_id" class="message" :class="message.role">
        <div class="avatar">{{ message.role === 'user' ? '你' : 'AI' }}</div>
        <div class="message-body">
          <div class="message-content">{{ message.content || (chatStore.isStreaming ? '正在思考…' : '') }}</div>
          <div v-if="message.citations?.length" class="citations">
            <button v-for="(citation, index) in message.citations" :key="citation.block_id" class="citation-card" @click="openCitation(citation)">
              <span class="badge info">{{ index + 1 }}</span><span><strong>{{ citation.heading_path || citation.file_path }}</strong><small>{{ citation.content }}</small></span>
            </button>
          </div>
          <time>{{ new Date(message.created_at).toLocaleTimeString() }}</time>
        </div>
      </article>
    </main>
    <footer class="composer">
      <textarea v-model="chatStore.inputText" class="textarea" placeholder="输入问题，Ctrl + Enter 发送"
        @keydown.ctrl.enter.prevent="send" />
      <div class="composer-actions"><span class="subtle">回答可能包含错误，请核对 Citation。</span>
        <button v-if="chatStore.isStreaming" class="button-danger" @click="chatStore.stopGeneration">停止</button>
        <button v-else class="button-primary" :disabled="!chatStore.inputText.trim()" @click="send">发送</button>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.chat-page { display: grid; grid-template-rows: auto auto 1fr auto; height: 100%; min-height: 0; background: var(--color-background-primary); }
.chat-toolbar { display: flex; align-items: end; flex-wrap: wrap; gap: var(--space-md); padding: var(--space-md) var(--space-xl); border-bottom: 1px solid var(--color-border-default); }
.compact { min-width: 160px; }
.rag-toggle { display: flex; align-items: center; gap: var(--space-xs); min-height: 36px; color: var(--color-text-secondary); }
.chat-error { margin: var(--space-md) var(--space-xl) 0; }
.message-timeline { min-height: 0; overflow: auto; padding: var(--space-xl) max(var(--space-xl), calc((100% - 820px) / 2)); user-select: text; }
.message { display: grid; grid-template-columns: 36px 1fr; gap: var(--space-md); margin-bottom: var(--space-xl); }
.avatar { display: grid; place-items: center; width: 34px; height: 34px; border-radius: var(--radius-full); background: var(--color-background-tertiary); font-weight: 700; }
.assistant .avatar { background: var(--color-accent-soft); color: var(--color-accent-primary); }
.message-content { white-space: pre-wrap; line-height: var(--line-height-relaxed); }
.message time { display: block; margin-top: var(--space-sm); color: var(--color-text-tertiary); font-size: var(--font-size-xs); }
.citations { display: grid; gap: var(--space-sm); margin-top: var(--space-md); }
.citation-card { display: flex; align-items: flex-start; gap: var(--space-sm); padding: var(--space-sm); border: 1px solid var(--color-border-default); border-radius: var(--radius-md); text-align: left; }
.citation-card small { display: block; margin-top: 2px; color: var(--color-text-secondary); }
.composer { padding: var(--space-md) max(var(--space-xl), calc((100% - 820px) / 2)); border-top: 1px solid var(--color-border-default); background: var(--color-surface-primary); }
.composer .textarea { min-height: 72px; }
.composer-actions { display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); margin-top: var(--space-sm); }
</style>
